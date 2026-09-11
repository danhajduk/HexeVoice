#include "voice/model_bundle.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

#include "board/storage.h"
#include "board_profile_pins.h"
#include "cJSON.h"
#include "endpoint_config.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_spiffs.h"
#include "mbedtls/md.h"
#include "nvs.h"
#include "nvs_flash.h"

namespace {
constexpr char kTag[] = "hexe_model_bundle";
constexpr char kNamespace[] = "hexe_models";
constexpr char kActiveBankKey[] = "active_bank";
constexpr char kPreviousBankKey[] = "previous_bank";
constexpr char kBundleIdKey[] = "bundle_id";
constexpr char kVersionKey[] = "version";
constexpr char kSha256Key[] = "sha256";
constexpr char kActiveSourceKey[] = "active_source";
constexpr char kFailCountKey[] = "fail_count";
constexpr char kLastErrorKey[] = "last_error";
constexpr char kInternalSource[] = "internal_ab";
constexpr char kInternalSingleSource[] = "internal_single";
constexpr char kSdSource[] = "sd_versioned";
constexpr char kEmbeddedSource[] = "embedded";
constexpr char kEmbeddedStatus[] = "embedded_fallback";
constexpr char kActiveStatus[] = "active";
constexpr char kModelErrorStatus[] = "model_error";
constexpr char kNoError[] = "";
constexpr char kModelApiVersion[] = "hexe-model-bundle-api-v1";
constexpr char kConfigPartition[] = "config";
constexpr int kModelLoadRetryLimit = 2;
constexpr char kModelBundleSchemaVersion[] = "hexe-model-bundle-v1";
constexpr char kModelBundleSecurityPolicy[] = "signed_manifest_sha256_required";
constexpr char kModelBundleSignatureAlgorithm[] = "hmac-sha256";
constexpr char kModelBundleSignatureScope[] = "canonical_manifest_without_signature";
constexpr char kInternalModelPartition[] = "model";
constexpr char kInternalModelMount[] = "/model";
constexpr char kModelManifestFilename[] = "manifest.json";
constexpr size_t kSha256HexSize = 65;
constexpr size_t kSha256BlockBytes = 64;
constexpr size_t kMaxLoadedModels = 2;

struct RuntimeModelStore {
  bool loaded{false};
  hexe::voice::ModelBundleStorageKind storage_kind{hexe::voice::ModelBundleStorageKind::kEmbedded};
  char directory[192]{};
  char bundle_id[64]{};
  char version[32]{};
  char manifest_sha256[kSha256HexSize]{};
  char ids[kMaxLoadedModels][40]{};
  char wake_words[kMaxLoadedModels][40]{};
  char aliases[kMaxLoadedModels][40]{};
  char sources[kMaxLoadedModels][64]{};
  char manifest_paths[kMaxLoadedModels][192]{};
  char tflite_paths[kMaxLoadedModels][192]{};
  char trained_languages[kMaxLoadedModels][64]{};
  char authors[kMaxLoadedModels][64]{};
  char minimum_esphome_versions[kMaxLoadedModels][32]{};
  char metadata_sha256[kMaxLoadedModels][kSha256HexSize]{};
  char tflite_sha256[kMaxLoadedModels][kSha256HexSize]{};
  uint8_t *model_data[kMaxLoadedModels]{};
  hexe::voice::LocalKeywordModel metadata[kMaxLoadedModels]{};
  hexe::voice::MicroWakeModelAsset assets[kMaxLoadedModels]{};
  size_t model_count{0};
};

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
char g_active_sha256[65] = "";
char g_cache_status[32] = "idle";
char g_cache_error[80] = "";
int g_fail_count = 0;
bool g_internal_model_mounted = false;
RuntimeModelStore g_internal_store = {};
RuntimeModelStore g_sd_store = {};

bool single_model_schema();
bool open_model_config_nvs(nvs_open_mode_t mode, nvs_handle_t *handle);

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

void free_runtime_store(RuntimeModelStore *store) {
  if (store == nullptr) {
    return;
  }
  for (size_t index = 0; index < kMaxLoadedModels; ++index) {
    if (store->model_data[index] != nullptr) {
      std::free(store->model_data[index]);
      store->model_data[index] = nullptr;
    }
  }
  *store = {};
}

void rebind_runtime_store(RuntimeModelStore *store) {
  if (store == nullptr) {
    return;
  }
  for (size_t index = 0; index < store->model_count && index < kMaxLoadedModels; ++index) {
    store->metadata[index].id = store->ids[index];
    store->metadata[index].wake_word = store->wake_words[index];
    store->metadata[index].alias = store->aliases[index][0] == '\0' ? nullptr : store->aliases[index];
    store->metadata[index].source = store->sources[index];
    store->metadata[index].manifest_url = store->manifest_paths[index];
    store->metadata[index].tflite_url = store->tflite_paths[index];
    store->metadata[index].trained_languages = store->trained_languages[index];
    store->metadata[index].author = store->authors[index];
    store->metadata[index].minimum_esphome_version = store->minimum_esphome_versions[index];
    store->metadata[index].manifest_sha256 = store->metadata_sha256[index];
    store->metadata[index].tflite_sha256 = store->tflite_sha256[index];
    store->assets[index].metadata = &store->metadata[index];
    store->assets[index].model_data = store->model_data[index];
  }
}

bool string_contains(const char *value, const char *needle) {
  return value != nullptr && needle != nullptr && std::strstr(value, needle) != nullptr;
}

bool model_policy_allows_embedded_fallback() {
  return string_contains(hexe::board::pins::kStorageModelPolicy, "embedded_fallback");
}

bool model_policy_allows_sd_model_sets() {
  return string_contains(hexe::board::pins::kStorageModelPolicy, "sd") ||
         string_contains(hexe::board::pins::kStorageModelPolicy, "model_set");
}

bool model_policy_uses_internal_single_cache() {
  return single_model_schema() ||
         string_contains(hexe::board::pins::kStorageModelPolicy, "internal_single") ||
         string_contains(hexe::board::pins::kStorageModelPolicy, "single_model_cache");
}

bool valid_hex_sha256(const char *value) {
  if (value == nullptr || std::strlen(value) != 64) {
    return false;
  }
  for (size_t index = 0; index < 64; ++index) {
    const char ch = value[index];
    const bool digit = ch >= '0' && ch <= '9';
    const bool lower = ch >= 'a' && ch <= 'f';
    if (!digit && !lower) {
      return false;
    }
  }
  return true;
}

void bytes_to_hex(const unsigned char *bytes, size_t length, char *target, size_t target_size) {
  static constexpr char kHex[] = "0123456789abcdef";
  if (target == nullptr || target_size < length * 2 + 1) {
    return;
  }
  for (size_t index = 0; index < length; ++index) {
    target[index * 2] = kHex[(bytes[index] >> 4) & 0x0f];
    target[index * 2 + 1] = kHex[bytes[index] & 0x0f];
  }
  target[length * 2] = '\0';
}

bool constant_time_equal(const char *left, const char *right) {
  if (left == nullptr || right == nullptr) {
    return false;
  }
  const size_t left_len = std::strlen(left);
  const size_t right_len = std::strlen(right);
  if (left_len != right_len) {
    return false;
  }
  unsigned char diff = 0;
  for (size_t index = 0; index < left_len; ++index) {
    diff |= static_cast<unsigned char>(left[index] ^ right[index]);
  }
  return diff == 0;
}

bool sha256_file(const char *path, char *target, size_t target_size, size_t *size_bytes = nullptr) {
  if (path == nullptr || target == nullptr || target_size < kSha256HexSize) {
    return false;
  }
  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    return false;
  }
  const mbedtls_md_info_t *md_info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (md_info == nullptr) {
    std::fclose(file);
    return false;
  }
  mbedtls_md_context_t context;
  mbedtls_md_init(&context);
  bool ok = mbedtls_md_setup(&context, md_info, 0) == 0 && mbedtls_md_starts(&context) == 0;
  unsigned char buffer[1024] = {};
  size_t total = 0;
  while (ok) {
    const size_t read = std::fread(buffer, 1, sizeof(buffer), file);
    if (read > 0) {
      total += read;
      ok = mbedtls_md_update(&context, buffer, read) == 0;
    }
    if (read < sizeof(buffer)) {
      if (std::ferror(file)) {
        ok = false;
      }
      break;
    }
  }
  unsigned char digest[32] = {};
  if (ok) {
    ok = mbedtls_md_finish(&context, digest) == 0;
  }
  mbedtls_md_free(&context);
  std::fclose(file);
  if (!ok) {
    return false;
  }
  bytes_to_hex(digest, sizeof(digest), target, target_size);
  if (size_bytes != nullptr) {
    *size_bytes = total;
  }
  return true;
}

