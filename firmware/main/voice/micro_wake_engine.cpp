#include "voice/micro_wake_engine.h"

#include <algorithm>
#include <array>
#include <cstring>

#include "esp_log.h"

#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
#include "tensorflow/lite/schema/schema_generated.h"
#endif

namespace {

constexpr char kTag[] = "hexe_micro_wake";
constexpr size_t kMaxModels = 4;
constexpr size_t kTfliteHeaderSize = 8;
constexpr int kExpectedTfliteSchemaVersion = 3;

#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
constexpr bool kTflmLinked = true;
#else
constexpr bool kTflmLinked = false;
#endif

#if defined(HEXE_MICRO_WAKE_WORD_FEATURE_FRONTEND_ENABLED) && HEXE_MICRO_WAKE_WORD_FEATURE_FRONTEND_ENABLED
constexpr bool kFeatureFrontendLinked = true;
#else
constexpr bool kFeatureFrontendLinked = false;
#endif

struct EngineModel {
  hexe::voice::MicroWakeModelRole role{hexe::voice::MicroWakeModelRole::kWake};
  const hexe::voice::LocalKeywordModel *metadata{nullptr};
  const uint8_t *model_data{nullptr};
  size_t model_size{0};
  bool enabled{false};
  bool valid{false};
};

struct EngineState {
  bool initialized{false};
  std::array<EngineModel, kMaxModels> models{};
  size_t model_count{0};
};

EngineState g_engine;

bool has_tflite_header(const uint8_t *model_data, size_t model_size) {
  return model_data != nullptr && model_size >= kTfliteHeaderSize && std::memcmp(model_data + 4, "TFL3", 4) == 0;
}

bool validate_model_asset(const hexe::voice::MicroWakeModelAsset &model) {
  if (model.metadata == nullptr || model.model_data == nullptr || model.model_size == 0) {
    return false;
  }
  if (!has_tflite_header(model.model_data, model.model_size)) {
    ESP_LOGW(kTag, "microWakeWord model '%s' has no valid TFLite header", model.metadata->id);
    return false;
  }
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  const tflite::Model *tflite_model = tflite::GetModel(model.model_data);
  if (tflite_model == nullptr || tflite_model->version() != kExpectedTfliteSchemaVersion) {
    ESP_LOGW(kTag, "microWakeWord model '%s' schema is incompatible with this TFLM build", model.metadata->id);
    return false;
  }
#endif
  return true;
}

bool role_asset_available(hexe::voice::MicroWakeModelRole role) {
  for (size_t index = 0; index < g_engine.model_count; ++index) {
    const EngineModel &model = g_engine.models[index];
    if (model.role == role && model.valid) {
      return true;
    }
  }
  return false;
}

size_t role_asset_bytes(hexe::voice::MicroWakeModelRole role) {
  for (size_t index = 0; index < g_engine.model_count; ++index) {
    const EngineModel &model = g_engine.models[index];
    if (model.role == role && model.valid) {
      return model.model_size;
    }
  }
  return 0;
}

bool role_ready(hexe::voice::MicroWakeModelRole role) {
  if (!kTflmLinked || !kFeatureFrontendLinked || !g_engine.initialized) {
    return false;
  }
  for (size_t index = 0; index < g_engine.model_count; ++index) {
    const EngineModel &model = g_engine.models[index];
    if (model.role == role && model.valid && model.enabled) {
      return true;
    }
  }
  return false;
}

const char *role_reason(hexe::voice::MicroWakeModelRole role) {
  if (!kTflmLinked) {
    return "missing_micro_wake_word_inference_engine";
  }
  if (!g_engine.initialized) {
    return "micro_wake_word_engine_not_initialized";
  }
  if (!role_asset_available(role)) {
    return role == hexe::voice::MicroWakeModelRole::kPlaybackStop ? "missing_stop_keyword_model_asset"
                                                                  : "missing_micro_wake_word_model_asset";
  }
  if (!kFeatureFrontendLinked) {
    return "missing_micro_wake_word_feature_frontend";
  }
  if (!role_ready(role)) {
    return role == hexe::voice::MicroWakeModelRole::kPlaybackStop ? "stop_keyword_model_disabled"
                                                                  : "micro_wake_word_model_disabled";
  }
  return "available";
}

}  // namespace

namespace hexe::voice {

void init_micro_wake_engine(const MicroWakeModelAsset *models, size_t model_count) {
  g_engine = {};
  g_engine.initialized = true;
  if (models == nullptr || model_count == 0) {
    ESP_LOGI(kTag, "microWakeWord/TFLM adapter initialized: tflm=%s frontend=%s models=0",
             kTflmLinked ? "linked" : "missing",
             kFeatureFrontendLinked ? "linked" : "missing");
    return;
  }
  const size_t bounded_count = std::min(model_count, kMaxModels);
  for (size_t index = 0; index < bounded_count; ++index) {
    const MicroWakeModelAsset &source = models[index];
    EngineModel &target = g_engine.models[g_engine.model_count];
    target.role = source.role;
    target.metadata = source.metadata;
    target.model_data = source.model_data;
    target.model_size = source.model_size;
    target.enabled = source.enabled;
    target.valid = validate_model_asset(source);
    if (target.valid) {
      ESP_LOGI(kTag, "Registered microWakeWord model asset: id=%s role=%s size=%u bytes",
               target.metadata->id,
               target.role == MicroWakeModelRole::kPlaybackStop ? "playback_stop" : "wake",
               static_cast<unsigned>(target.model_size));
    }
    ++g_engine.model_count;
  }
  if (model_count > kMaxModels) {
    ESP_LOGW(kTag, "Ignoring %u extra microWakeWord model assets", static_cast<unsigned>(model_count - kMaxModels));
  }
  ESP_LOGI(kTag, "microWakeWord/TFLM adapter initialized: tflm=%s frontend=%s models=%u",
           kTflmLinked ? "linked" : "missing",
           kFeatureFrontendLinked ? "linked" : "missing",
           static_cast<unsigned>(g_engine.model_count));
}

MicroWakeEngineStatus micro_wake_engine_status() {
  MicroWakeEngineStatus status;
  status.tflm_linked = kTflmLinked;
  status.feature_frontend_linked = kFeatureFrontendLinked;
  status.initialized = g_engine.initialized;
  status.wake_model_asset_available = role_asset_available(MicroWakeModelRole::kWake);
  status.stop_model_asset_available = role_asset_available(MicroWakeModelRole::kPlaybackStop);
  status.wake_model_asset_bytes = role_asset_bytes(MicroWakeModelRole::kWake);
  status.stop_model_asset_bytes = role_asset_bytes(MicroWakeModelRole::kPlaybackStop);
  status.wake_ready = role_ready(MicroWakeModelRole::kWake);
  status.stop_ready = role_ready(MicroWakeModelRole::kPlaybackStop);
  status.wake_reason = role_reason(MicroWakeModelRole::kWake);
  status.stop_reason = role_reason(MicroWakeModelRole::kPlaybackStop);
  return status;
}

LocalKeywordDetection process_micro_wake_frame(
    MicroWakeModelRole role,
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
  if (!role_ready(role)) {
    return {};
  }
  // Model assets and role policy must be registered before detections can be emitted.
  return {};
}

void reset_micro_wake_engine() {
  g_engine = {};
}

}  // namespace hexe::voice
