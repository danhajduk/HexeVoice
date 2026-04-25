#include "voice/tts_player.h"

#include "app_state.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_tts";
}

namespace hexe::voice {

void init_tts_player() {
  ESP_LOGI(kTag, "TTS player scaffold initialized");
}

void handle_tts_ready(const char *stream_id, const char *content_type, const char *audio_url) {
  auto &state = hexe::state();
  if (state.muted) {
    ESP_LOGI(kTag, "Ignoring TTS while muted");
    return;
  }

  state.phase = hexe::AppPhase::kReplying;
  ESP_LOGI(
      kTag,
      "TTS ready stream=%s content_type=%s url=%s",
      stream_id == nullptr ? "none" : stream_id,
      content_type == nullptr ? "unknown" : content_type,
      audio_url == nullptr ? "none" : audio_url);
}

void stop_tts_playback() {
  ESP_LOGI(kTag, "Stopping TTS playback scaffold");
  auto &state = hexe::state();
  if (!state.muted) {
    state.phase = hexe::AppPhase::kIdle;
  }
}

}  // namespace hexe::voice
