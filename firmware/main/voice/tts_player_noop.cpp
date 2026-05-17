#include "voice/tts_player.h"

#include <algorithm>

#include "app_state.h"
#include "esp_log.h"
#include "system/settings.h"
#include "voice/backend_client.h"

namespace {
constexpr char kTag[] = "hexe_tts_none";
}

namespace hexe::voice {

void init_tts_player() {
  ESP_LOGW(kTag, "TTS output disabled for this board profile");
}

void prewarm_tts_output() {
}

void handle_tts_ready(const char *stream_id, const char *content_type, const char *audio_url) {
  ESP_LOGW(
      kTag,
      "Ignoring TTS output stream=%s content_type=%s url=%s because local speaker output is not enabled",
      stream_id == nullptr ? "none" : stream_id,
      content_type == nullptr ? "unknown" : content_type,
      audio_url == nullptr ? "none" : audio_url);
  send_tts_playback_event("tts.playback.failed", stream_id, audio_url, "speaker_disabled", 0);
  auto &state = hexe::state();
  state.tts_playback_active = false;
  state.tts_playback_state = hexe::PlaybackLifecycleState::kFailed;
  if (!state.muted) {
    state.phase = hexe::idle_or_connecting_phase();
  }
}

void play_wake_accepted_sound() {}

void play_sd_sound(const char *filename) {
  ESP_LOGW(kTag, "Ignoring SD sound %s because local speaker output is not enabled", filename == nullptr ? "none" : filename);
}

void stop_tts_playback() {
  auto &state = hexe::state();
  state.tts_playback_active = false;
  state.tts_playback_state = hexe::PlaybackLifecycleState::kStopped;
  if (!state.muted) {
    state.phase = hexe::idle_or_connecting_phase();
  }
}

void set_output_volume(int volume_percent) {
  hexe::system::set_output_volume_percent(std::clamp(volume_percent, 0, 100));
}

bool tts_playback_active() {
  return false;
}

}  // namespace hexe::voice
