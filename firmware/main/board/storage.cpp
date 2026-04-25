#include "board/storage.h"

#include "esp_log.h"
#include "nvs_flash.h"

namespace {
constexpr char kTag[] = "hexe_storage";
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
}

}  // namespace hexe::board