bool safe_relative_asset_path(const char *path) {
  if (path == nullptr || path[0] == '\0' || path[0] == '/') {
    return false;
  }
  return std::strstr(path, "..") == nullptr && std::strstr(path, "//") == nullptr;
}

bool join_path(char *target, size_t target_size, const char *base, const char *relative) {
  if (target == nullptr || target_size == 0 || base == nullptr || relative == nullptr) {
    return false;
  }
  if (!safe_relative_asset_path(relative)) {
    return false;
  }
  const int written = std::snprintf(target, target_size, "%s/%s", base, relative);
  return written > 0 && static_cast<size_t>(written) < target_size;
}

bool load_file_to_memory(const char *path, uint8_t **data, size_t *size) {
  if (path == nullptr || data == nullptr || size == nullptr) {
    return false;
  }
  *data = nullptr;
  *size = 0;
  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    return false;
  }
  if (std::fseek(file, 0, SEEK_END) != 0) {
    std::fclose(file);
    return false;
  }
  const long length = std::ftell(file);
  if (length <= 0 || std::fseek(file, 0, SEEK_SET) != 0) {
    std::fclose(file);
    return false;
  }
  uint8_t *buffer = static_cast<uint8_t *>(heap_caps_malloc(static_cast<size_t>(length), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (buffer == nullptr) {
    buffer = static_cast<uint8_t *>(std::malloc(static_cast<size_t>(length)));
  }
  if (buffer == nullptr) {
    std::fclose(file);
    return false;
  }
  const size_t read = std::fread(buffer, 1, static_cast<size_t>(length), file);
  std::fclose(file);
  if (read != static_cast<size_t>(length)) {
    std::free(buffer);
    return false;
  }
  *data = buffer;
  *size = read;
  return true;
}

std::string json_escaped_string(const char *value) {
  cJSON *string = cJSON_CreateString(value == nullptr ? "" : value);
  char *printed = cJSON_PrintUnformatted(string);
  cJSON_Delete(string);
  if (printed == nullptr) {
    return "\"\"";
  }
  std::string result(printed);
  cJSON_free(printed);
  return result;
}

std::string canonical_json_without_signature(const cJSON *item, bool skip_top_level_signature = true) {
  if (item == nullptr) {
    return "null";
  }
  if (cJSON_IsObject(item)) {
    std::vector<const cJSON *> children;
    for (const cJSON *child = item->child; child != nullptr; child = child->next) {
      if (skip_top_level_signature && child->string != nullptr && std::strcmp(child->string, "signature") == 0) {
        continue;
      }
      children.push_back(child);
    }
    std::sort(children.begin(), children.end(), [](const cJSON *left, const cJSON *right) {
      return std::strcmp(left->string == nullptr ? "" : left->string, right->string == nullptr ? "" : right->string) < 0;
    });
    std::string result = "{";
    for (size_t index = 0; index < children.size(); ++index) {
      if (index > 0) {
        result.push_back(',');
      }
      result += json_escaped_string(children[index]->string == nullptr ? "" : children[index]->string);
      result.push_back(':');
      result += canonical_json_without_signature(children[index], false);
    }
    result.push_back('}');
    return result;
  }
  if (cJSON_IsArray(item)) {
    std::string result = "[";
    size_t index = 0;
    for (const cJSON *child = item->child; child != nullptr; child = child->next) {
      if (index++ > 0) {
        result.push_back(',');
      }
      result += canonical_json_without_signature(child, false);
    }
    result.push_back(']');
    return result;
  }
  char *printed = cJSON_PrintUnformatted(item);
  if (printed == nullptr) {
    return "null";
  }
  std::string result(printed);
  cJSON_free(printed);
  return result;
}

bool calculate_manifest_hmac(const cJSON *manifest, char *target, size_t target_size) {
  if (target == nullptr || target_size < kSha256HexSize) {
    return false;
  }
  const mbedtls_md_info_t *md_info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (md_info == nullptr) {
    return false;
  }
  const std::string payload = canonical_json_without_signature(manifest);
  unsigned char digest[32] = {};
  const unsigned char *key = reinterpret_cast<const unsigned char *>(hexe::config::kModelBundleManifestSigningKey);
  size_t key_len = std::strlen(hexe::config::kModelBundleManifestSigningKey);
  unsigned char key_block[kSha256BlockBytes] = {};
  if (key_len > kSha256BlockBytes) {
    if (mbedtls_md(md_info, key, key_len, key_block) != 0) {
      return false;
    }
    key_len = 32;
  } else {
    std::memcpy(key_block, key, key_len);
  }
  unsigned char inner_pad[kSha256BlockBytes] = {};
  unsigned char outer_pad[kSha256BlockBytes] = {};
  for (size_t index = 0; index < kSha256BlockBytes; ++index) {
    inner_pad[index] = key_block[index] ^ 0x36;
    outer_pad[index] = key_block[index] ^ 0x5c;
  }

  std::string inner(reinterpret_cast<const char *>(inner_pad), sizeof(inner_pad));
  inner.append(payload);
  unsigned char inner_digest[32] = {};
  if (mbedtls_md(
          md_info,
          reinterpret_cast<const unsigned char *>(inner.data()),
          inner.size(),
          inner_digest) != 0) {
    return false;
  }
  std::string outer(reinterpret_cast<const char *>(outer_pad), sizeof(outer_pad));
  outer.append(reinterpret_cast<const char *>(inner_digest), sizeof(inner_digest));
  if (mbedtls_md(
          md_info,
          reinterpret_cast<const unsigned char *>(outer.data()),
          outer.size(),
          digest) != 0) {
    return false;
  }
  bytes_to_hex(digest, sizeof(digest), target, target_size);
  return true;
}

bool valid_bank_name(const char *bank, hexe::voice::ModelBundleStorageKind storage_kind) {
  if (bank == nullptr || bank[0] == '\0') {
    return false;
  }
  if (storage_kind == hexe::voice::ModelBundleStorageKind::kInternalBank) {
    return std::strcmp(bank, "model_a") == 0 || std::strcmp(bank, "model_b") == 0 ||
           std::strcmp(bank, "model") == 0;
  }
  if (storage_kind == hexe::voice::ModelBundleStorageKind::kSdVersionedDirectory) {
    constexpr char kModelSetsRoot[] = "/sdcard/hexe/model_sets/";
    if (std::strncmp(bank, kModelSetsRoot, sizeof(kModelSetsRoot) - 1) != 0) {
      return false;
    }
    return std::strstr(bank, "/../") == nullptr && std::strstr(bank, "//") == nullptr;
  }
  return false;
}

const char *source_for_storage_kind(hexe::voice::ModelBundleStorageKind storage_kind) {
  if (storage_kind == hexe::voice::ModelBundleStorageKind::kInternalBank) {
    return single_model_schema() ? kInternalSingleSource : kInternalSource;
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

bool single_model_schema() {
  return std::strcmp(hexe::board::pins::kPartitionSchema, "s3-16m-recovery-single-model-v1") == 0;
}

bool open_model_config_nvs(nvs_open_mode_t mode, nvs_handle_t *handle) {
  if (handle == nullptr) {
    return false;
  }
  esp_err_t err = nvs_flash_init_partition(kConfigPartition);
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_LOGW(kTag, "Model config partition unavailable: %s", esp_err_to_name(err));
    return false;
  }
  err = nvs_open_from_partition(kConfigPartition, kNamespace, mode, handle);
  if (err == ESP_ERR_NVS_NOT_FOUND && mode == NVS_READONLY) {
    return false;
  }
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to open model config partition: %s", esp_err_to_name(err));
    return false;
  }
  return true;
}

void load_model_config_metadata() {
  nvs_handle_t handle = 0;
  if (!open_model_config_nvs(NVS_READONLY, &handle)) {
    return;
  }
  int32_t persisted_fail_count = 0;
  if (nvs_get_i32(handle, kFailCountKey, &persisted_fail_count) == ESP_OK) {
    g_fail_count = static_cast<int>(persisted_fail_count);
  }
  size_t length = sizeof(g_error);
  nvs_get_str(handle, kLastErrorKey, g_error, &length);
  nvs_close(handle);
}

void persist_model_config_metadata(const char *last_error) {
  nvs_handle_t handle = 0;
  if (!open_model_config_nvs(NVS_READWRITE, &handle)) {
    return;
  }
  esp_err_t err = nvs_set_i32(handle, kFailCountKey, g_fail_count);
  if (err == ESP_OK && last_error != nullptr) {
    err = nvs_set_str(handle, kLastErrorKey, last_error);
  }
  if (err == ESP_OK) {
    err = nvs_commit(handle);
  }
  nvs_close(handle);
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to persist model metadata: %s", esp_err_to_name(err));
  }
}

