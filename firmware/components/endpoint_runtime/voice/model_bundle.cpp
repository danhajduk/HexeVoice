#include "voice/model_bundle.h"

#include <cstdio>
#include <cstring>

#include "board/storage.h"
#include "board_profile_pins.h"
#include "endpoint_config.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "nvs.h"

namespace {
constexpr char kTag[] = "hexe_model_bundle";
constexpr char kNamespace[] = "hexe_models";
constexpr char kActiveBankKey[] = "active_bank";
constexpr char kPreviousBankKey[] = "previous_bank";
constexpr char kBundleIdKey[] = "bundle_id";
constexpr char kVersionKey[] = "version";
constexpr char kActiveSourceKey[] = "active_source";
constexpr char kInternalSource[] = "internal_ab";
constexpr char kSdSource[] = "sd_versioned";
constexpr char kEmbeddedSource[] = "embedded";
constexpr char kEmbeddedStatus[] = "embedded_fallback";
constexpr char kActiveStatus[] = "active";
constexpr char kNoError[] = "";
constexpr char kModelApiVersion[] = "hexe-model-bundle-api-v1";

struct MutableModelBundle {
  hexe::voice::ModelBundleStorageKind storage_kind{hexe::voice::ModelBundleStorageKind::kEmbedded};
  char bank[64]{};
  char bundle_id[64]{};
  char version[32]{};
  const hexe::voice::MicroWakeModelAsset *models{nullptr};
  size_t model_count{0};
  bool tested{false};
  bool active{false};
};

hexe::voice::ModelBundleState g_state = {};
MutableModelBundle g_active_candidate = {};
char g_status[32] = "embedded_fallback";
char g_error[80] = "";
char g_active_source[24] = "embedded";
char g_active_bank[64] = "";
char g_previous_bank[64] = "";
char g_active_bundle_id[64] = "embedded";
char g_active_version[32] = "embedded";

void copy_cstr(char *target, size_t target_size, const char *value) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  std::snprintf(target, target_size, "%s", value == nullptr ? "" : value);
}

void set_error(char *target, size_t target_size, const char *code) {
  copy_cstr(target, target_size, code == nullptr ? "model_bundle_rejected" : code);
  copy_cstr(g_error, sizeof(g_error), code == nullptr ? "model_bundle_rejected" : code);
}

bool valid_bank_name(const char *bank, hexe::voice::ModelBundleStorageKind storage_kind) {
  if (bank == nullptr || bank[0] == '\0') {
    return false;
  }
  if (storage_kind == hexe::voice::ModelBundleStorageKind::kInternalBank) {
    return std::strcmp(bank, "model_a") == 0 || std::strcmp(bank, "model_b") == 0;
  }
  if (storage_kind == hexe::voice::ModelBundleStorageKind::kSdVersionedDirectory) {
    if (std::strncmp(bank, "/sdcard/hexe/models/", 20) != 0) {
      return false;
    }
    return std::strstr(bank, "/../") == nullptr && std::strstr(bank, "//") == nullptr;
  }
  return false;
}

const char *source_for_storage_kind(hexe::voice::ModelBundleStorageKind storage_kind) {
  if (storage_kind == hexe::voice::ModelBundleStorageKind::kInternalBank) {
    return kInternalSource;
  }
  if (storage_kind == hexe::voice::ModelBundleStorageKind::kSdVersionedDirectory) {
    return kSdSource;
  }
  return kEmbeddedSource;
}

const esp_partition_t *find_model_partition(const char *label) {
  return esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS, label);
}

size_t model_partition_size(const char *label) {
  const esp_partition_t *partition = find_model_partition(label);
  return partition == nullptr ? 0 : partition->size;
}

bool commit_active_bundle_pointer(
    const char *active_source,
    const char *active_bank,
    const char *previous_bank,
    const char *bundle_id,
    const char *version) {
  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kNamespace, NVS_READWRITE, &handle);
  if (err != ESP_OK) {
    copy_cstr(g_error, sizeof(g_error), esp_err_to_name(err));
    return false;
  }

  if (err == ESP_OK) err = nvs_set_str(handle, kActiveSourceKey, active_source);
  if (err == ESP_OK) err = nvs_set_str(handle, kActiveBankKey, active_bank);
  if (err == ESP_OK) err = nvs_set_str(handle, kPreviousBankKey, previous_bank);
  if (err == ESP_OK) err = nvs_set_str(handle, kBundleIdKey, bundle_id);
  if (err == ESP_OK) err = nvs_set_str(handle, kVersionKey, version);
  if (err == ESP_OK) err = nvs_commit(handle);
  nvs_close(handle);

  if (err != ESP_OK) {
    copy_cstr(g_error, sizeof(g_error), esp_err_to_name(err));
    return false;
  }
  return true;
}

