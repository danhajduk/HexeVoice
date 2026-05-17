#include "board/touch.h"

#include <algorithm>

#include "app_state.h"
#include "board/display.h"
#include "bsp/touch.h"
#include "esp_err.h"
#include "esp_lcd_touch.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "system/settings.h"
#include "voice/backend_client.h"
#include "voice/tts_player.h"

namespace {
constexpr char kTag[] = "hexe_touch";
constexpr int kVolumeStepPercent = 10;
constexpr int64_t kTapDebounceUs = 250 * 1000;
constexpr int64_t kReadErrorLogIntervalUs = 5 * 1000 * 1000;

esp_lcd_touch_handle_t g_touch = nullptr;
bool g_touch_ready = false;
bool g_touch_pressed = false;
int64_t g_last_tap_us = 0;
int64_t g_last_read_error_log_us = 0;

enum class TouchAction {
  kNone,
  kVolumeDown,
  kVolumeUp,
  kToggleMute,
};

TouchAction action_for_point(int x, int y) {
  const int width = std::max(1, hexe::board::display_width());
  const int height = std::max(1, hexe::board::display_height());

  if (y <= 56 && x >= width - 104) {
    return TouchAction::kToggleMute;
  }

  if (y < height - 80) {
    return TouchAction::kNone;
  }

  if (x < width / 3) {
    return TouchAction::kVolumeDown;
  }
  if (x >= (width * 2) / 3) {
    return TouchAction::kVolumeUp;
  }
  return TouchAction::kToggleMute;
}

void apply_touch_action(TouchAction action) {
  auto &app_state = hexe::state();
  switch (action) {
    case TouchAction::kVolumeDown: {
      const int volume = std::clamp(app_state.output_volume_percent - kVolumeStepPercent, 0, 100);
      hexe::voice::set_output_volume(volume);
      ESP_LOGI(kTag, "Touch volume down: %d%%", volume);
      return;
    }
    case TouchAction::kVolumeUp: {
      const int volume = std::clamp(app_state.output_volume_percent + kVolumeStepPercent, 0, 100);
      hexe::voice::set_output_volume(volume);
      ESP_LOGI(kTag, "Touch volume up: %d%%", volume);
      return;
    }
    case TouchAction::kToggleMute: {
      hexe::system::set_muted(!app_state.muted);
      if (app_state.muted) {
        hexe::voice::stop_tts_playback();
        hexe::voice::cancel_active_session("touch_mute");
      }
      app_state.phase = app_state.muted ? hexe::AppPhase::kMuted : hexe::idle_or_connecting_phase();
      ESP_LOGI(kTag, "Touch mute toggle: %s", app_state.muted ? "muted" : "unmuted");
      return;
    }
    case TouchAction::kNone:
      return;
  }
}
}

namespace hexe::board {

void init_touch() {
  if (g_touch_ready) {
    return;
  }

  const esp_err_t result = bsp_touch_new(nullptr, &g_touch);
  if (result != ESP_OK || g_touch == nullptr) {
    ESP_LOGW(kTag, "Touchscreen unavailable: %s", esp_err_to_name(result));
    return;
  }

  g_touch_ready = true;
  ESP_LOGI(kTag, "Touchscreen initialized");
}

bool touch_ready() {
  return g_touch_ready;
}

void update_touch() {
  if (!g_touch_ready || g_touch == nullptr) {
    return;
  }

  const esp_err_t read_result = esp_lcd_touch_read_data(g_touch);
  if (read_result != ESP_OK) {
    const int64_t now_us = esp_timer_get_time();
    if (now_us - g_last_read_error_log_us >= kReadErrorLogIntervalUs) {
      g_last_read_error_log_us = now_us;
      ESP_LOGW(kTag, "Touch read failed: %s", esp_err_to_name(read_result));
    }
    return;
  }

  esp_lcd_touch_point_data_t point = {};
  uint8_t point_count = 0;
  const esp_err_t data_result = esp_lcd_touch_get_data(g_touch, &point, &point_count, 1);
  if (data_result != ESP_OK) {
    return;
  }

  const bool pressed = point_count > 0;
  if (!pressed) {
    g_touch_pressed = false;
    return;
  }

  const int64_t now_us = esp_timer_get_time();
  if (g_touch_pressed || now_us - g_last_tap_us < kTapDebounceUs) {
    return;
  }

  g_touch_pressed = true;
  g_last_tap_us = now_us;

  const int max_x = std::max(0, hexe::board::display_width() - 1);
  const int max_y = std::max(0, hexe::board::display_height() - 1);
  const int x = std::clamp(static_cast<int>(point.x), 0, max_x);
  const int y = std::clamp(static_cast<int>(point.y), 0, max_y);
  apply_touch_action(action_for_point(x, y));
}

}  // namespace hexe::board