void record_model_load_failure(const char *error_code) {
  if (g_fail_count < kModelLoadRetryLimit) {
    ++g_fail_count;
  }
  copy_cstr(g_error, sizeof(g_error), error_code);
  persist_model_config_metadata(g_error);
}

bool commit_active_bundle_pointer(
    const char *active_source,
    const char *active_bank,
    const char *previous_bank,
    const char *bundle_id,
    const char *version,
    const char *sha256) {
  nvs_handle_t handle = 0;
  if (!open_model_config_nvs(NVS_READWRITE, &handle)) {
    copy_cstr(g_error, sizeof(g_error), "model_config_unavailable");
    return false;
  }

  esp_err_t err = ESP_OK;
  if (err == ESP_OK) err = nvs_set_str(handle, kActiveSourceKey, active_source);
  if (err == ESP_OK) err = nvs_set_str(handle, kActiveBankKey, active_bank);
  if (err == ESP_OK) err = nvs_set_str(handle, kPreviousBankKey, previous_bank);
  if (err == ESP_OK) err = nvs_set_str(handle, kBundleIdKey, bundle_id);
  if (err == ESP_OK) err = nvs_set_str(handle, kVersionKey, version);
  if (err == ESP_OK) err = nvs_set_str(handle, kSha256Key, sha256 == nullptr ? "" : sha256);
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
  if (!open_model_config_nvs(NVS_READONLY, &handle)) {
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
  length = sizeof(g_active_sha256);
  nvs_get_str(handle, kSha256Key, g_active_sha256, &length);
  nvs_close(handle);
}

const char *json_string(const cJSON *object, const char *field) {
  const cJSON *item = cJSON_GetObjectItem(object, field);
  return cJSON_IsString(item) ? item->valuestring : nullptr;
}

bool json_string_equals(const cJSON *object, const char *field, const char *expected) {
  const char *value = json_string(object, field);
  return value != nullptr && std::strcmp(value, expected) == 0;
}

bool json_string_array_contains(const cJSON *array, const char *needle) {
  if (!cJSON_IsArray(array) || needle == nullptr) {
    return false;
  }
  const cJSON *item = nullptr;
  cJSON_ArrayForEach(item, array) {
    if (cJSON_IsString(item) && std::strcmp(item->valuestring, needle) == 0) {
      return true;
    }
  }
  return false;
}

bool manifest_compatible(const cJSON *manifest, char *error_code, size_t error_code_size) {
  if (!json_string_equals(manifest, "schema_version", kModelBundleSchemaVersion) ||
      !json_string_equals(manifest, "model_api_version", kModelApiVersion) ||
      !json_string_equals(manifest, "security_policy", kModelBundleSecurityPolicy)) {
    set_error(error_code, error_code_size, "invalid_model_bundle_manifest");
    return false;
  }
  const cJSON *compatibility = cJSON_GetObjectItem(manifest, "compatibility");
  if (!cJSON_IsObject(compatibility)) {
    set_error(error_code, error_code_size, "missing_model_bundle_compatibility");
    return false;
  }
  if (!json_string_array_contains(cJSON_GetObjectItem(compatibility, "board_profiles"), hexe::board::pins::kBoardProfile)) {
    set_error(error_code, error_code_size, "model_bundle_board_mismatch");
    return false;
  }
  if (!json_string_array_contains(cJSON_GetObjectItem(compatibility, "partition_schemas"), hexe::board::pins::kPartitionSchema)) {
    set_error(error_code, error_code_size, "model_bundle_partition_schema_mismatch");
    return false;
  }
  const cJSON *required_partitions = cJSON_GetObjectItem(compatibility, "requires_partitions");
  if (single_model_schema()) {
    if (!json_string_array_contains(required_partitions, kInternalModelPartition)) {
      set_error(error_code, error_code_size, "model_bundle_requires_wrong_partition");
      return false;
    }
  } else if (!json_string_array_contains(required_partitions, "model_a") ||
             !json_string_array_contains(required_partitions, "model_b")) {
    set_error(error_code, error_code_size, "model_bundle_requires_wrong_partition");
    return false;
  }
  return true;
}

bool verify_manifest_signature(const cJSON *manifest, char *error_code, size_t error_code_size) {
  const cJSON *signature = cJSON_GetObjectItem(manifest, "signature");
  if (!cJSON_IsObject(signature)) {
    set_error(error_code, error_code_size, "missing_model_bundle_signature");
    return false;
  }
  const char *key_id = json_string(signature, "key_id");
  const char *value = json_string(signature, "value");
  if (!json_string_equals(signature, "algorithm", kModelBundleSignatureAlgorithm) ||
      !json_string_equals(signature, "scope", kModelBundleSignatureScope) ||
      key_id == nullptr || std::strcmp(key_id, hexe::config::kModelBundleManifestKeyId) != 0 ||
      !valid_hex_sha256(value)) {
    set_error(error_code, error_code_size, "invalid_model_bundle_signature");
    return false;
  }
  char expected[kSha256HexSize] = {};
  if (!calculate_manifest_hmac(manifest, expected, sizeof(expected)) || !constant_time_equal(value, expected)) {
    set_error(error_code, error_code_size, "model_bundle_signature_mismatch");
    return false;
  }
  return true;
}

bool verify_asset_digest(const char *directory, const cJSON *asset, char *resolved_path, size_t resolved_path_size, char *error_code, size_t error_code_size) {
  if (!cJSON_IsObject(asset)) {
    set_error(error_code, error_code_size, "missing_model_bundle_asset");
    return false;
  }
  const char *relative_path = json_string(asset, "path");
  const char *expected_sha256 = json_string(asset, "sha256");
  const cJSON *size_item = cJSON_GetObjectItem(asset, "size_bytes");
  if (!safe_relative_asset_path(relative_path) || !valid_hex_sha256(expected_sha256) || !cJSON_IsNumber(size_item) ||
      size_item->valueint <= 0 || !join_path(resolved_path, resolved_path_size, directory, relative_path)) {
    set_error(error_code, error_code_size, "invalid_model_bundle_asset");
    return false;
  }
  char actual_sha256[kSha256HexSize] = {};
  size_t actual_size = 0;
  if (!sha256_file(resolved_path, actual_sha256, sizeof(actual_sha256), &actual_size)) {
    set_error(error_code, error_code_size, "model_bundle_asset_unreadable");
    return false;
  }
  if (actual_size != static_cast<size_t>(size_item->valueint) || !constant_time_equal(actual_sha256, expected_sha256)) {
    set_error(error_code, error_code_size, "model_bundle_asset_hash_mismatch");
    return false;
  }
  return true;
}

bool verify_hash_manifest_files(const char *directory, const cJSON *manifest, char *error_code, size_t error_code_size) {
  const cJSON *hashes = cJSON_GetObjectItem(manifest, "hashes");
  if (!cJSON_IsArray(hashes) || cJSON_GetArraySize(hashes) == 0) {
    set_error(error_code, error_code_size, "missing_model_bundle_hashes");
    return false;
  }
  const cJSON *asset = nullptr;
  cJSON_ArrayForEach(asset, hashes) {
    char resolved_path[256] = {};
    if (!verify_asset_digest(directory, asset, resolved_path, sizeof(resolved_path), error_code, error_code_size)) {
      return false;
    }
  }
  return true;
}

void copy_language_list(const cJSON *languages, char *target, size_t target_size) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  target[0] = '\0';
  if (!cJSON_IsArray(languages)) {
    return;
  }
  const cJSON *item = nullptr;
  cJSON_ArrayForEach(item, languages) {
    if (!cJSON_IsString(item)) {
      continue;
    }
    if (target[0] != '\0') {
      std::strncat(target, ",", target_size - std::strlen(target) - 1);
    }
    std::strncat(target, item->valuestring, target_size - std::strlen(target) - 1);
  }
}