void load_active_bundle_pointer() {
  nvs_handle_t handle = 0;
  const esp_err_t open_result = nvs_open(kNamespace, NVS_READONLY, &handle);
  if (open_result == ESP_ERR_NVS_NOT_FOUND) {
    return;
  }
  if (open_result != ESP_OK) {
    copy_cstr(g_error, sizeof(g_error), esp_err_to_name(open_result));
    return;
  }

  size_t length = sizeof(g_active_source);
  nvs_get_str(handle, kActiveSourceKey, g_active_source, &length);
  length = sizeof(g_active_bank);
  nvs_get_str(handle, kActiveBankKey, g_active_bank, &length);
  length = sizeof(g_previous_bank);
  nvs_get_str(handle, kPreviousBankKey, g_previous_bank, &length);
  length = sizeof(g_active_bundle_id);
  nvs_get_str(handle, kBundleIdKey, g_active_bundle_id, &length);
  length = sizeof(g_active_version);
  nvs_get_str(handle, kVersionKey, g_active_version, &length);
  nvs_close(handle);
}

void refresh_public_state() {
  const bool active_mutable_loaded = g_active_candidate.active && g_active_candidate.tested &&
                                     g_active_candidate.models != nullptr && g_active_candidate.model_count > 0;
  const bool has_active_pointer = g_active_bank[0] != '\0' && std::strcmp(g_active_source, kEmbeddedSource) != 0;
  if (active_mutable_loaded) {
    copy_cstr(g_status, sizeof(g_status), kActiveStatus);
    copy_cstr(g_error, sizeof(g_error), kNoError);
  } else if (has_active_pointer) {
    copy_cstr(g_status, sizeof(g_status), kEmbeddedStatus);
    copy_cstr(g_error, sizeof(g_error), "active_bundle_assets_not_loaded");
  } else {
    copy_cstr(g_status, sizeof(g_status), kEmbeddedStatus);
    copy_cstr(g_error, sizeof(g_error), kNoError);
    copy_cstr(g_active_source, sizeof(g_active_source), kEmbeddedSource);
    copy_cstr(g_active_bundle_id, sizeof(g_active_bundle_id), "embedded");
    copy_cstr(g_active_version, sizeof(g_active_version), "embedded");
  }

  g_state.status = g_status;
  g_state.error = g_error;
  g_state.active_source = active_mutable_loaded ? source_for_storage_kind(g_active_candidate.storage_kind) : g_active_source;
  g_state.active_bank = g_active_bank;
  g_state.previous_bank = g_previous_bank;
  g_state.active_bundle_id = g_active_bundle_id;
  g_state.active_version = g_active_version;
  g_state.embedded_fallback = !active_mutable_loaded;
  g_state.rollback_available = g_previous_bank[0] != '\0';
  g_state.staged_tested = g_active_candidate.tested;
  g_state.internal_ab_available = find_model_partition("model_a") != nullptr && find_model_partition("model_b") != nullptr;
  g_state.sd_versioned_available = hexe::board::sd_card_mounted();
  g_state.model_a_bytes = model_partition_size("model_a");
  g_state.model_b_bytes = model_partition_size("model_b");
}

bool candidate_compatible(const hexe::voice::ModelBundleCandidate &candidate, char *error_code, size_t error_code_size) {
  if (candidate.bundle_id == nullptr || candidate.bundle_id[0] == '\0' || candidate.version == nullptr ||
      candidate.version[0] == '\0') {
    set_error(error_code, error_code_size, "missing_model_bundle_identity");
    return false;
  }
  if (std::strcmp(candidate.model_api_version == nullptr ? "" : candidate.model_api_version, kModelApiVersion) != 0) {
    set_error(error_code, error_code_size, "incompatible_model_api");
    return false;
  }
  if (std::strcmp(candidate.partition_schema == nullptr ? "" : candidate.partition_schema, hexe::board::pins::kPartitionSchema) != 0) {
    set_error(error_code, error_code_size, "partition_schema_mismatch");
    return false;
  }
  if (!valid_bank_name(candidate.bank, candidate.storage_kind)) {
    set_error(error_code, error_code_size, "invalid_model_bundle_bank");
    return false;
  }
  if (candidate.storage_kind == hexe::voice::ModelBundleStorageKind::kInternalBank &&
      find_model_partition(candidate.bank) == nullptr) {
    set_error(error_code, error_code_size, "model_bank_partition_missing");
    return false;
  }
  if (candidate.storage_kind == hexe::voice::ModelBundleStorageKind::kSdVersionedDirectory &&
      !hexe::board::sd_card_mounted()) {
    set_error(error_code, error_code_size, "sd_model_bundle_storage_unavailable");
    return false;
  }
  if (candidate.models == nullptr || candidate.model_count == 0) {
    set_error(error_code, error_code_size, "model_bundle_assets_not_staged");
    return false;
  }
  return true;
}
}  // namespace

