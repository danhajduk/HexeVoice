#include "system/telemetry.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_telemetry";
}

namespace hexe::system {

void init_telemetry() {
  ESP_LOGI(kTag, "Dedicated telemetry channel disabled; heartbeat capabilities carry endpoint telemetry");
}

const char *telemetry_runtime_mode() {
  return "heartbeat_capabilities";
}

bool telemetry_dedicated_channel_enabled() {
  return false;
}

bool telemetry_heartbeat_owned() {
  return true;
}

}  // namespace hexe::system
