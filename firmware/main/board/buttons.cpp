#include "board/buttons.h"

#include <cstdint>

#include "app_state.h"
#include "bsp/esp-box-3.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "iot_button.h"
#include "voice/backend_client.h"

namespace {
constexpr char kTag[] = "hexe_buttons";
button_handle_t g_buttons[BSP_BUTTON_NUM] = {};
int64_t g_last_press_start_us[BSP_BUTTON_NUM] = {};
uint32_t g_last_press_duration_ms[BSP_BUTTON_NUM] = {};
bool g_pending_single_click[BSP_BUTTON_NUM] = {};
int64_t g_single_click_deadline_us[BSP_BUTTON_NUM] = {};
constexpr uint32_t kMaxTapDurationMs = 700;
constexpr uint32_t kDoubleClickGapMs = 450;

const char *button_name(int index) {
  switch (index) {
    case BSP_BUTTON_CONFIG:
      return "config";
    case BSP_BUTTON_MUTE:
      return "mute";
    case BSP_BUTTON_MAIN:
      return "main";
    default:
      return "unknown";
  }
}

void handle_press_down(void *button_handle, void *usr_data) {
  const int index = static_cast<int>(reinterpret_cast<intptr_t>(usr_data));
  (void)button_handle;
  g_last_press_start_us[index] = esp_timer_get_time();
  ESP_LOGI(kTag, "Button %s: press down", button_name(index));
}

void handle_press_up(void *button_handle, void *usr_data) {
  const int index = static_cast<int>(reinterpret_cast<intptr_t>(usr_data));
  (void)button_handle;
  const int64_t now_us = esp_timer_get_time();
  if (g_last_press_start_us[index] > 0 && now_us >= g_last_press_start_us[index]) {
    g_last_press_duration_ms[index] = static_cast<uint32_t>((now_us - g_last_press_start_us[index]) / 1000);
  } else {
    g_last_press_duration_ms[index] = 0;
  }
  ESP_LOGI(
      kTag,
      "Button %s: press up (%lu ms)",
      button_name(index),
      static_cast<unsigned long>(g_last_press_duration_ms[index]));

  if (index != BSP_BUTTON_CONFIG) {
    return;
  }

  if (g_last_press_duration_ms[index] > kMaxTapDurationMs) {
    g_pending_single_click[index] = false;
    return;
  }

  if (g_pending_single_click[index] && now_us <= g_single_click_deadline_us[index]) {
    g_pending_single_click[index] = false;
    ESP_LOGI(
        kTag,
        "Button %s: double press (%lu ms)",
        button_name(index),
        static_cast<unsigned long>(g_last_press_duration_ms[index]));

    auto &app_state = hexe::state();
    app_state.muted = false;
    hexe::voice::cancel_active_session("config_double_press");
    app_state.phase = hexe::idle_or_connecting_phase();
    return;
  }

  g_pending_single_click[index] = true;
  g_single_click_deadline_us[index] = now_us + (static_cast<int64_t>(kDoubleClickGapMs) * 1000);
}

void handle_single_click(void *button_handle, void *usr_data) {
  const int index = static_cast<int>(reinterpret_cast<intptr_t>(usr_data));
  (void)button_handle;
  ESP_LOGI(
      kTag,
      "Button %s: single press (%lu ms)",
      button_name(index),
      static_cast<unsigned long>(g_last_press_duration_ms[index]));

  auto &app_state = hexe::state();
  if (index == BSP_BUTTON_MUTE) {
    app_state.muted = !app_state.muted;
    if (app_state.muted) {
      hexe::voice::cancel_active_session("mute_button");
    }
    app_state.phase = app_state.muted ? hexe::AppPhase::kMuted : hexe::idle_or_connecting_phase();
    return;
  }

  if (index == BSP_BUTTON_CONFIG) {
    if (app_state.phase == hexe::AppPhase::kListening || app_state.phase == hexe::AppPhase::kThinking ||
        app_state.phase == hexe::AppPhase::kReplying) {
      hexe::voice::cancel_active_session("config_button");
      app_state.phase = hexe::idle_or_connecting_phase();
    } else {
      app_state.phase = hexe::idle_or_connecting_phase();
    }
  }
}
}

namespace hexe::board {

void init_buttons() {
  int button_count = 0;
  ESP_ERROR_CHECK(bsp_iot_button_create(g_buttons, &button_count, BSP_BUTTON_NUM));

  if (button_count <= BSP_BUTTON_MUTE || g_buttons[BSP_BUTTON_MUTE] == nullptr) {
    ESP_LOGE(kTag, "Mute button handle not available");
    return;
  }

  void *mute_button_index = reinterpret_cast<void *>(static_cast<intptr_t>(BSP_BUTTON_MUTE));
  ESP_ERROR_CHECK(iot_button_register_cb(
      g_buttons[BSP_BUTTON_MUTE], BUTTON_SINGLE_CLICK, nullptr, handle_single_click, mute_button_index));

  if (button_count <= BSP_BUTTON_CONFIG || g_buttons[BSP_BUTTON_CONFIG] == nullptr) {
    ESP_LOGE(kTag, "Config button handle not available");
    return;
  }

  void *config_button_index = reinterpret_cast<void *>(static_cast<intptr_t>(BSP_BUTTON_CONFIG));
  ESP_ERROR_CHECK(iot_button_register_cb(
      g_buttons[BSP_BUTTON_CONFIG], BUTTON_PRESS_DOWN, nullptr, handle_press_down, config_button_index));
  ESP_ERROR_CHECK(iot_button_register_cb(
      g_buttons[BSP_BUTTON_CONFIG], BUTTON_PRESS_UP, nullptr, handle_press_up, config_button_index));

  ESP_LOGI(kTag, "Buttons ready: mute=official single click, config=raw single/double timing");
}

void update_buttons() {
  const int64_t now_us = esp_timer_get_time();
  for (int index = 0; index < BSP_BUTTON_NUM; ++index) {
    if (!g_pending_single_click[index] || now_us < g_single_click_deadline_us[index]) {
      continue;
    }

    g_pending_single_click[index] = false;
    handle_single_click(nullptr, reinterpret_cast<void *>(static_cast<intptr_t>(index)));
  }
}

}  // namespace hexe::board