bool load_model_set_from_directory(
    const char *directory,
    hexe::voice::ModelBundleStorageKind storage_kind,
    RuntimeModelStore *store,
    char *error_code,
    size_t error_code_size) {
  if (directory == nullptr || directory[0] == '\0' || store == nullptr) {
    set_error(error_code, error_code_size, "invalid_model_bundle_directory");
    return false;
  }
  char manifest_path[256] = {};
  if (!join_path(manifest_path, sizeof(manifest_path), directory, kModelManifestFilename)) {
    set_error(error_code, error_code_size, "invalid_model_bundle_manifest_path");
    return false;
  }
  char *manifest_bytes = nullptr;
  size_t manifest_size = 0;
  if (!load_file_to_memory(manifest_path, reinterpret_cast<uint8_t **>(&manifest_bytes), &manifest_size)) {
    set_error(error_code, error_code_size, "missing_model_bundle_manifest");
    return false;
  }
  cJSON *manifest = cJSON_ParseWithLength(manifest_bytes, manifest_size);
  std::free(manifest_bytes);
  if (manifest == nullptr) {
    set_error(error_code, error_code_size, "invalid_model_bundle_json");
    return false;
  }

  bool ok = manifest_compatible(manifest, error_code, error_code_size) &&
            verify_manifest_signature(manifest, error_code, error_code_size) &&
            verify_hash_manifest_files(directory, manifest, error_code, error_code_size);
  if (!ok) {
    cJSON_Delete(manifest);
    return false;
  }

  RuntimeModelStore loaded = {};
  loaded.storage_kind = storage_kind;
  copy_cstr(loaded.directory, sizeof(loaded.directory), directory);
  copy_cstr(loaded.bundle_id, sizeof(loaded.bundle_id), json_string(manifest, "bundle_id"));
  copy_cstr(loaded.version, sizeof(loaded.version), json_string(manifest, "version"));
  sha256_file(manifest_path, loaded.manifest_sha256, sizeof(loaded.manifest_sha256));

  const cJSON *models = cJSON_GetObjectItem(manifest, "models");
  if (!cJSON_IsArray(models)) {
    cJSON_Delete(manifest);
    set_error(error_code, error_code_size, "missing_model_bundle_models");
    return false;
  }
  const cJSON *model = nullptr;
  cJSON_ArrayForEach(model, models) {
    if (loaded.model_count >= kMaxLoadedModels) {
      break;
    }
    const char *role = json_string(model, "role");
    if (!cJSON_IsObject(model) || role == nullptr) {
      continue;
    }
    const size_t index = loaded.model_count;
    const cJSON *metadata_asset = cJSON_GetObjectItem(model, "metadata");
    const cJSON *model_asset = cJSON_GetObjectItem(model, "model");
    if (!verify_asset_digest(directory, metadata_asset, loaded.manifest_paths[index], sizeof(loaded.manifest_paths[index]), error_code, error_code_size) ||
        !verify_asset_digest(directory, model_asset, loaded.tflite_paths[index], sizeof(loaded.tflite_paths[index]), error_code, error_code_size) ||
        !load_file_to_memory(loaded.tflite_paths[index], &loaded.model_data[index], &loaded.assets[index].model_size)) {
      free_runtime_store(&loaded);
      cJSON_Delete(manifest);
      if (error_code != nullptr && error_code[0] == '\0') {
        set_error(error_code, error_code_size, "model_bundle_asset_load_failed");
      }
      return false;
    }
    copy_cstr(loaded.ids[index], sizeof(loaded.ids[index]), json_string(model, "id"));
    copy_cstr(loaded.wake_words[index], sizeof(loaded.wake_words[index]), json_string(model, "wake_word"));
    copy_cstr(loaded.aliases[index], sizeof(loaded.aliases[index]), json_string(model, "alias"));
    copy_cstr(loaded.sources[index], sizeof(loaded.sources[index]), json_string(model, "source"));
    copy_language_list(cJSON_GetObjectItem(model, "trained_languages"), loaded.trained_languages[index], sizeof(loaded.trained_languages[index]));
    copy_cstr(loaded.authors[index], sizeof(loaded.authors[index]), json_string(model, "author"));
    copy_cstr(loaded.minimum_esphome_versions[index], sizeof(loaded.minimum_esphome_versions[index]), json_string(model, "minimum_esphome_version"));
    copy_cstr(loaded.metadata_sha256[index], sizeof(loaded.metadata_sha256[index]), json_string(metadata_asset, "sha256"));
    copy_cstr(loaded.tflite_sha256[index], sizeof(loaded.tflite_sha256[index]), json_string(model_asset, "sha256"));

    const cJSON *thresholds = cJSON_GetObjectItem(model, "thresholds");
    loaded.metadata[index].id = loaded.ids[index];
    loaded.metadata[index].wake_word = loaded.wake_words[index];
    loaded.metadata[index].alias = loaded.aliases[index][0] == '\0' ? nullptr : loaded.aliases[index];
    loaded.metadata[index].source = loaded.sources[index];
    loaded.metadata[index].manifest_url = loaded.manifest_paths[index];
    loaded.metadata[index].tflite_url = loaded.tflite_paths[index];
    loaded.metadata[index].trained_languages = loaded.trained_languages[index];
    loaded.metadata[index].author = loaded.authors[index];
    loaded.metadata[index].minimum_esphome_version = loaded.minimum_esphome_versions[index];
    loaded.metadata[index].manifest_sha256 = loaded.metadata_sha256[index];
    loaded.metadata[index].tflite_sha256 = loaded.tflite_sha256[index];
    loaded.metadata[index].model_version = cJSON_IsNumber(cJSON_GetObjectItem(model, "upstream_version"))
                                               ? cJSON_GetObjectItem(model, "upstream_version")->valueint
                                               : 0;
    loaded.metadata[index].probability_cutoff = cJSON_IsNumber(cJSON_GetObjectItem(thresholds, "probability_cutoff"))
                                                    ? static_cast<float>(cJSON_GetObjectItem(thresholds, "probability_cutoff")->valuedouble)
                                                    : 0.0f;
    loaded.metadata[index].sliding_window_size = cJSON_IsNumber(cJSON_GetObjectItem(thresholds, "sliding_window_size"))
                                                     ? cJSON_GetObjectItem(thresholds, "sliding_window_size")->valueint
                                                     : 0;
    loaded.metadata[index].feature_step_size_ms = cJSON_IsNumber(cJSON_GetObjectItem(thresholds, "feature_step_size_ms"))
                                                      ? cJSON_GetObjectItem(thresholds, "feature_step_size_ms")->valueint
                                                      : 0;
    loaded.metadata[index].tensor_arena_size = cJSON_IsNumber(cJSON_GetObjectItem(thresholds, "tensor_arena_size"))
                                                   ? cJSON_GetObjectItem(thresholds, "tensor_arena_size")->valueint
                                                   : 0;
    loaded.assets[index].role = std::strcmp(role, "playback_stop") == 0 ? hexe::voice::MicroWakeModelRole::kPlaybackStop
                                                                         : hexe::voice::MicroWakeModelRole::kWake;
    loaded.assets[index].metadata = &loaded.metadata[index];
    loaded.assets[index].model_data = loaded.model_data[index];
    loaded.assets[index].enabled = true;
    ++loaded.model_count;
  }
  cJSON_Delete(manifest);
  if (loaded.bundle_id[0] == '\0' || loaded.version[0] == '\0' || loaded.model_count == 0) {
    free_runtime_store(&loaded);
    set_error(error_code, error_code_size, "incomplete_model_bundle_manifest");
    return false;
  }
  if (!test_load_micro_wake_model_assets(loaded.assets, loaded.model_count, error_code, error_code_size)) {
    free_runtime_store(&loaded);
    return false;
  }
  free_runtime_store(store);
  *store = loaded;
  rebind_runtime_store(store);
  store->loaded = true;
  if (error_code != nullptr && error_code_size > 0) {
    error_code[0] = '\0';
  }
  return true;
}

