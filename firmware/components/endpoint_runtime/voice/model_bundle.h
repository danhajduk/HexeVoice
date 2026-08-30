#pragma once

#include <cstddef>

#include "voice/micro_wake_engine.h"

namespace hexe::voice {

enum class ModelBundleStorageKind {
  kEmbedded,
  kInternalBank,
  kSdVersionedDirectory,
};

struct ModelBundleCandidate {
  const char *bundle_id{nullptr};
  const char *version{nullptr};
  const char *bank{nullptr};
  ModelBundleStorageKind storage_kind{ModelBundleStorageKind::kInternalBank};
  const char *model_api_version{nullptr};
  const char *partition_schema{nullptr};
  const MicroWakeModelAsset *models{nullptr};
  size_t model_count{0};
};

struct ModelBundleState {
  const char *status{nullptr};
  const char *error{nullptr};
  const char *active_source{nullptr};
  const char *active_bank{nullptr};
  const char *previous_bank{nullptr};
  const char *active_bundle_id{nullptr};
  const char *active_version{nullptr};
  bool embedded_fallback{true};
  bool rollback_available{false};
  bool staged_tested{false};
  bool internal_ab_available{false};
  bool sd_versioned_available{false};
  size_t model_a_bytes{0};
  size_t model_b_bytes{0};
};

void init_model_bundle_manager();
const ModelBundleState &model_bundle_state();
const MicroWakeModelAsset *active_model_bundle_models(
    const MicroWakeModelAsset *embedded_models,
    size_t embedded_model_count,
    size_t *selected_model_count);
bool activate_model_bundle_candidate(const ModelBundleCandidate &candidate, char *error_code, size_t error_code_size);
bool rollback_model_bundle(char *error_code, size_t error_code_size);

}  // namespace hexe::voice
