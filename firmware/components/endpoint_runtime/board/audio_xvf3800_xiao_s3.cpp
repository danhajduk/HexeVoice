#include "board/audio.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

#include "app_state.h"
#include "board/xvf3800_audio_bus.h"
#include "board/xvf3800_control.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "system/settings.h"
#include "voice/backend_client.h"
#include "voice/tts_player.h"
#include "voice/wake_word.h"

namespace {
constexpr char kTag[] = "hexe_audio_xvf";
constexpr int kSampleRate = 16000;
constexpr size_t kFrameSamples = 320;
constexpr uint32_t kFrameDurationMs = static_cast<uint32_t>((kFrameSamples * 1000) / kSampleRate);
constexpr uint32_t kVadStartEnergyThreshold = 900;
constexpr uint32_t kVadContinueEnergyThreshold = 500;
constexpr uint32_t kVadStartNoiseMultiplier = 3;
constexpr uint32_t kVadContinueNoiseMultiplier = 2;
constexpr uint32_t kVadNoiseMargin = 220;
constexpr uint32_t kVadStartVoiceFrames = 2;
constexpr uint32_t kVadReleasePeakPercent = 60;
constexpr uint32_t kVadSilenceHoldMs = 1200;
constexpr uint32_t kVadSilenceHoldFrames = kVadSilenceHoldMs / kFrameDurationMs;
constexpr uint32_t kVadTaskStackBytes = 8192;
constexpr uint32_t kMicReadTimeoutLogEvery = 200;

TaskHandle_t g_vad_task = nullptr;
SemaphoreHandle_t g_mic_mutex = nullptr;
bool g_vad_turn_active = false;
bool g_mic_paused_for_playback = false;
bool g_audio_ready = false;
uint32_t g_mic_read_timeout_count = 0;
std::array<int32_t, kFrameSamples * 2> g_raw_samples = {};
std::array<int16_t, kFrameSamples> g_mono_samples = {};

uint32_t estimate_level(const int16_t *samples, size_t count) {
  uint64_t total = 0;
  for (size_t index = 0; index < count; ++index) {
    const int32_t sample = samples[index];
    total += sample < 0 ? static_cast<uint32_t>(-sample) : static_cast<uint32_t>(sample);
  }
  return count == 0 ? 0 : static_cast<uint32_t>(total / count);
}

uint32_t update_noise_floor(uint32_t current_floor, uint32_t level) {
  if (current_floor == 0) {
    return level;
  }
  return ((current_floor * 15) + level) / 16;
}

uint32_t micro_vad_pause_frames() {
  return std::max<uint32_t>(1, hexe::system::micro_vad_pause_ms() / kFrameDurationMs);
}

uint32_t micro_vad_start_threshold() {
  return static_cast<uint32_t>(hexe::system::micro_vad_energy_threshold());
}

uint32_t micro_vad_continue_threshold(uint32_t start_threshold) {
  return std::max<uint32_t>(1, (start_threshold * kVadContinueEnergyThreshold) / kVadStartEnergyThreshold);
}

hexe::voice::MicroVadFrameState micro_vad_frame_state(
    bool frame_has_voice,
    uint32_t &chunk_index,
    bool &chunk_active,
    uint32_t &silent_frames) {
  hexe::voice::MicroVadFrameState state = {};
  state.chunk_index = chunk_index;

  if (frame_has_voice) {
    state.active = true;
    state.started = !chunk_active;
    chunk_active = true;
    silent_frames = 0;
    return state;
  }

  if (!chunk_active) {
    return state;
  }

  const uint32_t pause_frames = micro_vad_pause_frames();
  if (silent_frames < pause_frames) {
    ++silent_frames;
  }
  state.active = true;
  state.pause_ms = silent_frames * kFrameDurationMs;
  if (silent_frames >= pause_frames) {
    state.ended = true;
    chunk_active = false;
    silent_frames = 0;
    ++chunk_index;
  }
  return state;
}

int16_t processed_voice_channel_sample(int32_t left, int32_t right) {
  (void)right;
  return static_cast<int16_t>(std::clamp<int32_t>(left >> 16, -32768, 32767));
}

void apply_vad_state(bool speaking, uint32_t level) {
  auto &app_state = hexe::state();
  app_state.vad_enabled = true;
  app_state.vad_level = static_cast<int>(level);

  if (app_state.muted || app_state.ota_active) {
    app_state.vad_speaking = false;
    return;
  }

  const bool state_changed = app_state.vad_speaking != speaking;
  app_state.vad_speaking = speaking;
  if (!state_changed) {
    return;
  }

  if (speaking) {
    g_vad_turn_active = true;
    ESP_LOGI(kTag, "VAD speech detected (level=%lu)", static_cast<unsigned long>(level));
    hexe::voice::notify_vad_speech_started(level);
  } else if (g_vad_turn_active) {
    g_vad_turn_active = false;
    if (hexe::voice::finish_audio_stream("vad_silence")) {
      if (app_state.phase == hexe::AppPhase::kListening) {
        app_state.phase = hexe::AppPhase::kThinking;
      }
    } else if (app_state.phase == hexe::AppPhase::kListening) {
      app_state.phase = hexe::idle_or_connecting_phase();
    }
    ESP_LOGI(kTag, "VAD silence detected (level=%lu)", static_cast<unsigned long>(level));
  }
}

void process_local_keywords(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor,
    uint32_t speech_peak_level,
    bool frame_has_voice,
    const hexe::voice::MicroVadFrameState &micro_vad) {
  const hexe::voice::LocalKeywordFrameDetections local_keywords = hexe::voice::inspect_local_keyword_frame(
      samples,
      sample_count,
      level,
      noise_floor,
      speech_peak_level,
      frame_has_voice);
  const hexe::voice::LocalKeywordDetection &wake_detection = local_keywords.wake;
  if (wake_detection.detected) {
    ESP_LOGI(
        kTag,
        "Local wake detected: model=%s confidence=%.3f level=%lu noise=%lu peak=%lu",
        wake_detection.model == nullptr ? "unknown" : wake_detection.model,
        static_cast<double>(wake_detection.confidence),
        static_cast<unsigned long>(level),
        static_cast<unsigned long>(noise_floor),
        static_cast<unsigned long>(speech_peak_level));
    hexe::voice::WakeCandidateMetrics candidate;
    candidate.source = wake_detection.source;
    candidate.model = wake_detection.model;
    candidate.confidence = wake_detection.confidence;
    candidate.chunk_index = micro_vad.chunk_index;
    candidate.chunk_count = 1;
    candidate.detection_window_ms = kFrameDurationMs;
    candidate.frame_level = level;
    candidate.noise_floor_level = noise_floor;
    candidate.speech_peak_level = speech_peak_level;
    candidate.endpoint_audio_profile_version = "xvf3800_i2s_processed_v1";
    hexe::voice::submit_wake_candidate(candidate);
  }

  const hexe::voice::LocalKeywordDetection &stop_detection = local_keywords.playback_stop;
  if (stop_detection.detected) {
    ESP_LOGI(
        kTag,
        "Local stop detected: model=%s confidence=%.3f",
        stop_detection.model == nullptr ? "unknown" : stop_detection.model,
        static_cast<double>(stop_detection.confidence));
    if (hexe::voice::tts_playback_active()) {
      hexe::voice::stop_playback("voice_stop");
    } else {
      hexe::voice::cancel_active_session("voice_stop");
    }
  }
}

void vad_task(void *arg) {
  (void)arg;

  uint32_t silent_frames = kVadSilenceHoldFrames;
  uint32_t voice_candidate_frames = 0;
  uint32_t noise_floor = 0;
  uint32_t speech_peak_level = 0;
  uint32_t micro_vad_chunk_index = 0;
  uint32_t micro_vad_silent_frames = 0;
  bool micro_vad_chunk_active = false;

  while (true) {
    if (hexe::state().ota_active) {
      auto &app_state = hexe::state();
      app_state.vad_enabled = false;
      app_state.vad_speaking = false;
      app_state.vad_level = 0;
      app_state.audio_streaming = false;
      g_vad_turn_active = false;
      silent_frames = kVadSilenceHoldFrames;
      voice_candidate_frames = 0;
      speech_peak_level = 0;
      micro_vad_chunk_active = false;
      micro_vad_silent_frames = 0;
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }

    if (g_mic_mutex == nullptr || xSemaphoreTake(g_mic_mutex, portMAX_DELAY) != pdTRUE) {
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    if (g_mic_paused_for_playback || !hexe::board::xvf3800_audio_rx_ready()) {
      if (micro_vad_chunk_active) {
        ++micro_vad_chunk_index;
      }
      micro_vad_chunk_active = false;
      micro_vad_silent_frames = 0;
      xSemaphoreGive(g_mic_mutex);
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    size_t bytes_read = 0;
    const esp_err_t result = hexe::board::xvf3800_audio_rx_read(
        g_raw_samples.data(),
        g_raw_samples.size() * sizeof(g_raw_samples[0]),
        &bytes_read,
        100);
    xSemaphoreGive(g_mic_mutex);
    if (result != ESP_OK || bytes_read == 0) {
      if (result == ESP_ERR_TIMEOUT && bytes_read == 0) {
        ++g_mic_read_timeout_count;
        if ((g_mic_read_timeout_count % kMicReadTimeoutLogEvery) == 0) {
          ESP_LOGD(kTag, "XVF3800 microphone read timeout count=%lu", static_cast<unsigned long>(g_mic_read_timeout_count));
        }
      } else {
        ESP_LOGW(kTag, "XVF3800 microphone read failed: %s bytes=%u", esp_err_to_name(result), static_cast<unsigned>(bytes_read));
      }
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    const size_t stereo_frames = std::min(bytes_read / (sizeof(int32_t) * 2), kFrameSamples);
    for (size_t index = 0; index < stereo_frames; ++index) {
      g_mono_samples[index] = processed_voice_channel_sample(g_raw_samples[index * 2], g_raw_samples[(index * 2) + 1]);
    }

    const uint32_t level = estimate_level(g_mono_samples.data(), stereo_frames);
    if (noise_floor == 0) {
      noise_floor = level;
    }
    if (hexe::voice::post_tts_input_cooldown_active()) {
      noise_floor = update_noise_floor(noise_floor, level);
      hexe::state().vad_level = static_cast<int>(level);
      hexe::state().vad_speaking = false;
      g_vad_turn_active = false;
      silent_frames = kVadSilenceHoldFrames;
      voice_candidate_frames = 0;
      speech_peak_level = 0;
      micro_vad_chunk_active = false;
      micro_vad_silent_frames = 0;
      continue;
    }

    const bool was_speaking = hexe::state().vad_speaking;
    const uint32_t configured_start_threshold = micro_vad_start_threshold();
    const uint32_t configured_continue_threshold = micro_vad_continue_threshold(configured_start_threshold);
    const uint32_t start_threshold =
        std::max(configured_start_threshold, (noise_floor * kVadStartNoiseMultiplier) + kVadNoiseMargin);
    const uint32_t continue_threshold =
        std::max(configured_continue_threshold, (noise_floor * kVadContinueNoiseMultiplier) + kVadNoiseMargin);
    const uint32_t release_threshold = speech_peak_level == 0
        ? continue_threshold
        : std::max(continue_threshold, (speech_peak_level * kVadReleasePeakPercent) / 100);
    const uint32_t threshold = was_speaking ? release_threshold : start_threshold;
    const bool frame_over_threshold = level >= threshold;
    if (!was_speaking && !frame_over_threshold) {
      noise_floor = update_noise_floor(noise_floor, level);
    }
    if (frame_over_threshold) {
      if (voice_candidate_frames < kVadStartVoiceFrames) {
        ++voice_candidate_frames;
      }
    } else {
      voice_candidate_frames = 0;
    }
    const bool frame_has_voice = was_speaking ? frame_over_threshold : voice_candidate_frames >= kVadStartVoiceFrames;
    const hexe::voice::MicroVadFrameState micro_vad = micro_vad_frame_state(
        frame_has_voice,
        micro_vad_chunk_index,
        micro_vad_chunk_active,
        micro_vad_silent_frames);

    process_local_keywords(g_mono_samples.data(), stereo_frames, level, noise_floor, speech_peak_level, frame_has_voice, micro_vad);
    hexe::voice::observe_passive_placement_frame(g_mono_samples.data(), stereo_frames, level, frame_has_voice);
    hexe::voice::submit_audio_frame(
        g_mono_samples.data(),
        stereo_frames,
        level,
        noise_floor,
        speech_peak_level,
        frame_has_voice,
        &micro_vad);

    if (frame_has_voice) {
      speech_peak_level = std::max(speech_peak_level, level);
      silent_frames = 0;
      apply_vad_state(true, level);
    } else {
      if (!was_speaking) {
        speech_peak_level = 0;
      }
      if (silent_frames < kVadSilenceHoldFrames) {
        ++silent_frames;
      }
      apply_vad_state(silent_frames < kVadSilenceHoldFrames, level);
    }
  }
}
}  // namespace

namespace hexe::board {

void init_audio() {
  if (!xvf3800_control_init()) {
    return;
  }
  if (!xvf3800_audio_bus_init()) {
    return;
  }
  g_mic_mutex = xSemaphoreCreateMutex();
  if (g_mic_mutex == nullptr) {
    ESP_LOGE(kTag, "Failed to create XVF3800 microphone mutex");
    return;
  }
  if (xTaskCreate(vad_task, "hexe_xvf_vad", kVadTaskStackBytes, nullptr, 5, &g_vad_task) != pdPASS) {
    ESP_LOGE(kTag, "Failed to create XVF3800 VAD task");
    return;
  }
  g_audio_ready = true;
  hexe::state().vad_enabled = true;
  ESP_LOGI(kTag, "XVF3800 processed I2S microphone initialized at 16 kHz stereo/32-bit");
}

void update_audio() {
}

bool audio_input_ready() {
  return g_audio_ready && xvf3800_control_ready() && xvf3800_audio_rx_ready() && !g_mic_paused_for_playback;
}

bool audio_output_ready() {
  return xvf3800_control_ready() && xvf3800_audio_tx_ready();
}

bool pause_microphone_for_playback() {
  if (g_mic_mutex == nullptr) {
    return false;
  }
  if (xSemaphoreTake(g_mic_mutex, pdMS_TO_TICKS(500)) != pdTRUE) {
    return false;
  }
  if (!g_mic_paused_for_playback) {
    xvf3800_audio_rx_pause();
    g_mic_paused_for_playback = true;
    hexe::state().vad_enabled = false;
    hexe::state().vad_speaking = false;
    hexe::state().mic_paused_for_playback = true;
  }
  return true;
}

void resume_microphone_after_playback() {
  if (g_mic_mutex == nullptr) {
    return;
  }
  if (g_mic_paused_for_playback) {
    xvf3800_audio_rx_resume();
    g_mic_paused_for_playback = false;
    hexe::state().vad_enabled = true;
    hexe::state().mic_paused_for_playback = false;
  }
  xSemaphoreGive(g_mic_mutex);
}

}  // namespace hexe::board
