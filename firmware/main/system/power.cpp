#include "system/power.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_power";
}

namespace hexe::system {

void init_power() {
  ESP_LOGI(kTag, "Power management uses board defaults; low-power and shutdown commands are disabled");
}

const char *power_runtime_mode() {
  return "board_defaults";
}

bool power_low_power_mode_available() {
  return false;
}

bool power_shutdown_command_available() {
  return false;
}

}  // namespace hexe::system
