#include "board/storage.h"

#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

namespace {
constexpr char kTag[] = "hexe_storage_nvs";
constexpr char kNoPath[] = "";
}

namespace hexe::board {

void init_storage() {
  esp_err_t err = nvs_flash_init();
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    err = nvs_flash_init();
  }
  ESP_ERROR_CHECK(err);
  ESP_LOGI(kTag, "NVS storage initialized; SD media storage disabled for this board profile");
}

bool sd_card_mounted() {
  return false;
}

bool ensure_sd_media_directories() {
  return false;
}

bool reformat_sd_media() {
  return false;
}

const char *sd_card_mount_path() {
  return kNoPath;
}

const char *sd_card_pictures_path() {
  return kNoPath;
}

const char *sd_card_sprites_path() {
  return kNoPath;
}

const char *sd_card_sounds_path() {
  return kNoPath;
}

}  // namespace hexe::board
