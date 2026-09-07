#include "board/audio.h"

#include "app_state.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_audio_none";
}

namespace hexe::board {

void init_audio() {
  auto &state = hexe::state();
  state.audio_streaming = false;
  state.mic_paused_for_playback = false;
  ESP_LOGW(kTag, "Audio disabled for this board profile until the codec adapter is implemented");
}

void update_audio() {
}

bool audio_input_ready() {
  return false;
}

bool audio_output_ready() {
  return false;
}

bool pause_microphone_for_playback() {
  hexe::state().mic_paused_for_playback = false;
  return false;
}

void resume_microphone_after_playback() {
  hexe::state().mic_paused_for_playback = false;
}

}  // namespace hexe::board
