#pragma once

#include <cstddef>
#include <cstdint>

#include "voice/wake_word.h"

namespace hexe::voice {

enum class MicroWakeModelRole {
  kWake,
  kPlaybackStop,
};

struct MicroWakeModelAsset {
  MicroWakeModelRole role{MicroWakeModelRole::kWake};
  const LocalKeywordModel *metadata{nullptr};
  const uint8_t *model_data{nullptr};
  size_t model_size{0};
  bool enabled{false};
};

struct MicroWakeRuntimeDiagnostics {
  uint32_t inference_count{0};
  uint32_t detection_count{0};
  uint8_t last_probability{0};
  uint8_t last_average_probability{0};
  uint8_t last_max_probability{0};
  uint8_t best_average_probability{0};
  uint8_t last_detection_probability{0};
};

struct MicroWakeEngineStatus {
  bool tflm_linked{false};
  bool feature_frontend_linked{false};
  bool feature_frontend_ready{false};
  uint32_t feature_frame_count{0};
  bool initialized{false};
  bool wake_model_asset_available{false};
  bool stop_model_asset_available{false};
  size_t wake_model_asset_bytes{0};
  size_t stop_model_asset_bytes{0};
  bool wake_runtime_ready{false};
  bool stop_runtime_ready{false};
  size_t wake_runtime_arena_bytes{0};
  size_t stop_runtime_arena_bytes{0};
  bool wake_ready{false};
  bool stop_ready{false};
  const char *wake_reason{nullptr};
  const char *stop_reason{nullptr};
  MicroWakeRuntimeDiagnostics wake_runtime;
  MicroWakeRuntimeDiagnostics stop_runtime;
};

void init_micro_wake_engine(const MicroWakeModelAsset *models, size_t model_count);
MicroWakeEngineStatus micro_wake_engine_status();
LocalKeywordFrameDetections process_micro_wake_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking);
void reset_micro_wake_engine();

}  // namespace hexe::voice
