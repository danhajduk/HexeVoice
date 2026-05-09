#include "board/buttons.h"

#include <algorithm>
#include <cstdint>

#include "app_state.h"
#include "board/led_ring.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "system/settings.h"
#include "voice/backend_client.h"
#include "voice/tts_player.h"

namespace {
constexpr char kTag[] = "hexe_buttons_vpe";
constexpr gpio_num_t kCenterButton = GPIO_NUM_0;
constexpr gpio_num_t kHardwareMute = GPIO_NUM_3;
constexpr gpio_num_t kDialA = GPIO_NUM_16;
constexpr gpio_num_t kDialB = GPIO_NUM_18;
constexpr int64_t kLongPressUs = 1000 * 1000;
constexpr int kVolumeStepPercent = 5;
constexpr int kQuadratureStepsPerDetent = 2;

bool g_last_center_pressed = false;
bool g_last_hardware_mute = false;
bool g_center_rotary_consumed = false;
int64_t g_center_pressed_at_us = 0;
uint8_t g_last_dial_state = 0;
int g_dial_accumulator = 0;

bool center_pressed() {
  return gpio_get_level(kCenterButton) == 0;
}

bool hardware_mute_active() {
  return gpio_get_level(kHardwareMute) != 0;
}

uint8_t dial_state() {
  return static_cast<uint8_t>((gpio_get_level(kDialA) ? 0x2 : 0) | (gpio_get_level(kDialB) ? 0x1 : 0));
}

int8_t dial_transition_delta(uint8_t previous, uint8_t current) {
  static constexpr int8_t kTransitionTable[16] = {
      0, -1, 1, 0,
      1, 0, 0, -1,
      -1, 0, 0, 1,
      0, 1, -1, 0,
  };
  return kTransitionTable[((previous & 0x3) << 2) | (current & 0x3)];
}

void handle_dial_step(int direction, bool center_is_pressed) {
  if (direction == 0) {
    return;
  }

  if (center_is_pressed) {
    g_center_rotary_consumed = true;
    hexe::board::led_ring_adjust_accent_hue(direction);
    ESP_LOGI(kTag, "Center-held rotary changed LED accent direction=%d", direction);
    return;
  }

  auto &state = hexe::state();
  const int new_volume = std::clamp(
      state.output_volume_percent + (direction * kVolumeStepPercent),
      0,
      100);
  if (new_volume == state.output_volume_percent) {
    hexe::board::led_ring_show_volume(new_volume);
    return;
  }
  hexe::voice::set_output_volume(new_volume);
  hexe::board::led_ring_show_volume(new_volume);
  ESP_LOGI(kTag, "Rotary volume set to %d%%", new_volume);
}

void update_dial(bool center_is_pressed) {
  const uint8_t current = dial_state();
  if (current == g_last_dial_state) {
    return;
  }

  g_dial_accumulator += dial_transition_delta(g_last_dial_state, current);
  g_last_dial_state = current;
  if (g_dial_accumulator >= kQuadratureStepsPerDetent) {
    g_dial_accumulator = 0;
    handle_dial_step(1, center_is_pressed);
  } else if (g_dial_accumulator <= -kQuadratureStepsPerDetent) {
    g_dial_accumulator = 0;
    handle_dial_step(-1, center_is_pressed);
  }
}

void apply_hardware_mute(bool muted) {
  auto &state = hexe::state();
  hexe::system::set_muted(muted);
  if (state.muted) {
    hexe::voice::cancel_active_session("hardware_mute_switch");
  }
  state.phase = state.muted ? hexe::AppPhase::kMuted : hexe::idle_or_connecting_phase();
  ESP_LOGI(kTag, "Hardware mute switch %s", state.muted ? "on" : "off");
}

void handle_center_release(int64_t duration_us) {
  auto &state = hexe::state();
  if (g_center_rotary_consumed) {
    g_center_rotary_consumed = false;
    ESP_LOGI(kTag, "Center button release consumed by rotary color selection");
    return;
  }

  if (duration_us >= kLongPressUs) {
    hexe::voice::cancel_active_session("voice_pe_center_long_press");
    state.phase = state.muted ? hexe::AppPhase::kMuted : hexe::idle_or_connecting_phase();
    ESP_LOGI(kTag, "Center button long press cancelled active session");
    return;
  }

  if (state.phase == hexe::AppPhase::kListening || state.phase == hexe::AppPhase::kThinking ||
      state.phase == hexe::AppPhase::kReplying) {
    hexe::voice::cancel_active_session("voice_pe_center_button");
    state.phase = state.muted ? hexe::AppPhase::kMuted : hexe::idle_or_connecting_phase();
  } else if (!state.muted) {
    if (!hexe::voice::start_voice_session("button")) {
      state.phase = hexe::idle_or_connecting_phase();
    }
  }
  ESP_LOGI(kTag, "Center button press handled");
}
}  // namespace

namespace hexe::board {

void init_buttons() {
  gpio_config_t input_config = {};
  input_config.pin_bit_mask = (1ULL << kCenterButton) | (1ULL << kHardwareMute) | (1ULL << kDialA) | (1ULL << kDialB);
  input_config.mode = GPIO_MODE_INPUT;
  input_config.pull_up_en = GPIO_PULLUP_ENABLE;
  input_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
  input_config.intr_type = GPIO_INTR_DISABLE;
  gpio_config(&input_config);

  g_last_center_pressed = center_pressed();
  g_last_hardware_mute = hardware_mute_active();
  g_last_dial_state = dial_state();
  apply_hardware_mute(g_last_hardware_mute);
  ESP_LOGI(kTag, "Home Assistant Voice PE controls ready: center=GPIO0 mute=GPIO3 dial=GPIO16/GPIO18");
}

void update_buttons() {
  const bool muted = hardware_mute_active();
  if (muted != g_last_hardware_mute) {
    g_last_hardware_mute = muted;
    apply_hardware_mute(muted);
  }

  const bool pressed = center_pressed();
  const int64_t now_us = esp_timer_get_time();
  if (pressed && !g_last_center_pressed) {
    g_center_pressed_at_us = now_us;
    g_center_rotary_consumed = false;
  } else if (!pressed && g_last_center_pressed) {
    handle_center_release(g_center_pressed_at_us > 0 ? now_us - g_center_pressed_at_us : 0);
    g_center_pressed_at_us = 0;
  }
  update_dial(pressed);
  g_last_center_pressed = pressed;
}

}  // namespace hexe::board
