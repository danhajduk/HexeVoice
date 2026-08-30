#include "voice/micro_wake_engine.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <memory>

#include "esp_heap_caps.h"
#include "esp_log.h"

#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
#include "tensorflow/lite/core/c/common.h"
#include "tensorflow/lite/kernels/internal/tensor_ctypes.h"
#include "tensorflow/lite/micro/micro_allocator.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/micro/micro_resource_variable.h"
#include "tensorflow/lite/schema/schema_generated.h"
#endif

namespace {

constexpr char kTag[] = "hexe_micro_wake";
constexpr size_t kMaxModels = 4;
constexpr size_t kTfliteHeaderSize = 8;
constexpr int kExpectedTfliteSchemaVersion = 3;
constexpr size_t kPreprocessorFeatureSize = 40;
constexpr size_t kPreprocessorWindowSamples = 480;
constexpr size_t kPreprocessorStepSamples = 160;
constexpr size_t kPreprocessorArenaSize = 16 * 1024;
constexpr size_t kStreamingVariableArenaSize = 1024;
constexpr size_t kStreamingArenaMultiplier = 2;
constexpr size_t kMaxSlidingWindowSize = 16;
constexpr int16_t kMinWindowsBeforeDetection = 100;

extern const uint8_t kAudioPreprocessorModelStart[] asm("_binary_audio_preprocessor_int8_tflite_start");
extern const uint8_t kAudioPreprocessorModelEnd[] asm("_binary_audio_preprocessor_int8_tflite_end");

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

#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
using AudioPreprocessorOpResolver = tflite::MicroMutableOpResolver<18>;
using StreamingOpResolver = tflite::MicroMutableOpResolver<20>;

struct AudioFrontendState {
  AudioPreprocessorOpResolver resolver;
  bool resolver_initialized{false};
  uint8_t *tensor_arena{nullptr};
  std::unique_ptr<tflite::MicroInterpreter> interpreter;
  bool ready{false};
  const char *failure_reason{nullptr};
  std::array<int16_t, kPreprocessorWindowSamples> rolling_samples{};
  std::array<int16_t, kPreprocessorWindowSamples> ordered_samples{};
  size_t write_index{0};
  size_t samples_filled{0};
  uint64_t total_samples{0};
  uint64_t last_feature_sample{0};
  uint32_t feature_frame_count{0};
};

struct StreamingRuntime {
  StreamingOpResolver resolver;
  bool resolver_initialized{false};
  uint8_t *tensor_arena{nullptr};
  size_t tensor_arena_size{0};
  uint8_t *variable_arena{nullptr};
  tflite::MicroAllocator *allocator{nullptr};
  tflite::MicroResourceVariables *resource_variables{nullptr};
  std::unique_ptr<tflite::MicroInterpreter> interpreter;
  std::array<uint8_t, kMaxSlidingWindowSize> recent_probabilities{};
  size_t sliding_window_size{0};
  size_t last_probability_index{0};
  uint8_t current_stride_step{0};
  int16_t ignore_windows{-kMinWindowsBeforeDetection};
  bool loaded{false};
  bool unprocessed_probability{false};
  uint32_t inference_count{0};
  uint32_t detection_count{0};
  uint8_t last_probability{0};
  uint8_t last_average_probability{0};
  uint8_t last_max_probability{0};
  uint8_t best_average_probability{0};
  uint8_t last_detection_probability{0};
  const char *failure_reason{nullptr};
};
#endif

struct EngineModel {
  hexe::voice::MicroWakeModelRole role{hexe::voice::MicroWakeModelRole::kWake};
  const hexe::voice::LocalKeywordModel *metadata{nullptr};
  const uint8_t *model_data{nullptr};
  size_t model_size{0};
  bool enabled{false};
  bool valid{false};
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  std::unique_ptr<StreamingRuntime> runtime;
#endif
};

struct EngineState {
  bool initialized{false};
  std::array<EngineModel, kMaxModels> models{};
  size_t model_count{0};
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  AudioFrontendState frontend;
#endif
};

EngineState g_engine;

uint8_t probability_cutoff_as_uint8(float probability_cutoff) {
  const float bounded = std::max(0.0f, std::min(1.0f, probability_cutoff));
  return static_cast<uint8_t>(std::lround(bounded * 255.0f));
}

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

#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
uint8_t *allocate_arena(size_t bytes) {
  auto *arena = static_cast<uint8_t *>(heap_caps_calloc(1, bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (arena == nullptr) {
    arena = static_cast<uint8_t *>(heap_caps_calloc(1, bytes, MALLOC_CAP_8BIT));
  }
  return arena;
}

void free_arena(uint8_t *&arena) {
  if (arena != nullptr) {
    heap_caps_free(arena);
    arena = nullptr;
  }
}

bool register_audio_preprocessor_ops(AudioPreprocessorOpResolver &resolver) {
  if (resolver.AddReshape() != kTfLiteOk) return false;
  if (resolver.AddCast() != kTfLiteOk) return false;
  if (resolver.AddStridedSlice() != kTfLiteOk) return false;
  if (resolver.AddConcatenation() != kTfLiteOk) return false;
  if (resolver.AddMul() != kTfLiteOk) return false;
  if (resolver.AddAdd() != kTfLiteOk) return false;
  if (resolver.AddDiv() != kTfLiteOk) return false;
  if (resolver.AddMinimum() != kTfLiteOk) return false;
  if (resolver.AddMaximum() != kTfLiteOk) return false;
  if (resolver.AddWindow() != kTfLiteOk) return false;
  if (resolver.AddFftAutoScale() != kTfLiteOk) return false;
  if (resolver.AddRfft() != kTfLiteOk) return false;
  if (resolver.AddEnergy() != kTfLiteOk) return false;
  if (resolver.AddFilterBank() != kTfLiteOk) return false;
  if (resolver.AddFilterBankSquareRoot() != kTfLiteOk) return false;
  if (resolver.AddFilterBankSpectralSubtraction() != kTfLiteOk) return false;
  if (resolver.AddPCAN() != kTfLiteOk) return false;
  return resolver.AddFilterBankLog() == kTfLiteOk;
}

bool register_streaming_ops(StreamingOpResolver &resolver) {
  if (resolver.AddCallOnce() != kTfLiteOk) return false;
  if (resolver.AddVarHandle() != kTfLiteOk) return false;
  if (resolver.AddReshape() != kTfLiteOk) return false;
  if (resolver.AddReadVariable() != kTfLiteOk) return false;
  if (resolver.AddStridedSlice() != kTfLiteOk) return false;
  if (resolver.AddConcatenation() != kTfLiteOk) return false;
  if (resolver.AddAssignVariable() != kTfLiteOk) return false;
  if (resolver.AddConv2D() != kTfLiteOk) return false;
  if (resolver.AddMul() != kTfLiteOk) return false;
  if (resolver.AddAdd() != kTfLiteOk) return false;
  if (resolver.AddMean() != kTfLiteOk) return false;
  if (resolver.AddFullyConnected() != kTfLiteOk) return false;
  if (resolver.AddLogistic() != kTfLiteOk) return false;
  if (resolver.AddQuantize() != kTfLiteOk) return false;
  if (resolver.AddDepthwiseConv2D() != kTfLiteOk) return false;
  if (resolver.AddAveragePool2D() != kTfLiteOk) return false;
  if (resolver.AddMaxPool2D() != kTfLiteOk) return false;
  if (resolver.AddPad() != kTfLiteOk) return false;
  if (resolver.AddPack() != kTfLiteOk) return false;
  return resolver.AddSplitV() == kTfLiteOk;
}

void reset_runtime_probabilities(StreamingRuntime &runtime) {
  runtime.recent_probabilities.fill(0);
  runtime.last_probability_index = 0;
  runtime.current_stride_step = 0;
  runtime.ignore_windows = -kMinWindowsBeforeDetection;
  runtime.unprocessed_probability = false;
}

bool initialize_audio_frontend() {
  AudioFrontendState &frontend = g_engine.frontend;
  if (frontend.ready) {
    return true;
  }
  if (!has_tflite_header(kAudioPreprocessorModelStart,
                         static_cast<size_t>(kAudioPreprocessorModelEnd - kAudioPreprocessorModelStart))) {
    frontend.failure_reason = "missing_micro_wake_word_feature_frontend";
    return false;
  }
  const tflite::Model *model = tflite::GetModel(kAudioPreprocessorModelStart);
  if (model == nullptr || model->version() != kExpectedTfliteSchemaVersion) {
    frontend.failure_reason = "micro_wake_word_feature_frontend_schema_mismatch";
    return false;
  }
  if (!frontend.resolver_initialized) {
    if (!register_audio_preprocessor_ops(frontend.resolver)) {
      frontend.failure_reason = "micro_wake_word_feature_frontend_ops_unavailable";
      return false;
    }
    frontend.resolver_initialized = true;
  }
  if (frontend.tensor_arena == nullptr) {
    frontend.tensor_arena = allocate_arena(kPreprocessorArenaSize);
    if (frontend.tensor_arena == nullptr) {
      frontend.failure_reason = "micro_wake_word_feature_frontend_arena_alloc_failed";
      return false;
    }
  }
  frontend.interpreter = std::make_unique<tflite::MicroInterpreter>(
      model,
      frontend.resolver,
      frontend.tensor_arena,
      kPreprocessorArenaSize);
  if (frontend.interpreter->AllocateTensors() != kTfLiteOk) {
    frontend.failure_reason = "micro_wake_word_feature_frontend_tensor_alloc_failed";
    return false;
  }
  TfLiteTensor *input = frontend.interpreter->input(0);
  TfLiteTensor *output = frontend.interpreter->output(0);
  if (input == nullptr || input->dims->size != 2 || input->dims->data[0] != 1 ||
      input->dims->data[1] != static_cast<int>(kPreprocessorWindowSamples) || input->type != kTfLiteInt16) {
    frontend.failure_reason = "micro_wake_word_feature_frontend_input_mismatch";
    return false;
  }
  if (output == nullptr || output->dims->size != 1 ||
      output->dims->data[0] != static_cast<int>(kPreprocessorFeatureSize) || output->type != kTfLiteInt8) {
    frontend.failure_reason = "micro_wake_word_feature_frontend_output_mismatch";
    return false;
  }
  frontend.ready = true;
  frontend.failure_reason = nullptr;
  ESP_LOGI(kTag, "microWakeWord audio frontend initialized: model_size=%u arena=%u",
           static_cast<unsigned>(kAudioPreprocessorModelEnd - kAudioPreprocessorModelStart),
           static_cast<unsigned>(kPreprocessorArenaSize));
  return true;
}

bool initialize_streaming_runtime(EngineModel &model) {
  if (!model.valid || !model.enabled || model.metadata == nullptr) {
    return false;
  }
  if (!model.runtime) {
    model.runtime = std::make_unique<StreamingRuntime>();
  }
  StreamingRuntime &runtime = *model.runtime;
  if (runtime.loaded) {
    return true;
  }
  const tflite::Model *tflite_model = tflite::GetModel(model.model_data);
  if (tflite_model == nullptr || tflite_model->version() != kExpectedTfliteSchemaVersion) {
    runtime.failure_reason = "micro_wake_word_model_schema_mismatch";
    return false;
  }
  if (!runtime.resolver_initialized) {
    if (!register_streaming_ops(runtime.resolver)) {
      runtime.failure_reason = "micro_wake_word_model_ops_unavailable";
      return false;
    }
    runtime.resolver_initialized = true;
  }
  if (runtime.variable_arena == nullptr) {
    runtime.variable_arena = allocate_arena(kStreamingVariableArenaSize);
    if (runtime.variable_arena == nullptr) {
      runtime.failure_reason = "micro_wake_word_variable_arena_alloc_failed";
      return false;
    }
    runtime.allocator = tflite::MicroAllocator::Create(runtime.variable_arena, kStreamingVariableArenaSize);
    runtime.resource_variables = tflite::MicroResourceVariables::Create(runtime.allocator, 20);
  }
  if (runtime.tensor_arena == nullptr) {
    const size_t arena_size =
        ((static_cast<size_t>(model.metadata->tensor_arena_size) * kStreamingArenaMultiplier) + 15) & ~static_cast<size_t>(15);
    runtime.tensor_arena = allocate_arena(arena_size);
    if (runtime.tensor_arena == nullptr) {
      runtime.failure_reason = "micro_wake_word_tensor_arena_alloc_failed";
      return false;
    }
    runtime.tensor_arena_size = arena_size;
  }
  runtime.interpreter = std::make_unique<tflite::MicroInterpreter>(
      tflite_model,
      runtime.resolver,
      runtime.tensor_arena,
      runtime.tensor_arena_size,
      runtime.resource_variables);
  if (runtime.interpreter->AllocateTensors() != kTfLiteOk) {
    runtime.failure_reason = "micro_wake_word_tensor_alloc_failed";
    return false;
  }
  TfLiteTensor *input = runtime.interpreter->input(0);
  TfLiteTensor *output = runtime.interpreter->output(0);
  if (input == nullptr || input->dims->size != 3 || input->dims->data[0] != 1 ||
      input->dims->data[2] != static_cast<int>(kPreprocessorFeatureSize) || input->type != kTfLiteInt8) {
    runtime.failure_reason = "micro_wake_word_input_tensor_mismatch";
    return false;
  }
  if (output == nullptr || output->dims->size != 2 || output->dims->data[0] != 1 ||
      output->dims->data[1] != 1 || output->type != kTfLiteUInt8) {
    runtime.failure_reason = "micro_wake_word_output_tensor_mismatch";
    return false;
  }
  runtime.sliding_window_size = std::min<size_t>(model.metadata->sliding_window_size, kMaxSlidingWindowSize);
  reset_runtime_probabilities(runtime);
  runtime.loaded = true;
  runtime.failure_reason = nullptr;
  ESP_LOGI(kTag, "microWakeWord streaming model initialized: id=%s role=%s arena=%u input_stride=%d",
           model.metadata->id,
           model.role == hexe::voice::MicroWakeModelRole::kPlaybackStop ? "playback_stop" : "wake",
           static_cast<unsigned>(runtime.tensor_arena_size),
           input->dims->data[1]);
  return true;
}

void release_runtime(EngineModel &model) {
  if (!model.runtime) {
    return;
  }
  model.runtime->interpreter.reset();
  free_arena(model.runtime->tensor_arena);
  free_arena(model.runtime->variable_arena);
  model.runtime.reset();
}

void release_frontend() {
  g_engine.frontend.interpreter.reset();
  free_arena(g_engine.frontend.tensor_arena);
  g_engine.frontend.ready = false;
  g_engine.frontend.failure_reason = nullptr;
  g_engine.frontend.write_index = 0;
  g_engine.frontend.samples_filled = 0;
  g_engine.frontend.total_samples = 0;
  g_engine.frontend.last_feature_sample = 0;
  g_engine.frontend.feature_frame_count = 0;
  g_engine.frontend.rolling_samples.fill(0);
  g_engine.frontend.ordered_samples.fill(0);
}

bool generate_feature(const int16_t *samples, int8_t feature[kPreprocessorFeatureSize]) {
  AudioFrontendState &frontend = g_engine.frontend;
  if (!frontend.ready || frontend.interpreter == nullptr) {
    return false;
  }
  for (size_t index = 0; index < kPreprocessorWindowSamples; ++index) {
    frontend.ordered_samples[index] =
        frontend.rolling_samples[(frontend.write_index + index) % kPreprocessorWindowSamples];
  }
  TfLiteTensor *input = frontend.interpreter->input(0);
  TfLiteTensor *output = frontend.interpreter->output(0);
  std::copy_n(frontend.ordered_samples.data(), kPreprocessorWindowSamples, tflite::GetTensorData<int16_t>(input));
  if (frontend.interpreter->Invoke() != kTfLiteOk) {
    frontend.failure_reason = "micro_wake_word_feature_frontend_invoke_failed";
    frontend.ready = false;
    return false;
  }
  std::copy_n(tflite::GetTensorData<int8_t>(output), kPreprocessorFeatureSize, feature);
  ++frontend.feature_frame_count;
  (void)samples;
  return true;
}

bool perform_streaming_inference(EngineModel &model, const int8_t feature[kPreprocessorFeatureSize]) {
  if (!model.runtime || !model.runtime->loaded || model.runtime->interpreter == nullptr || model.metadata == nullptr) {
    return false;
  }
  StreamingRuntime &runtime = *model.runtime;
  TfLiteTensor *input = runtime.interpreter->input(0);
  const int stride = input->dims->data[1];
  if (stride <= 0) {
    runtime.failure_reason = "micro_wake_word_input_stride_invalid";
    return false;
  }
  runtime.current_stride_step = runtime.current_stride_step % static_cast<uint8_t>(stride);
  std::copy_n(
      feature,
      kPreprocessorFeatureSize,
      tflite::GetTensorData<int8_t>(input) + (kPreprocessorFeatureSize * runtime.current_stride_step));
  ++runtime.current_stride_step;
  if (runtime.current_stride_step < stride) {
    return true;
  }

  runtime.current_stride_step = 0;
  if (runtime.interpreter->Invoke() != kTfLiteOk) {
    runtime.failure_reason = "micro_wake_word_invoke_failed";
    return false;
  }
  TfLiteTensor *output = runtime.interpreter->output(0);
  ++runtime.inference_count;
  ++runtime.last_probability_index;
  if (runtime.last_probability_index >= runtime.sliding_window_size) {
    runtime.last_probability_index = 0;
  }
  runtime.last_probability = output->data.uint8[0];
  runtime.recent_probabilities[runtime.last_probability_index] = runtime.last_probability;
  runtime.unprocessed_probability = true;
  if (runtime.last_probability < probability_cutoff_as_uint8(model.metadata->probability_cutoff)) {
    runtime.ignore_windows = std::min<int16_t>(runtime.ignore_windows + 1, 0);
  }
  return true;
}

hexe::voice::LocalKeywordDetection detection_from_runtime(EngineModel &model) {
  hexe::voice::LocalKeywordDetection detection = {};
  if (!model.runtime || !model.runtime->unprocessed_probability || model.metadata == nullptr) {
    return detection;
  }
  StreamingRuntime &runtime = *model.runtime;
  runtime.unprocessed_probability = false;
  if (runtime.ignore_windows < 0 || runtime.sliding_window_size == 0) {
    return detection;
  }

  uint32_t sum = 0;
  uint8_t max_probability = 0;
  for (size_t index = 0; index < runtime.sliding_window_size; ++index) {
    max_probability = std::max(max_probability, runtime.recent_probabilities[index]);
    sum += runtime.recent_probabilities[index];
  }
  const uint8_t cutoff = probability_cutoff_as_uint8(model.metadata->probability_cutoff);
  const uint8_t average = static_cast<uint8_t>(sum / runtime.sliding_window_size);
  runtime.last_average_probability = average;
  runtime.last_max_probability = max_probability;
  runtime.best_average_probability = std::max(runtime.best_average_probability, average);
  if (sum <= static_cast<uint32_t>(cutoff) * runtime.sliding_window_size) {
    return detection;
  }

  detection.detected = true;
  detection.source = model.role == hexe::voice::MicroWakeModelRole::kPlaybackStop
                         ? "endpoint_micro_wake_word_stop"
                         : hexe::voice::wake_word_candidate_source();
  detection.model = model.metadata->id;
  detection.wake_word = model.metadata->wake_word;
  detection.confidence = static_cast<float>(average) / 255.0f;
  ++runtime.detection_count;
  runtime.last_detection_probability = average;
  reset_runtime_probabilities(runtime);
  return detection;
}

hexe::voice::LocalKeywordFrameDetections process_feature_for_models(
    const int8_t feature[kPreprocessorFeatureSize]) {
  hexe::voice::LocalKeywordFrameDetections detections = {};
  for (size_t index = 0; index < g_engine.model_count; ++index) {
    EngineModel &model = g_engine.models[index];
    if (!model.enabled || !model.valid || !model.runtime || !model.runtime->loaded) {
      continue;
    }
    if (!perform_streaming_inference(model, feature)) {
      ESP_LOGW(kTag, "microWakeWord inference failed for model '%s': %s",
               model.metadata == nullptr ? "unknown" : model.metadata->id,
               model.runtime && model.runtime->failure_reason != nullptr ? model.runtime->failure_reason : "unknown");
      continue;
    }
    hexe::voice::LocalKeywordDetection model_detection = detection_from_runtime(model);
    if (!model_detection.detected) {
      continue;
    }
    if (model.role == hexe::voice::MicroWakeModelRole::kPlaybackStop) {
      detections.playback_stop = model_detection;
    } else {
      detections.wake = model_detection;
    }
  }
  return detections;
}
#endif

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

bool role_runtime_ready(hexe::voice::MicroWakeModelRole role) {
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  for (size_t index = 0; index < g_engine.model_count; ++index) {
    const EngineModel &model = g_engine.models[index];
    if (model.role == role && model.valid && model.enabled && model.runtime && model.runtime->loaded) {
      return true;
    }
  }
#else
  (void)role;
#endif
  return false;
}

size_t role_runtime_arena_bytes(hexe::voice::MicroWakeModelRole role) {
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  for (size_t index = 0; index < g_engine.model_count; ++index) {
    const EngineModel &model = g_engine.models[index];
    if (model.role == role && model.valid && model.enabled && model.runtime && model.runtime->loaded) {
      return model.runtime->tensor_arena_size + kStreamingVariableArenaSize;
    }
  }
#else
  (void)role;
#endif
  return 0;
}

hexe::voice::MicroWakeRuntimeDiagnostics role_diagnostics(hexe::voice::MicroWakeModelRole role) {
  hexe::voice::MicroWakeRuntimeDiagnostics diagnostics;
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  for (size_t index = 0; index < g_engine.model_count; ++index) {
    const EngineModel &model = g_engine.models[index];
    if (model.role == role && model.valid && model.enabled && model.runtime && model.runtime->loaded) {
      diagnostics.inference_count = model.runtime->inference_count;
      diagnostics.detection_count = model.runtime->detection_count;
      diagnostics.last_probability = model.runtime->last_probability;
      diagnostics.last_average_probability = model.runtime->last_average_probability;
      diagnostics.last_max_probability = model.runtime->last_max_probability;
      diagnostics.best_average_probability = model.runtime->best_average_probability;
      diagnostics.last_detection_probability = model.runtime->last_detection_probability;
      break;
    }
  }
#else
  (void)role;
#endif
  return diagnostics;
}

bool role_ready(hexe::voice::MicroWakeModelRole role) {
  if (!kTflmLinked || !kFeatureFrontendLinked || !g_engine.initialized) {
    return false;
  }
  if (!role_runtime_ready(role)) {
    return false;
  }
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  return g_engine.frontend.ready;
#else
  return false;
#endif
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
  #if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  if (!g_engine.frontend.ready) {
    return g_engine.frontend.failure_reason == nullptr ? "micro_wake_word_feature_frontend_not_ready"
                                                       : g_engine.frontend.failure_reason;
  }
  #endif
  for (size_t index = 0; index < g_engine.model_count; ++index) {
    const EngineModel &model = g_engine.models[index];
    if (model.role == role && model.valid && !model.enabled) {
      return role == hexe::voice::MicroWakeModelRole::kPlaybackStop ? "stop_keyword_model_disabled"
                                                                    : "micro_wake_word_model_disabled";
    }
    #if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
    if (model.role == role && model.valid && model.enabled && (!model.runtime || !model.runtime->loaded)) {
      return model.runtime && model.runtime->failure_reason != nullptr ? model.runtime->failure_reason
                                                                       : "micro_wake_word_model_not_loaded";
    }
    #endif
  }
  if (!role_ready(role)) {
    return role == hexe::voice::MicroWakeModelRole::kPlaybackStop ? "stop_keyword_model_disabled"
                                                                  : "micro_wake_word_model_disabled";
  }
  return "available";
}

}  // namespace

namespace hexe::voice {

bool test_load_micro_wake_model_assets(const MicroWakeModelAsset *models, size_t model_count, char *error_code, size_t error_code_size) {
  if (models == nullptr || model_count == 0) {
    if (error_code != nullptr && error_code_size > 0) {
      std::snprintf(error_code, error_code_size, "%s", "missing_model_bundle_assets");
    }
    return false;
  }
  bool wake_present = false;
  bool stop_present = false;
  for (size_t index = 0; index < model_count; ++index) {
    if (!validate_model_asset(models[index])) {
      if (error_code != nullptr && error_code_size > 0) {
        std::snprintf(error_code, error_code_size, "%s", "model_bundle_test_load_failed");
      }
      return false;
    }
    if (models[index].role == MicroWakeModelRole::kWake) {
      wake_present = true;
    } else if (models[index].role == MicroWakeModelRole::kPlaybackStop) {
      stop_present = true;
    }
  }
  if (!wake_present || !stop_present) {
    if (error_code != nullptr && error_code_size > 0) {
      std::snprintf(error_code, error_code_size, "%s", !wake_present ? "missing_wake_model" : "missing_stop_model");
    }
    return false;
  }
  if (error_code != nullptr && error_code_size > 0) {
    error_code[0] = '\0';
  }
  return true;
}

void init_micro_wake_engine(const MicroWakeModelAsset *models, size_t model_count) {
  reset_micro_wake_engine();
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
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  if (kFeatureFrontendLinked) {
    initialize_audio_frontend();
  }
  for (size_t index = 0; index < g_engine.model_count; ++index) {
    initialize_streaming_runtime(g_engine.models[index]);
  }
#endif
}

MicroWakeEngineStatus micro_wake_engine_status() {
  MicroWakeEngineStatus status;
  status.tflm_linked = kTflmLinked;
  status.feature_frontend_linked = kFeatureFrontendLinked;
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  status.feature_frontend_ready = g_engine.frontend.ready;
  status.feature_frame_count = g_engine.frontend.feature_frame_count;
#else
  status.feature_frontend_ready = false;
  status.feature_frame_count = 0;
#endif
  status.initialized = g_engine.initialized;
  status.wake_model_asset_available = role_asset_available(MicroWakeModelRole::kWake);
  status.stop_model_asset_available = role_asset_available(MicroWakeModelRole::kPlaybackStop);
  status.wake_model_asset_bytes = role_asset_bytes(MicroWakeModelRole::kWake);
  status.stop_model_asset_bytes = role_asset_bytes(MicroWakeModelRole::kPlaybackStop);
  status.wake_runtime_ready = role_runtime_ready(MicroWakeModelRole::kWake);
  status.stop_runtime_ready = role_runtime_ready(MicroWakeModelRole::kPlaybackStop);
  status.wake_runtime_arena_bytes = role_runtime_arena_bytes(MicroWakeModelRole::kWake);
  status.stop_runtime_arena_bytes = role_runtime_arena_bytes(MicroWakeModelRole::kPlaybackStop);
  status.wake_ready = role_ready(MicroWakeModelRole::kWake);
  status.stop_ready = role_ready(MicroWakeModelRole::kPlaybackStop);
  status.wake_reason = role_reason(MicroWakeModelRole::kWake);
  status.stop_reason = role_reason(MicroWakeModelRole::kPlaybackStop);
  status.wake_runtime = role_diagnostics(MicroWakeModelRole::kWake);
  status.stop_runtime = role_diagnostics(MicroWakeModelRole::kPlaybackStop);
  return status;
}

LocalKeywordFrameDetections process_micro_wake_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking) {
  (void)level;
  (void)noise_floor_level;
  (void)speech_peak_level;
  (void)vad_speaking;
  if (samples == nullptr || sample_count == 0) {
    return {};
  }
  if (!role_ready(MicroWakeModelRole::kWake) && !role_ready(MicroWakeModelRole::kPlaybackStop)) {
    return {};
  }
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  LocalKeywordFrameDetections detections = {};
  AudioFrontendState &frontend = g_engine.frontend;
  std::array<int8_t, kPreprocessorFeatureSize> feature{};
  for (size_t index = 0; index < sample_count; ++index) {
    frontend.rolling_samples[frontend.write_index] = samples[index];
    frontend.write_index = (frontend.write_index + 1) % kPreprocessorWindowSamples;
    if (frontend.samples_filled < kPreprocessorWindowSamples) {
      ++frontend.samples_filled;
    }
    ++frontend.total_samples;
    if (frontend.samples_filled == kPreprocessorWindowSamples &&
        (frontend.total_samples - frontend.last_feature_sample) >= kPreprocessorStepSamples) {
      frontend.last_feature_sample = frontend.total_samples;
      if (!generate_feature(samples, feature.data())) {
        break;
      }
      LocalKeywordFrameDetections feature_detections = process_feature_for_models(feature.data());
      if (feature_detections.wake.detected) {
        detections.wake = feature_detections.wake;
      }
      if (feature_detections.playback_stop.detected) {
        detections.playback_stop = feature_detections.playback_stop;
      }
    }
  }
  return detections;
#else
  (void)samples;
  (void)sample_count;
  return {};
#endif
}

void reset_micro_wake_engine() {
#if defined(HEXE_MICRO_WAKE_WORD_TFLM_ENABLED) && HEXE_MICRO_WAKE_WORD_TFLM_ENABLED
  for (size_t index = 0; index < g_engine.model_count; ++index) {
    release_runtime(g_engine.models[index]);
  }
  release_frontend();
#endif
  for (EngineModel &model : g_engine.models) {
    model.role = MicroWakeModelRole::kWake;
    model.metadata = nullptr;
    model.model_data = nullptr;
    model.model_size = 0;
    model.enabled = false;
    model.valid = false;
  }
  g_engine.initialized = false;
  g_engine.model_count = 0;
}

}  // namespace hexe::voice