namespace hexe::voice {

void init_model_bundle_manager() {
  copy_cstr(g_status, sizeof(g_status), kEmbeddedStatus);
  copy_cstr(g_error, sizeof(g_error), kNoError);
  copy_cstr(g_active_source, sizeof(g_active_source), kEmbeddedSource);
  copy_cstr(g_active_bank, sizeof(g_active_bank), "");
  copy_cstr(g_previous_bank, sizeof(g_previous_bank), "");
  copy_cstr(g_active_bundle_id, sizeof(g_active_bundle_id), "embedded");
  copy_cstr(g_active_version, sizeof(g_active_version), "embedded");
  g_active_candidate = {};
  load_active_bundle_pointer();
  refresh_public_state();
  ESP_LOGI(
      kTag,
      "Model bundle manager initialized: source=%s bank=%s status=%s fallback=%s model_a=%u model_b=%u",
      g_state.active_source,
      g_state.active_bank,
      g_state.status,
      g_state.embedded_fallback ? "true" : "false",
      static_cast<unsigned>(g_state.model_a_bytes),
      static_cast<unsigned>(g_state.model_b_bytes));
}

const ModelBundleState &model_bundle_state() {
  refresh_public_state();
  return g_state;
}

const MicroWakeModelAsset *active_model_bundle_models(
    const MicroWakeModelAsset *embedded_models,
    size_t embedded_model_count,
    size_t *selected_model_count) {
  refresh_public_state();
  if (g_active_candidate.active && g_active_candidate.tested && g_active_candidate.models != nullptr &&
      g_active_candidate.model_count > 0) {
    if (selected_model_count != nullptr) {
      *selected_model_count = g_active_candidate.model_count;
    }
    return g_active_candidate.models;
  }
  if (selected_model_count != nullptr) {
    *selected_model_count = embedded_model_count;
  }
  return embedded_models;
}

bool activate_model_bundle_candidate(const ModelBundleCandidate &candidate, char *error_code, size_t error_code_size) {
  if (!candidate_compatible(candidate, error_code, error_code_size)) {
    refresh_public_state();
    return false;
  }
  if (!test_load_micro_wake_model_assets(candidate.models, candidate.model_count, error_code, error_code_size)) {
    copy_cstr(g_error, sizeof(g_error), (error_code == nullptr || error_code[0] == '\0') ? "model_bundle_test_load_failed" : error_code);
    refresh_public_state();
    return false;
  }

  const char *previous_bank = g_active_bank[0] == '\0' ? "" : g_active_bank;
  if (!commit_active_bundle_pointer(
          source_for_storage_kind(candidate.storage_kind),
          candidate.bank,
          previous_bank,
          candidate.bundle_id,
          candidate.version)) {
    set_error(error_code, error_code_size, "model_bundle_active_pointer_commit_failed");
    refresh_public_state();
    return false;
  }

  g_active_candidate.storage_kind = candidate.storage_kind;
  copy_cstr(g_active_candidate.bank, sizeof(g_active_candidate.bank), candidate.bank);
  copy_cstr(g_active_candidate.bundle_id, sizeof(g_active_candidate.bundle_id), candidate.bundle_id);
  copy_cstr(g_active_candidate.version, sizeof(g_active_candidate.version), candidate.version);
  g_active_candidate.models = candidate.models;
  g_active_candidate.model_count = candidate.model_count;
  g_active_candidate.tested = true;
  g_active_candidate.active = true;
  copy_cstr(g_active_source, sizeof(g_active_source), source_for_storage_kind(candidate.storage_kind));
  copy_cstr(g_active_bank, sizeof(g_active_bank), candidate.bank);
  copy_cstr(g_previous_bank, sizeof(g_previous_bank), previous_bank);
  copy_cstr(g_active_bundle_id, sizeof(g_active_bundle_id), candidate.bundle_id);
  copy_cstr(g_active_version, sizeof(g_active_version), candidate.version);
  refresh_public_state();
  ESP_LOGI(kTag, "Activated model bundle id=%s version=%s bank=%s", g_active_bundle_id, g_active_version, g_active_bank);
  return true;
}

bool rollback_model_bundle(char *error_code, size_t error_code_size) {
  if (g_previous_bank[0] == '\0') {
    set_error(error_code, error_code_size, "model_bundle_rollback_unavailable");
    refresh_public_state();
    return false;
  }
  char rollback_target[64] = {};
  copy_cstr(rollback_target, sizeof(rollback_target), g_previous_bank);
  char old_active[64] = {};
  copy_cstr(old_active, sizeof(old_active), g_active_bank);
  if (!commit_active_bundle_pointer(g_active_source, rollback_target, old_active, g_active_bundle_id, g_active_version)) {
    set_error(error_code, error_code_size, "model_bundle_rollback_commit_failed");
    refresh_public_state();
    return false;
  }
  copy_cstr(g_active_bank, sizeof(g_active_bank), rollback_target);
  copy_cstr(g_previous_bank, sizeof(g_previous_bank), old_active);
  g_active_candidate.active = false;
  g_active_candidate.tested = false;
  refresh_public_state();
  ESP_LOGW(kTag, "Rolled back model bundle pointer to %s; embedded fallback remains active until assets reload", g_active_bank);
  return true;
}

}  // namespace hexe::voice
