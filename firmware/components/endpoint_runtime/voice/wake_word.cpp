#include "voice/wake_word.h"

#include "esp_log.h"
#include "voice/micro_wake_engine.h"
#include "voice/model_bundle.h"

namespace {
constexpr char kTag[] = "hexe_wake";
constexpr int kWakeElectionTimeoutMs = 300;

extern const uint8_t kAlexaWakeModelStart[] asm("_binary_alexa_tflite_start");
extern const uint8_t kAlexaWakeModelEnd[] asm("_binary_alexa_tflite_end");
extern const uint8_t kStopKeywordModelStart[] asm("_binary_stop_tflite_start");
extern const uint8_t kStopKeywordModelEnd[] asm("_binary_stop_tflite_end");

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
    "1d999798b35b1fe2606465b75ab840be51c1811d2909d5e620cefb6e96f8abd0",
    "9011a8155b04de858c48038529235cbc0e42e9fca05a55bf588cb80a653a723b",
    2,
    0.90f,
    5,
    10,
    22348,
};

constexpr hexe::voice::LocalKeywordModel kStopKeywordModel = {
    "stop",
    "Stop",
    "Stop",
    "kahrendt_microWakeWord_stop_beta_20241017_5",
    "https://github.com/kahrendt/microWakeWord/releases/download/stop/stop.json",
    "https://github.com/kahrendt/microWakeWord/releases/download/stop/stop.tflite",
    "en",
    "Kevin Ahrendt",
    "2024.7.0",
    "bd13aeb1b83852649dc4fb6135cb160ff68716d14612b06f6a405342c57447aa",
    "b5a18c4ad681a89950dfade31011e1631bdcb333e93c84519a1a63ff4f071146",
    2,
    0.50f,
    5,
    10,
    21000,
};

size_t embedded_model_size(const uint8_t *start, const uint8_t *end) {
  return start < end ? static_cast<size_t>(end - start) : 0;
}
}

namespace hexe::voice {

void init_wake_word() {
  init_model_bundle_manager();
  const MicroWakeModelAsset embedded_models[] = {
      {MicroWakeModelRole::kWake,
       &kAlexaWakeModel,
       kAlexaWakeModelStart,
       embedded_model_size(kAlexaWakeModelStart, kAlexaWakeModelEnd),
       true},
      {MicroWakeModelRole::kPlaybackStop,
       &kStopKeywordModel,
       kStopKeywordModelStart,
       embedded_model_size(kStopKeywordModelStart, kStopKeywordModelEnd),
       true},
  };
  size_t selected_model_count = 0;
  const MicroWakeModelAsset *models = active_model_bundle_models(
      embedded_models,
      sizeof(embedded_models) / sizeof(embedded_models[0]),
      &selected_model_count);
  init_micro_wake_engine(models, selected_model_count);
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

LocalKeywordFrameDetections inspect_local_keyword_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking) {
  if (!wake_word_on_device_available() && !playback_stop_word_on_device_available()) {
    return {};
  }
  return process_micro_wake_frame(
      samples,
      sample_count,
      level,
      noise_floor_level,
      speech_peak_level,
      vad_speaking);
}

LocalKeywordDetection inspect_wake_word_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking) {
  return inspect_local_keyword_frame(samples, sample_count, level, noise_floor_level, speech_peak_level, vad_speaking)
      .wake;
}

LocalKeywordDetection inspect_playback_stop_word_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking) {
  return inspect_local_keyword_frame(samples, sample_count, level, noise_floor_level, speech_peak_level, vad_speaking)
      .playback_stop;
}

const char *wake_word_runtime_mode() {
  return wake_word_on_device_available() ? "endpoint_micro_wake_word_experimental" : "backend_streaming_with_micro_wake_word_manifest";
}

bool wake_word_on_device_available() {
  return micro_wake_engine_status().wake_ready;
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
  return micro_wake_engine_status().wake_reason;
}

const LocalKeywordModel &playback_stop_word_model() {
  return kStopKeywordModel;
}

bool playback_stop_word_experimental_provider_configured() {
  return true;
}

const char *playback_stop_word_runtime_mode() {
  return playback_stop_word_on_device_available() ? "endpoint_stop_keyword_experimental_with_backend_stt_interrupt_fallback"
                                                 : "backend_stt_interrupt_with_stop_keyword_manifest";
}

bool playback_stop_word_on_device_available() {
  return micro_wake_engine_status().stop_ready;
}

bool playback_stop_word_active() {
  return playback_stop_word_on_device_available();
}

const char *playback_stop_word_unavailable_reason() {
  return micro_wake_engine_status().stop_reason;
}

}  // namespace hexe::voice
