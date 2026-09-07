#include "board/touch.h"

#include <algorithm>
#include <cstdlib>

#include "app_state.h"
#include "board/display.h"
#include "board/pins.h"
#include "board/waveshare_s3_1_85c_bus.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "system/settings.h"
#include "voice/backend_client.h"
#include "voice/tts_player.h"

namespace {
constexpr char kTag[] = "hexe_touch_ws185";
constexpr int kVolumeStepPercent = 10;
constexpr int kI2cTimeoutMs = 1000;
constexpr int64_t kTapDebounceUs = 250 * 1000;
constexpr int64_t kReadErrorLogIntervalUs = 5 * 1000 * 1000;
constexpr int kMaxTapMovementPx = 30;
constexpr int kSwipeThresholdPx = 80;
constexpr uint8_t kTouchRegisterDataStart = 0x02;
constexpr uint8_t kTouchRegisterChipId = 0xA7;
constexpr uint8_t kTouchRegisterAutoSleep = 0xFE;

i2c_master_dev_handle_t g_touch = nullptr;
bool g_touch_ready = false;
bool g_touch_pressed = false;
int64_t g_last_tap_us = 0;
int64_t g_last_read_error_log_us = 0;
int g_touch_start_x = 0;
int g_touch_start_y = 0;
int g_touch_last_x = 0;
int g_touch_last_y = 0;

constexpr gpio_num_t gpio_pin(int pin) {
  return static_cast<gpio_num_t>(pin);
}

enum class TouchAction {
  kNone,
  kWake,
  kVolumeDown,
  kVolumeUp,
  kToggleMute,
};

struct TouchPoint {
  bool pressed{false};
  int x{0};
  int y{0};
  uint8_t gesture{0};
};

TouchAction action_for_point(int x, int y) {
  const auto &app_state = hexe::state();
  if (app_state.phase == hexe::AppPhase::kIdle && !app_state.muted) {
    return TouchAction::kWake;
  }

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
    case TouchAction::kWake:
      if (hexe::voice::post_tts_input_cooldown_active()) {
        ESP_LOGI(kTag, "Touch wake ignored during input cooldown");
        return;
      }
      if (!hexe::voice::start_voice_session("touch")) {
        app_state.phase = hexe::idle_or_connecting_phase();
        ESP_LOGW(kTag, "Touch wake failed to start voice session reason=%s", hexe::voice::voice_session_start_unavailable_reason());
        return;
      }
      ESP_LOGI(kTag, "Touch wake started voice session");
      return;
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
        hexe::voice::stop_playback("touch_mute");
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

void handle_touch_release(int x, int y) {
  const int delta_x = x - g_touch_start_x;
  const int delta_y = y - g_touch_start_y;
  const int abs_x = std::abs(delta_x);
  const int abs_y = std::abs(delta_y);
  if (abs_x >= kSwipeThresholdPx && abs_x > abs_y * 2) {
    const bool moved = delta_x < 0 ? hexe::board::show_next_ui_page() : hexe::board::show_previous_ui_page();
    if (moved) {
      ESP_LOGI(kTag, "Touch swipe page navigation: dx=%d dy=%d", delta_x, delta_y);
    }
    return;
  }

  const int64_t now_us = esp_timer_get_time();
  if (abs_x <= kMaxTapMovementPx && abs_y <= kMaxTapMovementPx && now_us - g_last_tap_us >= kTapDebounceUs) {
    g_last_tap_us = now_us;
    apply_touch_action(action_for_point(g_touch_start_x, g_touch_start_y));
  }
}

esp_err_t touch_read(uint8_t reg, uint8_t *data, size_t data_size) {
  return i2c_master_transmit_receive(g_touch, &reg, sizeof(reg), data, data_size, kI2cTimeoutMs);
}

esp_err_t touch_write(uint8_t reg, uint8_t value) {
  const uint8_t data[] = {reg, value};
  return i2c_master_transmit(g_touch, data, sizeof(data), kI2cTimeoutMs);
}

bool read_touch_point(TouchPoint *point) {
  if (point == nullptr) {
    return false;
  }
  if (!g_touch_pressed && gpio_get_level(gpio_pin(hexe::board::pins::kWs185TouchInterrupt)) != 0) {
    point->pressed = false;
    return true;
  }

  uint8_t data[5] = {};
  const esp_err_t result = touch_read(kTouchRegisterDataStart, data, sizeof(data));
  if (result != ESP_OK) {
    const int64_t now_us = esp_timer_get_time();
    if (now_us - g_last_read_error_log_us >= kReadErrorLogIntervalUs) {
      g_last_read_error_log_us = now_us;
      ESP_LOGW(kTag, "Touch read failed: %s", esp_err_to_name(result));
    }
    return false;
  }

  const uint8_t finger_count = data[0] & 0x0F;
  point->pressed = finger_count > 0;
  point->gesture = 0;
  if (!point->pressed) {
    return true;
  }

  point->x = ((data[1] & 0x0F) << 8) | data[2];
  point->y = ((data[3] & 0x0F) << 8) | data[4];
  return true;
}

TouchPoint rotate_touch_point(const TouchPoint &point) {
  constexpr int rotation = hexe::board::display_config::kRotationDeg;
  static_assert(rotation == 0 || rotation == 90 || rotation == 180 || rotation == 270, "unsupported display rotation");
  if (!point.pressed || rotation == 0) {
    return point;
  }

  TouchPoint rotated = point;
  const int max_x = std::max(0, hexe::board::display_width() - 1);
  const int max_y = std::max(0, hexe::board::display_height() - 1);
  switch (rotation) {
    case 90:
      rotated.x = point.y;
      rotated.y = max_x - point.x;
      break;
    case 180:
      rotated.x = max_x - point.x;
      rotated.y = max_y - point.y;
      break;
    case 270:
      rotated.x = max_y - point.y;
      rotated.y = point.x;
      break;
  }
  return rotated;
}

bool init_touch_device() {
  if (!hexe::board::waveshare_185_init_i2c() || hexe::board::waveshare_185_i2c_bus() == nullptr) {
    return false;
  }

  gpio_config_t interrupt_config = {};
  interrupt_config.pin_bit_mask = 1ULL << hexe::board::pins::kWs185TouchInterrupt;
  interrupt_config.mode = GPIO_MODE_INPUT;
  interrupt_config.pull_up_en = GPIO_PULLUP_ENABLE;
  interrupt_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
  gpio_config(&interrupt_config);

  hexe::board::waveshare_185_reset_touch();

  i2c_device_config_t device_config = {};
  device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  device_config.device_address = hexe::board::pins::kWs185TouchAddress;
  device_config.scl_speed_hz = hexe::board::pins::kWs185I2cClockHz;

  esp_err_t result = i2c_master_bus_add_device(hexe::board::waveshare_185_i2c_bus(), &device_config, &g_touch);
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "Failed to add CST816S I2C device: %s", esp_err_to_name(result));
    g_touch = nullptr;
    return false;
  }

