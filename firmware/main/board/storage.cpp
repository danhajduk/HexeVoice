#include "board/storage.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <sys/stat.h>
#include <sys/types.h>

#include "bsp/esp-box-3.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "sdmmc_cmd.h"

namespace {
constexpr char kTag[] = "hexe_storage";

constexpr char kPicturesPath[] = BSP_SD_MOUNT_POINT "/hexe/pictures";
constexpr char kSoundsPath[] = BSP_SD_MOUNT_POINT "/hexe/sounds";

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

void ensure_sd_media_directories() {
  ensure_directory(BSP_SD_MOUNT_POINT "/hexe");
  ensure_directory(kPicturesPath);
  ensure_directory(kSoundsPath);
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

  ensure_sd_media_directories();
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

const char *sd_card_mount_path() {
  return BSP_SD_MOUNT_POINT;
}

const char *sd_card_pictures_path() {
  return kPicturesPath;
}

const char *sd_card_sounds_path() {
  return kSoundsPath;
}

}  // namespace hexe::board