bool mount_internal_model_partition(bool format_if_mount_failed) {
  if (!single_model_schema() && find_model_partition(kInternalModelPartition) == nullptr) {
    return false;
  }
  if (g_internal_model_mounted) {
    return true;
  }
  esp_vfs_spiffs_conf_t conf = {};
  conf.base_path = kInternalModelMount;
  conf.partition_label = kInternalModelPartition;
  conf.max_files = 8;
  conf.format_if_mount_failed = format_if_mount_failed;
  esp_err_t err = esp_vfs_spiffs_register(&conf);
  if (err == ESP_ERR_INVALID_STATE || err == ESP_OK) {
    g_internal_model_mounted = true;
    return true;
  }
  ESP_LOGW(kTag, "Failed to mount internal model partition: %s", esp_err_to_name(err));
  return false;
}

bool copy_file(const char *source, const char *target) {
  FILE *src = std::fopen(source, "rb");
  if (src == nullptr) {
    return false;
  }
  FILE *dst = std::fopen(target, "wb");
  if (dst == nullptr) {
    std::fclose(src);
    return false;
  }
  unsigned char buffer[1024] = {};
  bool ok = true;
  while (ok) {
    const size_t read = std::fread(buffer, 1, sizeof(buffer), src);
    if (read > 0 && std::fwrite(buffer, 1, read, dst) != read) {
      ok = false;
    }
    if (read < sizeof(buffer)) {
      if (std::ferror(src)) {
        ok = false;
      }
      break;
    }
  }
  if (std::fclose(dst) != 0) {
    ok = false;
  }
  std::fclose(src);
  return ok;
}

