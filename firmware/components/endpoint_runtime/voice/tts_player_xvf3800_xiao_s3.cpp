#include "voice/tts_player.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>

#include "app_state.h"
#include "board/audio.h"
#include "board/xvf3800_audio_bus.h"
#include "esp_err.h"
#include "esp_log.h"
#include "system/settings.h"
#include "voice/backend_client.h"

namespace {
constexpr char kTag[] = "hexe_tts_xvf";
constexpr int kSampleRate = 16000;
constexpr int kWakeDingDurationMs = 120;
constexpr int kWakeDingFrequencyHz = 880;
constexpr int32_t kWakeDingAmplitude = 5200;
constexpr size_t kToneFrameCount = 160;
constexpr double kPi = 3.14159265358979323846;
bool g_tone_active = false;

int current_output_volume() {
  return std::clamp(hexe::state().output_volume_percent, 0, 100);
}

void set_playback_lifecycle(hexe::PlaybackLifecycleState playback_state, bool active) {
  g_tone_active = active;
  auto &state = hexe::state();
  state.tts_playback_state = playback_state;
  state.tts_playback_active = active;
}

void play_tone(int frequency_hz, int duration_ms, int32_t amplitude) {
  if (!hexe::board::xvf3800_audio_tx_ready()) {
    ESP_LOGW(kTag, "Cannot play tone; XVF3800 I2S TX is unavailable");
    return;
  }
  const bool microphone_paused = hexe::board::pause_microphone_for_playback();
  set_playback_lifecycle(hexe::PlaybackLifecycleState::kStarted, true);

  std::array<int32_t, kToneFrameCount * 2> frames = {};
  const int total_frames = (kSampleRate * duration_ms) / 1000;
  int frame_index = 0;
  const int32_t scaled_amplitude = (amplitude * current_output_volume()) / 100;
  while (frame_index < total_frames) {
    const int frames_this_chunk = std::min<int>(kToneFrameCount, total_frames - frame_index);
    for (int index = 0; index < frames_this_chunk; ++index) {
      const double phase = (2.0 * kPi * frequency_hz * (frame_index + index)) / kSampleRate;
      const int32_t sample = static_cast<int32_t>(std::sin(phase) * scaled_amplitude) << 16;
      frames[index * 2] = sample;
      frames[(index * 2) + 1] = sample;
    }
    size_t bytes_written = 0;
    const esp_err_t result = hexe::board::xvf3800_audio_tx_write(
        frames.data(),
        static_cast<size_t>(frames_this_chunk) * 2 * sizeof(int32_t),
        &bytes_written,
        1000);
    if (result != ESP_OK) {
      ESP_LOGW(kTag, "Tone I2S write failed: %s", esp_err_to_name(result));
      break;
    }
    frame_index += frames_this_chunk;
  }
  hexe::board::xvf3800_audio_tx_stop();
  if (microphone_paused) {
    hexe::board::resume_microphone_after_playback();
  }
  set_playback_lifecycle(hexe::PlaybackLifecycleState::kFinished, false);
}
}  // namespace

namespace hexe::voice {

void init_tts_player() {
  if (!hexe::board::xvf3800_audio_bus_init()) {
    ESP_LOGE(kTag, "XVF3800 I2S output unavailable");
    return;
  }
  ESP_LOGI(kTag, "XVF3800 tone output ready on shared I2S bus");
}

void prewarm_tts_output() {
}

void handle_tts_ready(
    const char *stream_id,
    const char *content_type,
    const char *audio_url,
    bool loop,
    bool keep_microphone_open) {
  (void)content_type;
  (void)loop;
  (void)keep_microphone_open;
  ESP_LOGW(kTag, "XVF3800 profile has tone output only; streamed TTS playback is not enabled yet");
  send_tts_playback_event("tts.playback.failed", stream_id, audio_url, "speaker_streaming_not_enabled", 0);
  set_playback_lifecycle(hexe::PlaybackLifecycleState::kFailed, false);
  if (!hexe::state().muted) {
    hexe::state().phase = hexe::idle_or_connecting_phase();
  }
}

void play_wake_accepted_sound() {
  play_tone(kWakeDingFrequencyHz, kWakeDingDurationMs, kWakeDingAmplitude);
}

void play_sd_sound(const char *filename) {
  ESP_LOGW(kTag, "Ignoring SD sound %s because this profile has no SD card", filename == nullptr ? "none" : filename);
}

void stop_playback(const char *reason) {
  (void)reason;
  hexe::board::xvf3800_audio_tx_stop();
  if (g_tone_active) {
    hexe::board::resume_microphone_after_playback();
  }
  set_playback_lifecycle(hexe::PlaybackLifecycleState::kStopped, false);
  if (!hexe::state().muted) {
    hexe::state().phase = hexe::idle_or_connecting_phase();
  }
}

void stop_tts_playback() {
  stop_playback("tts_stop");
}

void set_output_volume(int volume_percent) {
  hexe::system::set_output_volume_percent(std::clamp(volume_percent, 0, 100));
}

bool tts_playback_active() {
  return g_tone_active;
}

}  // namespace hexe::voice
