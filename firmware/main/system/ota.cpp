#include "system/ota.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_ota";
}

namespace hexe::system {

void init_ota() {
  ESP_LOGI(kTag, "OTA scaffold initialized");
}

}  // namespace hexe::system