  uint8_t chip_id = 0;
  result = touch_read(kTouchRegisterChipId, &chip_id, sizeof(chip_id));
  if (result != ESP_OK) {
    ESP_LOGW(
        kTag,
        "CST816S probe failed at 0x%02x: %s",
        hexe::board::pins::kWs185TouchAddress,
        esp_err_to_name(result));
    return false;
  }

  ESP_LOGI(
      kTag,
      "CST816S touch initialized: address=0x%02x chip_id=0x%02x",
      hexe::board::pins::kWs185TouchAddress,
      chip_id);
  const esp_err_t autosleep_result = touch_write(kTouchRegisterAutoSleep, 0x01);
  if (autosleep_result != ESP_OK) {
    ESP_LOGW(kTag, "CST816S auto-sleep disable failed: %s", esp_err_to_name(autosleep_result));
  }
  return true;
}
}  // namespace

namespace hexe::board {

void init_touch() {
  if (g_touch_ready) {
    return;
  }
  g_touch_ready = init_touch_device();
  if (!g_touch_ready) {
    ESP_LOGW(kTag, "Touchscreen unavailable");
  }
}

void update_touch() {
  if (!g_touch_ready || g_touch == nullptr) {
    return;
  }

  TouchPoint point = {};
  if (!read_touch_point(&point)) {
    return;
  }
  point = rotate_touch_point(point);

  const bool pressed = point.pressed;
  if (!pressed) {
    if (g_touch_pressed) {
      handle_touch_release(g_touch_last_x, g_touch_last_y);
    }
    g_touch_pressed = false;
    return;
  }

  const int max_x = std::max(0, hexe::board::display_width() - 1);
  const int max_y = std::max(0, hexe::board::display_height() - 1);
  g_touch_last_x = std::clamp(point.x, 0, max_x);
  g_touch_last_y = std::clamp(point.y, 0, max_y);
  if (g_touch_pressed) {
    return;
  }

  g_touch_pressed = true;
  g_touch_start_x = g_touch_last_x;
  g_touch_start_y = g_touch_last_y;
}

bool touch_ready() {
  return g_touch_ready;
}

}  // namespace hexe::board
