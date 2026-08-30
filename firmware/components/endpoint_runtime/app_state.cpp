#include "app_state.h"

namespace hexe {

AppState &state() {
  static AppState app_state;
  return app_state;
}

void advance_loading_frame() {
  auto &app_state = state();
  app_state.loading_frame = (app_state.loading_frame + 1) % 120;
}

bool endpoint_ready() {
  const auto &app_state = state();
  return app_state.wifi_connected && app_state.backend_connected && app_state.voice_ws_connected && !app_state.ota_active;
}

AppPhase idle_or_connecting_phase() {
  const auto &app_state = state();
  if (!app_state.wifi_connected) {
    return AppPhase::kWiFiConnecting;
  }
  if (!app_state.backend_connected || !app_state.voice_ws_connected) {
    return AppPhase::kBackendConnecting;
  }
  return AppPhase::kIdle;
}

}  // namespace hexe
