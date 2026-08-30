#pragma once

#include <cstddef>
#include <cstdint>

namespace hexe::voice {

struct LocalKeywordModel {
  const char *id{nullptr};
  const char *wake_word{nullptr};
  const char *alias{nullptr};
  const char *source{nullptr};
  const char *manifest_url{nullptr};
  const char *tflite_url{nullptr};
  const char *trained_languages{nullptr};
  const char *author{nullptr};
  const char *minimum_esphome_version{nullptr};
  const char *manifest_sha256{nullptr};
  const char *tflite_sha256{nullptr};
  int model_version{0};
  float probability_cutoff{0.0f};
  int sliding_window_size{0};
  int feature_step_size_ms{0};
  int tensor_arena_size{0};
};

struct LocalKeywordDetection {
  bool detected{false};
  const char *source{nullptr};
  const char *model{nullptr};
  const char *wake_word{nullptr};
  float confidence{0.0f};
};

struct LocalKeywordFrameDetections {
  LocalKeywordDetection wake;
  LocalKeywordDetection playback_stop;
};

void init_wake_word();
LocalKeywordFrameDetections inspect_local_keyword_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking);
LocalKeywordDetection inspect_wake_word_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking);
LocalKeywordDetection inspect_playback_stop_word_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking);
const char *wake_word_runtime_mode();
bool wake_word_on_device_available();
bool wake_word_backend_owned();
bool wake_word_election_capable();
int wake_word_election_timeout_ms();
const char *wake_word_candidate_source();
const LocalKeywordModel &wake_word_primary_model();
bool wake_word_experimental_provider_configured();
const char *wake_word_unavailable_reason();
const LocalKeywordModel &playback_stop_word_model();
bool playback_stop_word_experimental_provider_configured();
const char *playback_stop_word_runtime_mode();
bool playback_stop_word_on_device_available();
bool playback_stop_word_active();
const char *playback_stop_word_unavailable_reason();

}  // namespace hexe::voice
