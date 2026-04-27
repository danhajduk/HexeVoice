#include "board/storage.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "bsp/esp-box-3.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "sdmmc_cmd.h"

namespace {
constexpr char kTag[] = "hexe_storage";

constexpr char kPicturesPath[] = BSP_SD_MOUNT_POINT "/hexe/pictures";
constexpr char kSpritesPath[] = BSP_SD_MOUNT_POINT "/hexe/sprites";
constexpr char kSoundsPath[] = BSP_SD_MOUNT_POINT "/hexe/sounds";
constexpr int kMaxLoggedDirectoryEntries = 64;

bool g_sd_card_mounted = false;

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
  const bool root_ready = ensure_directory(BSP_SD_MOUNT_POINT "/hexe");
  const bool pictures_ready = ensure_directory(kPicturesPath);
  const bool sprites_ready = ensure_directory(kSpritesPath);
  const bool sounds_ready = ensure_directory(kSoundsPath);
  return root_ready && pictures_ready && sprites_ready && sounds_ready;
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
  log_sd_directory(BSP_SD_MOUNT_POINT);
  log_sd_directory(kPicturesPath);
  log_sd_directory(kSpritesPath);
  log_sd_directory(kSoundsPath);
}

void init_sd_card() {
  bsp_sdcard_cfg_t cfg = {};
  const esp_err_t result = bsp_sdcard_sdspi_mount(&cfg);
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "SPI SD card not mounted: %s", esp_err_to_name(result));
    return;
  }

  g_sd_card_mounted = true;
  sdmmc_card_t *card = bsp_sdcard_get_handle();
  if (card != nullptr) {
    const uint64_t size_mb = (static_cast<uint64_t>(card->csd.capacity) * card->csd.sector_size) / (1024 * 1024);
    ESP_LOGI(
        kTag,
        "SPI SD card mounted at %s: name=%s size=%llu MB",
        BSP_SD_MOUNT_POINT,
        card->cid.name,
        static_cast<unsigned long long>(size_mb));
  } else {
    ESP_LOGI(kTag, "SPI SD card mounted at %s", BSP_SD_MOUNT_POINT);
  }

  ensure_sd_media_directories_internal();
  log_sd_media_directories();
}
}

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

const char *sd_card_mount_path() {
  return BSP_SD_MOUNT_POINT;
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
