#include "board/buttons.h"

#include <cstdint>

#include "app_state.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "system/settings.h"
#include "voice/backend_client.h"

namespace {
constexpr char kTag[] = "hexe_buttons_vpe";
constexpr gpio_num_t kCenterButton = GPIO_NUM_0;
constexpr gpio_num_t kHardwareMute = GPIO_NUM_3;
constexpr int64_t kLongPressUs = 1000 * 1000;

bool g_last_center_pressed = false;
bool g_last_hardware_mute = false;
int64_t g_center_pressed_at_us = 0;

bool center_pressed() {
  return gpio_get_level(kCenterButton) == 0;
}

bool hardware_mute_active() {
  return gpio_get_level(kHardwareMute) != 0;
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
  input_config.pin_bit_mask = (1ULL << kCenterButton) | (1ULL << kHardwareMute);
  input_config.mode = GPIO_MODE_INPUT;
  input_config.pull_up_en = GPIO_PULLUP_ENABLE;
  input_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
  input_config.intr_type = GPIO_INTR_DISABLE;
  gpio_config(&input_config);

  g_last_center_pressed = center_pressed();
  g_last_hardware_mute = hardware_mute_active();
  apply_hardware_mute(g_last_hardware_mute);
  ESP_LOGI(kTag, "Home Assistant Voice PE controls ready: center=GPIO0 mute=GPIO3");
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
  } else if (!pressed && g_last_center_pressed) {
    handle_center_release(g_center_pressed_at_us > 0 ? now_us - g_center_pressed_at_us : 0);
    g_center_pressed_at_us = 0;
  }
  g_last_center_pressed = pressed;
}

}  // namespace hexe::board
