#include "board/storage.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include "board/pins.h"
#include "driver/gpio.h"
#include "driver/sdmmc_host.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "nvs_flash.h"
#include "sdmmc_cmd.h"

namespace {
constexpr char kTag[] = "hexe_storage_ws185";
constexpr char kMountPath[] = "/sdcard";
constexpr char kPicturesPath[] = "/sdcard/hexe/pictures";
constexpr char kSpritesPath[] = "/sdcard/hexe/sprites";
constexpr char kSoundsPath[] = "/sdcard/hexe/sounds";
constexpr int kMaxLoggedDirectoryEntries = 64;

bool g_sd_card_mounted = false;
sdmmc_card_t *g_sd_card = nullptr;

constexpr gpio_num_t gpio_pin(int pin) {
  return static_cast<gpio_num_t>(pin);
}

bool ensure_directory(const char *path) {
  if (mkdir(path, 0775) == 0) {
    return true;
  }
  if (errno == EEXIST) {
    struct stat info = {};
    return stat(path, &info) == 0 && S_ISDIR(info.st_mode);
  }

  ESP_LOGW(kTag, "Failed to create directory %s: %s", path, std::strerror(errno));
  return false;
}

bool ensure_sd_media_directories_internal() {
  const bool root_ready = ensure_directory("/sdcard/hexe");
  const bool pictures_ready = ensure_directory(kPicturesPath);
  const bool sprites_ready = ensure_directory(kSpritesPath);
  const bool sounds_ready = ensure_directory(kSoundsPath);
  return root_ready && pictures_ready && sprites_ready && sounds_ready;
}

bool remove_tree_contents(const char *path) {
  DIR *directory = opendir(path);
  if (directory == nullptr) {
    return errno == ENOENT;
  }

  bool ok = true;
  while (dirent *entry = readdir(directory)) {
    if (std::strcmp(entry->d_name, ".") == 0 || std::strcmp(entry->d_name, "..") == 0) {
      continue;
    }

    char entry_path[256] = {};
    const int written = std::snprintf(entry_path, sizeof(entry_path), "%s/%s", path, entry->d_name);
    if (written < 0 || written >= static_cast<int>(sizeof(entry_path))) {
      ESP_LOGW(kTag, "Skipping media entry with long path: %s/%s", path, entry->d_name);
      ok = false;
      continue;
    }

    struct stat info = {};
    if (stat(entry_path, &info) != 0) {
      ESP_LOGW(kTag, "Could not inspect %s: %s", entry_path, std::strerror(errno));
      ok = false;
      continue;
    }

    if (S_ISDIR(info.st_mode)) {
      if (!remove_tree_contents(entry_path) || rmdir(entry_path) != 0) {
        ESP_LOGW(kTag, "Could not remove media directory %s: %s", entry_path, std::strerror(errno));
        ok = false;
      }
    } else if (unlink(entry_path) != 0) {
      ESP_LOGW(kTag, "Could not remove media file %s: %s", entry_path, std::strerror(errno));
      ok = false;
    }
  }

  closedir(directory);
  return ok;
}

void log_sd_directory(const char *path) {
  DIR *directory = opendir(path);
  if (directory == nullptr) {
    ESP_LOGW(kTag, "Unable to open SD directory %s: %s", path, std::strerror(errno));
    return;
  }

  ESP_LOGI(kTag, "SD directory %s:", path);
  int entry_count = 0;
  int logged_count = 0;
  while (dirent *entry = readdir(directory)) {
    if (std::strcmp(entry->d_name, ".") == 0 || std::strcmp(entry->d_name, "..") == 0) {
      continue;
    }

    ++entry_count;
    if (logged_count >= kMaxLoggedDirectoryEntries) {
      continue;
    }

    char entry_path[256] = {};
    const int written = std::snprintf(entry_path, sizeof(entry_path), "%s/%s", path, entry->d_name);
    if (written < 0 || written >= static_cast<int>(sizeof(entry_path))) {
      ESP_LOGW(kTag, "  %s (path too long)", entry->d_name);
      ++logged_count;
      continue;
    }

    struct stat info = {};
    if (stat(entry_path, &info) != 0) {
      ESP_LOGW(kTag, "  %s (stat failed: %s)", entry->d_name, std::strerror(errno));
      ++logged_count;
      continue;
    }

    if (S_ISDIR(info.st_mode)) {
      ESP_LOGI(kTag, "  [dir]  %s", entry->d_name);
    } else {
      ESP_LOGI(kTag, "  [file] %s (%lld bytes)", entry->d_name, static_cast<long long>(info.st_size));
    }
    ++logged_count;
  }

  closedir(directory);

  if (entry_count == 0) {
    ESP_LOGI(kTag, "  (empty)");
  } else if (entry_count > logged_count) {
    ESP_LOGI(kTag, "  ... %d more entries not shown", entry_count - logged_count);
  }
}

void log_sd_media_directories() {
  log_sd_directory(kMountPath);
  log_sd_directory(kPicturesPath);
  log_sd_directory(kSpritesPath);
  log_sd_directory(kSoundsPath);
}

void init_sd_card() {
  esp_vfs_fat_sdmmc_mount_config_t mount_config = {};
  mount_config.format_if_mount_failed = false;
  mount_config.max_files = 8;
  mount_config.allocation_unit_size = 16 * 1024;
  mount_config.disk_status_check_enable = false;
  mount_config.use_one_fat = false;

  sdmmc_host_t host = SDMMC_HOST_DEFAULT();
  sdmmc_slot_config_t slot_config = SDMMC_SLOT_CONFIG_DEFAULT();
  slot_config.clk = gpio_pin(hexe::board::pins::kWs185SdmmcClk);
  slot_config.cmd = gpio_pin(hexe::board::pins::kWs185SdmmcCmd);
  slot_config.d0 = gpio_pin(hexe::board::pins::kWs185SdmmcD0);
  slot_config.d1 = GPIO_NUM_NC;
  slot_config.d2 = GPIO_NUM_NC;
  slot_config.d3 = GPIO_NUM_NC;
  slot_config.d4 = GPIO_NUM_NC;
  slot_config.d5 = GPIO_NUM_NC;
  slot_config.d6 = GPIO_NUM_NC;
  slot_config.d7 = GPIO_NUM_NC;
  slot_config.cd = GPIO_NUM_NC;
  slot_config.wp = GPIO_NUM_NC;
  slot_config.width = 1;
  slot_config.flags = SDMMC_SLOT_FLAG_INTERNAL_PULLUP;

  const esp_err_t result = esp_vfs_fat_sdmmc_mount(kMountPath, &host, &slot_config, &mount_config, &g_sd_card);
  if (result != ESP_OK) {
    ESP_LOGW(
        kTag,
        "SDMMC card not mounted: %s (CLK=%d CMD=%d D0=%d)",
        esp_err_to_name(result),
        hexe::board::pins::kWs185SdmmcClk,
        hexe::board::pins::kWs185SdmmcCmd,
        hexe::board::pins::kWs185SdmmcD0);
    return;
  }

  g_sd_card_mounted = true;
  if (g_sd_card != nullptr) {
    const uint64_t size_mb =
        (static_cast<uint64_t>(g_sd_card->csd.capacity) * g_sd_card->csd.sector_size) / (1024 * 1024);
    ESP_LOGI(
        kTag,
        "SDMMC card mounted at %s: name=%s size=%llu MB",
        kMountPath,
        g_sd_card->cid.name,
        static_cast<unsigned long long>(size_mb));
  } else {
    ESP_LOGI(kTag, "SDMMC card mounted at %s", kMountPath);
  }

  ensure_sd_media_directories_internal();
  log_sd_media_directories();
}
}  // namespace