bool copy_manifest_assets_to_internal_cache(const char *source_directory, char *error_code, size_t error_code_size) {
  if (!mount_internal_model_partition(true)) {
    set_error(error_code, error_code_size, "internal_model_partition_unavailable");
    return false;
  }
  char manifest_path[256] = {};
  if (!join_path(manifest_path, sizeof(manifest_path), source_directory, kModelManifestFilename)) {
    set_error(error_code, error_code_size, "invalid_model_bundle_manifest_path");
    return false;
  }
  char *manifest_bytes = nullptr;
  size_t manifest_size = 0;
  if (!load_file_to_memory(manifest_path, reinterpret_cast<uint8_t **>(&manifest_bytes), &manifest_size)) {
    set_error(error_code, error_code_size, "missing_model_bundle_manifest");
    return false;
  }
  cJSON *manifest = cJSON_ParseWithLength(manifest_bytes, manifest_size);
  std::free(manifest_bytes);
  if (manifest == nullptr) {
    set_error(error_code, error_code_size, "invalid_model_bundle_json");
    return false;
  }
  const cJSON *hashes = cJSON_GetObjectItem(manifest, "hashes");
  const cJSON *asset = nullptr;
  cJSON_ArrayForEach(asset, hashes) {
    const char *relative_path = json_string(asset, "path");
    if (!safe_relative_asset_path(relative_path)) {
      cJSON_Delete(manifest);
      set_error(error_code, error_code_size, "invalid_model_bundle_asset");
      return false;
    }
    char source_path[256] = {};
    char target_path[256] = {};
    if (!join_path(source_path, sizeof(source_path), source_directory, relative_path) ||
        !join_path(target_path, sizeof(target_path), kInternalModelMount, relative_path) ||
        !copy_file(source_path, target_path)) {
      cJSON_Delete(manifest);
      set_error(error_code, error_code_size, "internal_model_cache_copy_failed");
      return false;
    }
  }
  char internal_manifest[256] = {};
  if (!join_path(internal_manifest, sizeof(internal_manifest), kInternalModelMount, kModelManifestFilename) ||
      !copy_file(manifest_path, internal_manifest)) {
    cJSON_Delete(manifest);
    set_error(error_code, error_code_size, "internal_model_cache_manifest_copy_failed");
    return false;
  }
  cJSON_Delete(manifest);
  copy_cstr(g_cache_status, sizeof(g_cache_status), "cached");
  copy_cstr(g_cache_error, sizeof(g_cache_error), kNoError);
  return true;
}

