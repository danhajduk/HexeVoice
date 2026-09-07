#include "board/wifi.h"

#include <cstring>

#include "app_state.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_wifi_none";
constexpr char kNoIpAddress[] = "0.0.0.0";
}

namespace hexe::board {

void init_wifi() {
  auto &state = hexe::state();
  state.wifi_connected = false;
  state.backend_connected = false;
  state.voice_ws_connected = false;
  state.wifi_rssi = -100;
  if (!state.muted) {
    state.phase = hexe::AppPhase::kWiFiConnecting;
  }
  ESP_LOGW(kTag, "Hosted Wi-Fi disabled for this board profile until the ESP32-C6 adapter is implemented");
}

void reconnect_wifi() {
  ESP_LOGW(kTag, "Wi-Fi reconnect ignored because hosted Wi-Fi is not implemented yet");
}

void refresh_wifi_status() {
}

const char *current_ip_address() {
  return kNoIpAddress;
}

}  // namespace hexe::board
