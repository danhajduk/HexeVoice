#include "board/buttons.h"

#include "app_state.h"
#include "board/led_ring.h"
#include "board/xvf3800_control.h"
#include "esp_log.h"
#include "system/settings.h"
#include "voice/backend_client.h"
#include "voice/tts_player.h"

namespace {
constexpr char kTag[] = "hexe_buttons_xvf";
constexpr uint8_t kMuteButtonGpiIndex = 0;
bool g_last_muted = false;

bool read_mute_button_pressed(bool *pressed) {
  uint8_t values[3] = {};
  if (pressed == nullptr || !hexe::board::xvf3800_read_gpi_values(values)) {
    return false;
  }
  *pressed = values[kMuteButtonGpiIndex] == 0;
  return true;
}

void apply_mute(bool muted) {
  auto &state = hexe::state();
  hexe::system::set_muted(muted);
  hexe::board::xvf3800_set_mute(muted);
  if (state.muted) {
    hexe::voice::stop_playback("xvf3800_mute_button");
    hexe::voice::cancel_active_session("xvf3800_mute_button");
  }
  state.phase = state.muted ? hexe::AppPhase::kMuted : hexe::idle_or_connecting_phase();
  ESP_LOGI(kTag, "XVF3800 mute button state %s", state.muted ? "muted" : "unmuted");
}
}  // namespace

namespace hexe::board {

void init_buttons() {
  if (!xvf3800_control_init()) {
    ESP_LOGE(kTag, "XVF3800 controls unavailable; mute button disabled");
    return;
  }
  bool pressed = false;
  if (read_mute_button_pressed(&pressed)) {
    g_last_muted = pressed;
    apply_mute(g_last_muted);
  }
  ESP_LOGI(kTag, "XVF3800 mute button polling ready through GPI X1D09");
}

void update_buttons() {
  bool pressed = false;
  if (!read_mute_button_pressed(&pressed)) {
    return;
  }
  if (pressed == g_last_muted) {
    return;
  }
  g_last_muted = pressed;
  apply_mute(g_last_muted);
}

}  // namespace hexe::board
