#include "system/power.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_power";
}

namespace hexe::system {

void init_power() {
  ESP_LOGI(kTag, "Power scaffold initialized");
}

}  // namespace hexe::system