bool activate_loaded_store(RuntimeModelStore *store, char *error_code, size_t error_code_size) {
  if (store == nullptr || !store->loaded) {
    set_error(error_code, error_code_size, "model_bundle_assets_not_staged");
    return false;
  }
  hexe::voice::ModelBundleCandidate candidate = {};
  candidate.bundle_id = store->bundle_id;
  candidate.version = store->version;
  candidate.bank = store->storage_kind == hexe::voice::ModelBundleStorageKind::kInternalBank && single_model_schema()
                       ? kInternalModelPartition
                       : store->directory;
  candidate.storage_kind = store->storage_kind;
  candidate.model_api_version = kModelApiVersion;
  candidate.partition_schema = hexe::board::pins::kPartitionSchema;
  candidate.bundle_sha256 = store->manifest_sha256;
  candidate.models = store->assets;
  candidate.model_count = store->model_count;
  return hexe::voice::activate_model_bundle_candidate(candidate, error_code, error_code_size);
}

bool load_internal_single_model_cache(char *error_code, size_t error_code_size) {
  if (!model_policy_uses_internal_single_cache() || find_model_partition(kInternalModelPartition) == nullptr ||
      !mount_internal_model_partition(false)) {
    set_error(error_code, error_code_size, "single_model_cache_not_loaded");
    return false;
  }
  return load_model_set_from_directory(
      kInternalModelMount,
      hexe::voice::ModelBundleStorageKind::kInternalBank,
      &g_internal_store,
      error_code,
      error_code_size) &&
         activate_loaded_store(&g_internal_store, error_code, error_code_size);
}

bool discover_sd_model_set(char *selected_directory, size_t selected_directory_size, char *error_code, size_t error_code_size) {
  if (!model_policy_allows_sd_model_sets() || !hexe::board::sd_card_mounted() ||
      hexe::board::sd_card_model_sets_path()[0] == '\0') {
    set_error(error_code, error_code_size, "sd_model_bundle_storage_unavailable");
    return false;
  }
  DIR *dir = opendir(hexe::board::sd_card_model_sets_path());
  if (dir == nullptr) {
    set_error(error_code, error_code_size, "sd_model_sets_unreadable");
    return false;
  }
  std::vector<std::string> candidates;
  struct dirent *entry = nullptr;
  while ((entry = readdir(dir)) != nullptr) {
    if (entry->d_name[0] == '.') {
      continue;
    }
    char path[256] = {};
    const int written = std::snprintf(path, sizeof(path), "%s/%s", hexe::board::sd_card_model_sets_path(), entry->d_name);
    if (written <= 0 || static_cast<size_t>(written) >= sizeof(path)) {
      continue;
    }
    struct stat st = {};
    if (stat(path, &st) == 0 && S_ISDIR(st.st_mode)) {
      candidates.emplace_back(path);
    }
  }
  closedir(dir);
  std::sort(candidates.begin(), candidates.end());
  for (const std::string &candidate : candidates) {
    char local_error[80] = {};
    if (load_model_set_from_directory(
            candidate.c_str(),
            hexe::voice::ModelBundleStorageKind::kSdVersionedDirectory,
            &g_sd_store,
            local_error,
            sizeof(local_error))) {
      copy_cstr(selected_directory, selected_directory_size, candidate.c_str());
      return true;
    }
    ESP_LOGW(kTag, "Rejected SD model set %s: %s", candidate.c_str(), local_error);
    copy_cstr(g_cache_error, sizeof(g_cache_error), local_error);
  }
  set_error(error_code, error_code_size, g_cache_error[0] == '\0' ? "no_valid_sd_model_set" : g_cache_error);
  return false;
}

bool load_and_cache_sd_model_set(char *error_code, size_t error_code_size) {
  char selected_directory[192] = {};
  if (!discover_sd_model_set(selected_directory, sizeof(selected_directory), error_code, error_code_size)) {
    return false;
  }
  copy_cstr(g_cache_status, sizeof(g_cache_status), "verified_sd");
  if (single_model_schema() && find_model_partition(kInternalModelPartition) != nullptr) {
    if (copy_manifest_assets_to_internal_cache(selected_directory, error_code, error_code_size) &&
        load_internal_single_model_cache(error_code, error_code_size)) {
      return true;
    }
    copy_cstr(g_cache_status, sizeof(g_cache_status), "cache_failed_sd_active");
    copy_cstr(g_cache_error, sizeof(g_cache_error), error_code == nullptr ? "internal_model_cache_failed" : error_code);
  }
  return activate_loaded_store(&g_sd_store, error_code, error_code_size);
}

