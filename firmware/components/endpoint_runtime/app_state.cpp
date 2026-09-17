#include "app_state.h"

#include <cstdio>
#include <cstring>

#include "freertos/FreeRTOS.h"

namespace hexe {

namespace {
portMUX_TYPE g_ui_flags_lock = portMUX_INITIALIZER_UNLOCKED;
}

AppState &state() {
  static AppState app_state;
  return app_state;
}

void clear_ui_flags() {
  portENTER_CRITICAL(&g_ui_flags_lock);
  for (auto &flag : state().ui_flags) flag = {};
  portEXIT_CRITICAL(&g_ui_flags_lock);
}

bool set_ui_flag(const char *name, bool value) {
  if (name == nullptr || name[0] == '\0' || std::strlen(name) >= kMaxUiFlagNameBytes) return false;
  portENTER_CRITICAL(&g_ui_flags_lock);
  UiFlag *available = nullptr;
  for (auto &flag : state().ui_flags) {
    if (std::strcmp(flag.name, name) == 0) {
      flag.value = value;
      portEXIT_CRITICAL(&g_ui_flags_lock);
      return true;
    }
    if (available == nullptr && flag.name[0] == '\0') available = &flag;
  }
  if (available != nullptr) {
    std::snprintf(available->name, sizeof(available->name), "%s", name);
    available->value = value;
  }
  portEXIT_CRITICAL(&g_ui_flags_lock);
  return available != nullptr;
}

bool ui_flag_value(const char *name) {
  if (name == nullptr) return false;
  bool value = false;
  portENTER_CRITICAL(&g_ui_flags_lock);
  for (const auto &flag : state().ui_flags) {
    if (std::strcmp(flag.name, name) == 0) {
      value = flag.value;
      break;
    }
  }
  portEXIT_CRITICAL(&g_ui_flags_lock);
  return value;
}

uint32_t ui_flags_signature() {
  uint32_t signature = 0;
  portENTER_CRITICAL(&g_ui_flags_lock);
  for (const auto &flag : state().ui_flags) {
    for (const char *cursor = flag.name; *cursor != '\0'; ++cursor) signature = signature * 33U + *cursor;
    signature = signature * 33U + (flag.value ? 1U : 0U);
  }
  portEXIT_CRITICAL(&g_ui_flags_lock);
  return signature;
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