namespace hexe::board {

void init_storage() {
  esp_err_t err = nvs_flash_init();
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    err = nvs_flash_init();
  }
  ESP_ERROR_CHECK(err);
  ESP_LOGI(kTag, "NVS storage initialized");
  init_sd_card();
}

bool sd_card_mounted() {
  return g_sd_card_mounted;
}

bool ensure_sd_media_directories() {
  if (!g_sd_card_mounted) {
    return false;
  }
  return ensure_sd_media_directories_internal();
}

bool reformat_sd_media() {
  if (!g_sd_card_mounted) {
    return false;
  }
  if (!ensure_sd_media_directories_internal()) {
    return false;
  }

  const bool pictures_removed = remove_tree_contents(kPicturesPath);
  const bool sprites_removed = remove_tree_contents(kSpritesPath);
  const bool sounds_removed = remove_tree_contents(kSoundsPath);
  const bool directories_ready = ensure_sd_media_directories_internal();
  log_sd_media_directories();
  return pictures_removed && sprites_removed && sounds_removed && directories_ready;
}

const char *sd_card_mount_path() {
  return kMountPath;
}

const char *sd_card_pictures_path() {
  return kPicturesPath;
}

const char *sd_card_sprites_path() {
  return kSpritesPath;
}

const char *sd_card_sounds_path() {
  return kSoundsPath;
}

}  // namespace hexe::board