void refresh_public_state() {
  const bool active_mutable_loaded = g_active_candidate.active && g_active_candidate.tested &&
                                     g_active_candidate.models != nullptr && g_active_candidate.model_count > 0;
  const bool has_active_pointer = g_active_bank[0] != '\0' && std::strcmp(g_active_source, kEmbeddedSource) != 0;
  const bool embedded_fallback_allowed = model_policy_allows_embedded_fallback();
  if (active_mutable_loaded) {
    copy_cstr(g_status, sizeof(g_status), kActiveStatus);
    copy_cstr(g_error, sizeof(g_error), kNoError);
    g_fail_count = 0;
  } else if (has_active_pointer) {
    copy_cstr(g_status, sizeof(g_status), embedded_fallback_allowed ? kEmbeddedStatus : kModelErrorStatus);
    copy_cstr(g_error, sizeof(g_error), "active_bundle_assets_not_loaded");
  } else {
    if (!embedded_fallback_allowed) {
      copy_cstr(g_status, sizeof(g_status), kModelErrorStatus);
      if (g_error[0] == '\0') {
        copy_cstr(g_error, sizeof(g_error), "single_model_cache_not_loaded");
      }
      copy_cstr(
          g_active_source,
          sizeof(g_active_source),
          model_policy_uses_internal_single_cache() ? kInternalSingleSource : kSdSource);
    } else {
      copy_cstr(g_status, sizeof(g_status), kEmbeddedStatus);
      copy_cstr(g_error, sizeof(g_error), kNoError);
      copy_cstr(g_active_source, sizeof(g_active_source), kEmbeddedSource);
      copy_cstr(g_active_bundle_id, sizeof(g_active_bundle_id), "embedded");
      copy_cstr(g_active_version, sizeof(g_active_version), "embedded");
    }
  }

  g_state.status = g_status;
  g_state.error = g_error;
  g_state.active_source = active_mutable_loaded ? source_for_storage_kind(g_active_candidate.storage_kind) : g_active_source;
  g_state.active_bank = g_active_bank;
  g_state.previous_bank = g_previous_bank;
  g_state.active_bundle_id = g_active_bundle_id;
  g_state.active_version = g_active_version;
  g_state.active_sha256 = g_active_sha256;
  g_state.storage_model_policy = hexe::board::pins::kStorageModelPolicy;
  g_state.sd_model_sets_path = hexe::board::sd_card_model_sets_path();
  g_state.cache_status = g_cache_status;
  g_state.cache_error = g_cache_error;
  g_state.embedded_fallback = !active_mutable_loaded && embedded_fallback_allowed;
  g_state.rollback_available = g_previous_bank[0] != '\0';
  g_state.staged_tested = g_active_candidate.tested;
  g_state.internal_ab_available = find_model_partition("model_a") != nullptr && find_model_partition("model_b") != nullptr;
  g_state.internal_single_available = find_model_partition("model") != nullptr;
  g_state.sd_versioned_available = hexe::board::sd_card_mounted();
  g_state.sd_model_sets_available = hexe::board::sd_card_mounted() && hexe::board::sd_card_model_sets_path()[0] != '\0';
  g_state.model_a_bytes = model_partition_size("model_a");
  g_state.model_b_bytes = model_partition_size("model_b");
  g_state.model_bytes = model_partition_size("model");
  g_state.fail_count = g_fail_count;
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
  if ((candidate.models == nullptr || candidate.model_count == 0) &&
      candidate.storage_kind != hexe::voice::ModelBundleStorageKind::kSdVersionedDirectory) {
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
  copy_cstr(g_active_sha256, sizeof(g_active_sha256), "");
  copy_cstr(g_cache_status, sizeof(g_cache_status), "idle");
  copy_cstr(g_cache_error, sizeof(g_cache_error), "");
  g_fail_count = 0;
  g_active_candidate = {};
  free_runtime_store(&g_internal_store);
  free_runtime_store(&g_sd_store);
  load_model_config_metadata();
  load_active_bundle_pointer();
  refresh_public_state();
  ESP_LOGI(
      kTag,
      "Model bundle manager initialized: source=%s bank=%s status=%s fallback=%s model=%u model_a=%u model_b=%u sd_model_sets=%s",
      g_state.active_source,
      g_state.active_bank,
      g_state.status,
      g_state.embedded_fallback ? "true" : "false",
      static_cast<unsigned>(g_state.model_bytes),
      static_cast<unsigned>(g_state.model_a_bytes),
      static_cast<unsigned>(g_state.model_b_bytes),
      g_state.sd_model_sets_available ? hexe::board::sd_card_model_sets_path() : "unavailable");
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
  if (model_policy_uses_internal_single_cache() || model_policy_allows_sd_model_sets()) {
    char load_error[80] = {};
    for (int attempt = 0; attempt < kModelLoadRetryLimit; ++attempt) {
      if (model_policy_uses_internal_single_cache() && load_internal_single_model_cache(load_error, sizeof(load_error))) {
        refresh_public_state();
        if (selected_model_count != nullptr) {
          *selected_model_count = g_active_candidate.model_count;
        }
        return g_active_candidate.models;
      }
      if (model_policy_allows_sd_model_sets() && load_and_cache_sd_model_set(load_error, sizeof(load_error))) {
        refresh_public_state();
        if (selected_model_count != nullptr) {
          *selected_model_count = g_active_candidate.model_count;
        }
        return g_active_candidate.models;
      }
      ESP_LOGW(kTag, "Mutable model bundle unavailable; retrying model load attempt=%d error=%s", attempt + 1, load_error);
    }
    if (!model_policy_allows_embedded_fallback()) {
      record_model_load_failure(load_error[0] == '\0' ? "single_model_cache_not_loaded" : load_error);
      refresh_public_state();
      if (selected_model_count != nullptr) {
        *selected_model_count = 0;
      }
      return nullptr;
    }
  }
  return embedded_models;
}

bool activate_model_bundle_candidate(const ModelBundleCandidate &candidate, char *error_code, size_t error_code_size) {
  if (!candidate_compatible(candidate, error_code, error_code_size)) {
    refresh_public_state();
    return false;
  }
  if ((candidate.models == nullptr || candidate.model_count == 0) &&
      candidate.storage_kind == ModelBundleStorageKind::kSdVersionedDirectory) {
    if (!load_model_set_from_directory(
            candidate.bank,
            ModelBundleStorageKind::kSdVersionedDirectory,
            &g_sd_store,
            error_code,
            error_code_size)) {
      refresh_public_state();
      return false;
    }
    if (single_model_schema() && copy_manifest_assets_to_internal_cache(candidate.bank, error_code, error_code_size) &&
        load_internal_single_model_cache(error_code, error_code_size)) {
      refresh_public_state();
      return true;
    }
    return activate_loaded_store(&g_sd_store, error_code, error_code_size);
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
          candidate.version,
          candidate.bundle_sha256)) {
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
  copy_cstr(g_active_sha256, sizeof(g_active_sha256), candidate.bundle_sha256);
  g_fail_count = 0;
  persist_model_config_metadata(kNoError);
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
  if (!commit_active_bundle_pointer(
          g_active_source,
          rollback_target,
          old_active,
          g_active_bundle_id,
          g_active_version,
          g_active_sha256)) {
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
