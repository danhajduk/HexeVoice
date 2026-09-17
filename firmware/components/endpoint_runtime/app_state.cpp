#include "app_state.h"

#include <cstdio>
#include <cstring>

#include "freertos/FreeRTOS.h"
#include "esp_timer.h"

namespace hexe {

namespace {
portMUX_TYPE g_ui_flags_lock = portMUX_INITIALIZER_UNLOCKED;
char g_ui_screen_id[kMaxUiScreenIdBytes] = {};
int64_t g_ui_screen_expires_us = 0;
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

bool trigger_ui_screen(const char *screen_id, int duration_ms) {
  if (screen_id == nullptr || screen_id[0] == '\0' || std::strlen(screen_id) >= kMaxUiScreenIdBytes ||
      duration_ms <= 0 || duration_ms > 30000) return false;
  portENTER_CRITICAL(&g_ui_flags_lock);
  std::snprintf(g_ui_screen_id, sizeof(g_ui_screen_id), "%s", screen_id);
  g_ui_screen_expires_us = esp_timer_get_time() + static_cast<int64_t>(duration_ms) * 1000;
  portEXIT_CRITICAL(&g_ui_flags_lock);
  return true;
}

bool active_ui_screen(char *screen_id, size_t screen_id_size) {
  if (screen_id == nullptr || screen_id_size == 0) return false;
  bool active = false;
  portENTER_CRITICAL(&g_ui_flags_lock);
  if (g_ui_screen_id[0] != '\0' && esp_timer_get_time() < g_ui_screen_expires_us) {
    std::snprintf(screen_id, screen_id_size, "%s", g_ui_screen_id);
    active = true;
  } else {
    g_ui_screen_id[0] = '\0';
    g_ui_screen_expires_us = 0;
  }
  portEXIT_CRITICAL(&g_ui_flags_lock);
  return active;
}

uint32_t ui_screen_signature() {
  char screen_id[kMaxUiScreenIdBytes] = {};
  if (!active_ui_screen(screen_id, sizeof(screen_id))) return 0;
  uint32_t signature = 5381;
  for (const char *cursor = screen_id; *cursor != '\0'; ++cursor) signature = signature * 33U + *cursor;
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
