#include "system/telemetry.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_telemetry";
}

namespace hexe::system {

void init_telemetry() {
  ESP_LOGI(kTag, "Telemetry scaffold initialized");
}

}  // namespace hexe::system
