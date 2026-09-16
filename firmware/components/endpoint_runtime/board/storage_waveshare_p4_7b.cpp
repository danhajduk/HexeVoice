#include "board/storage.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include "bsp/esp32_p4_wifi6_touch_lcd_7b.h"
#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

namespace {
constexpr char kTag[] = "hexe_storage_p4";
constexpr char kMountPath[] = BSP_SD_MOUNT_POINT;
constexpr char kPicturesPath[] = BSP_SD_MOUNT_POINT "/hexe/pictures";
constexpr char kSpritesPath[] = BSP_SD_MOUNT_POINT "/hexe/sprites";
constexpr char kSoundsPath[] = BSP_SD_MOUNT_POINT "/hexe/sounds";
constexpr char kFontsPath[] = BSP_SD_MOUNT_POINT "/hexe/fonts";
constexpr char kModelSetsPath[] = BSP_SD_MOUNT_POINT "/hexe/model_sets";
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
  return ensure_directory(BSP_SD_MOUNT_POINT "/hexe") && ensure_directory(kPicturesPath) &&
         ensure_directory(kSpritesPath) && ensure_directory(kSoundsPath) && ensure_directory(kFontsPath) &&
         ensure_directory(kModelSetsPath);
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
    } else if (S_ISDIR(info.st_mode)) {
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

  int entry_count = 0;
  int logged_count = 0;
  while (dirent *entry = readdir(directory)) {
    if (std::strcmp(entry->d_name, ".") == 0 || std::strcmp(entry->d_name, "..") == 0) {
      continue;
    }
    ++entry_count;
    if (logged_count++ < kMaxLoggedDirectoryEntries) {
      ESP_LOGI(kTag, "SD entry %s/%s", path, entry->d_name);
    }
  }
  closedir(directory);
  if (entry_count == 0) {
    ESP_LOGI(kTag, "SD directory %s is empty", path);
  } else if (entry_count > kMaxLoggedDirectoryEntries) {
    ESP_LOGI(kTag, "SD directory %s has %d additional entries", path, entry_count - kMaxLoggedDirectoryEntries);
  }
}

void init_sd_card() {
  const esp_err_t result = bsp_sdcard_mount();
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "microSD unavailable; continuing without removable storage: %s", esp_err_to_name(result));
    return;
  }

  g_sd_card_mounted = true;
  if (bsp_sdcard != nullptr) {
    const uint64_t size_mb =
        (static_cast<uint64_t>(bsp_sdcard->csd.capacity) * bsp_sdcard->csd.sector_size) / (1024 * 1024);
    ESP_LOGI(kTag, "microSD mounted at %s: name=%s size=%llu MB", kMountPath, bsp_sdcard->cid.name,
             static_cast<unsigned long long>(size_mb));
  } else {
    ESP_LOGI(kTag, "microSD mounted at %s", kMountPath);
  }

  if (!ensure_sd_media_directories_internal()) {
    ESP_LOGW(kTag, "microSD mounted but Hexe media directories are not ready");
  }
  log_sd_directory(kMountPath);
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
  return g_sd_card_mounted && ensure_sd_media_directories_internal();
}

bool reformat_sd_media() {
  if (!g_sd_card_mounted || !ensure_sd_media_directories_internal()) {
    return false;
  }
  const bool pictures_removed = remove_tree_contents(kPicturesPath);
  const bool sprites_removed = remove_tree_contents(kSpritesPath);
  const bool sounds_removed = remove_tree_contents(kSoundsPath);
  const bool fonts_removed = remove_tree_contents(kFontsPath);
  return pictures_removed && sprites_removed && sounds_removed && fonts_removed && ensure_sd_media_directories_internal();
}

const char *sd_card_mount_path() { return kMountPath; }
const char *sd_card_pictures_path() { return kPicturesPath; }
const char *sd_card_sprites_path() { return kSpritesPath; }
const char *sd_card_sounds_path() { return kSoundsPath; }
const char *sd_card_fonts_path() { return kFontsPath; }
const char *sd_card_model_sets_path() { return kModelSetsPath; }

}  // namespace hexe::board
