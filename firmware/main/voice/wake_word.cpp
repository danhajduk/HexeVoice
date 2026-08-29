#include "voice/wake_word.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_wake";
constexpr int kWakeElectionTimeoutMs = 300;

#if defined(HEXE_MICRO_WAKE_WORD_ENGINE_LINKED) && HEXE_MICRO_WAKE_WORD_ENGINE_LINKED
constexpr bool kMicroWakeWordEngineLinked = true;
#else
constexpr bool kMicroWakeWordEngineLinked = false;
#endif

constexpr hexe::voice::LocalKeywordModel kAlexaWakeModel = {
    "alexa",
    "Alexa",
    "Hexe",
    "esphome_micro_wake_word_models_v2",
    "github://esphome/micro-wake-word-models/models/v2/alexa.json@main",
    "github://esphome/micro-wake-word-models/models/v2/alexa.tflite@main",
    "en",
    "Kevin Ahrendt",
    "2024.7.0",
    0.90f,
    5,
    10,
    22348,
};

constexpr hexe::voice::LocalKeywordModel kStopKeywordModel = {
    "stop",
    "Stop",
    "Stop",
    "hexe_endpoint_local_keyword",
    "",
    "",
    "en",
    "Hexe",
    "",
    0.0f,
    0,
    10,
    0,
};
}

namespace hexe::voice {

void init_wake_word() {
  ESP_LOGI(
      kTag,
      "Experimental endpoint microWakeWord provider configured: model=%s wake_word=%s alias=%s arena=%d bytes cutoff=%.2f",
      kAlexaWakeModel.id,
      kAlexaWakeModel.wake_word,
      kAlexaWakeModel.alias,
      kAlexaWakeModel.tensor_arena_size,
      static_cast<double>(kAlexaWakeModel.probability_cutoff));
  if (!wake_word_on_device_available()) {
    ESP_LOGW(kTag, "Endpoint microWakeWord inference unavailable: %s", wake_word_unavailable_reason());
  }
  ESP_LOGI(kTag, "Backend openWakeWord fallback remains enabled through wake election timeout policy");
}

LocalKeywordDetection inspect_wake_word_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking) {
  (void)samples;
  (void)sample_count;
  (void)level;
  (void)noise_floor_level;
  (void)speech_peak_level;
  (void)vad_speaking;
  if (!wake_word_on_device_available()) {
    return {};
  }
  // The native firmware contract is ready; actual microWakeWord/TFLM inference is linked by a later engine adapter.
  return {};
}

LocalKeywordDetection inspect_playback_stop_word_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking) {
  (void)samples;
  (void)sample_count;
  (void)level;
  (void)noise_floor_level;
  (void)speech_peak_level;
  (void)vad_speaking;
  if (!playback_stop_word_on_device_available()) {
    return {};
  }
  // The Stop keyword shares the local keyword hook but remains inactive until a trained model is linked.
  return {};
}

const char *wake_word_runtime_mode() {
  return wake_word_on_device_available() ? "endpoint_micro_wake_word_experimental" : "backend_streaming_with_micro_wake_word_manifest";
}

bool wake_word_on_device_available() {
  return kMicroWakeWordEngineLinked;
}

bool wake_word_backend_owned() {
  return !wake_word_on_device_available();
}

bool wake_word_election_capable() {
  return true;
}

int wake_word_election_timeout_ms() {
  return kWakeElectionTimeoutMs;
}

const char *wake_word_candidate_source() {
  return "endpoint_micro_wake_word";
}

const LocalKeywordModel &wake_word_primary_model() {
  return kAlexaWakeModel;
}

bool wake_word_experimental_provider_configured() {
  return true;
}

const char *wake_word_unavailable_reason() {
  return wake_word_on_device_available() ? "available" : "missing_micro_wake_word_inference_engine";
}

const LocalKeywordModel &playback_stop_word_model() {
  return kStopKeywordModel;
}

bool playback_stop_word_experimental_provider_configured() {
  return true;
}

const char *playback_stop_word_runtime_mode() {
  return playback_stop_word_on_device_available() ? "endpoint_stop_keyword_experimental" : "backend_stt_interrupt_with_stop_keyword_manifest";
}

bool playback_stop_word_on_device_available() {
  return false;
}

bool playback_stop_word_active() {
  return false;
}

const char *playback_stop_word_unavailable_reason() {
  if (!kMicroWakeWordEngineLinked) {
    return "missing_micro_wake_word_inference_engine";
  }
  return "missing_stop_keyword_model";
}

}  // namespace hexe::voice
