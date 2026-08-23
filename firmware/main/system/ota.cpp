#include "system/ota.h"

#include <cctype>
#include <cstdio>
#include <cstring>
#include <string>

#include "app_state.h"
#include "endpoint_config.h"
#include "esp_app_desc.h"
#include "esp_err.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mbedtls/md.h"

namespace {
constexpr char kTag[] = "hexe_ota";
constexpr int kOtaQueueDepth = 1;
constexpr int kOtaTaskStackBytes = 8192;
constexpr int kOtaTaskPriority = 5;
constexpr int kOtaTimeoutMs = 30000;
constexpr size_t kSha256BlockBytes = 64;
constexpr char kOtaSignatureAlgorithm[] = "hmac-sha256";

struct OtaRequest {
  char request_id[96];
  char url[256];
  char version[32];
  char profile[32];
  char sha256[65];
  int size_bytes;
  char signature_algorithm[24];
  char signature_key_id[48];
  char manifest_signature[65];
};

struct OtaDownloadContext {
  mbedtls_md_context_t sha256;
  int bytes_seen;
};

void set_error_code(char *target, size_t target_size, const char *code) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  std::snprintf(target, target_size, "%s", code == nullptr ? "ota_rejected" : code);
}

bool is_hex_digest(const char *value) {
  if (value == nullptr || std::strlen(value) != 64) {
    return false;
  }
  for (size_t index = 0; value[index] != '\0'; ++index) {
    if (!std::isxdigit(static_cast<unsigned char>(value[index]))) {
      return false;
    }
  }
  return true;
}

void bytes_to_hex(const unsigned char *bytes, size_t byte_count, char *target, size_t target_size) {
  if (target == nullptr || target_size < (byte_count * 2) + 1) {
    return;
  }
  for (size_t index = 0; index < byte_count; ++index) {
    std::snprintf(target + (index * 2), 3, "%02x", bytes[index]);
  }
  target[byte_count * 2] = '\0';
}

const char *current_firmware_version() {
  const esp_app_desc_t *app = esp_app_get_description();
  return (app == nullptr || app->version[0] == '\0') ? "unknown" : app->version;
}

bool parse_semver_components(const char *version, int *parts, size_t part_count) {
  if (version == nullptr || version[0] == '\0' || parts == nullptr) {
    return false;
  }
  size_t part_index = 0;
  const char *cursor = version;
  while (*cursor != '\0' && part_index < part_count) {
    if (!std::isdigit(static_cast<unsigned char>(*cursor))) {
      return false;
    }
    int value = 0;
    while (std::isdigit(static_cast<unsigned char>(*cursor))) {
      value = (value * 10) + (*cursor - '0');
      ++cursor;
    }
    parts[part_index++] = value;
    if (*cursor == '.') {
      ++cursor;
      continue;
    }
    if (*cursor == '\0' || *cursor == '-' || *cursor == '+') {
      break;
    }
    return false;
  }
  return part_index > 0;
}

bool version_is_newer(const char *target, const char *current) {
  if (target == nullptr || target[0] == '\0') {
    return false;
  }
  if (current == nullptr || current[0] == '\0' || std::strcmp(current, "unknown") == 0) {
    return true;
  }
  int target_parts[4] = {};
  int current_parts[4] = {};
  if (parse_semver_components(target, target_parts, 4) && parse_semver_components(current, current_parts, 4)) {
    for (size_t index = 0; index < 4; ++index) {
      if (target_parts[index] > current_parts[index]) {
        return true;
      }
      if (target_parts[index] < current_parts[index]) {
        return false;
      }
    }
    return false;
  }
  return std::strcmp(target, current) > 0;
}

std::string canonical_manifest_payload(const OtaRequest &request) {
  std::string payload;
  payload.reserve(
      std::strlen(request.profile) + std::strlen(request.url) + std::strlen(request.version) +
      std::strlen(request.sha256) + std::strlen(request.signature_algorithm) + std::strlen(request.signature_key_id) + 32);
  payload.append(request.profile);
  payload.push_back('\n');
  payload.append(request.url);
  payload.push_back('\n');
  payload.append(request.version);
  payload.push_back('\n');
  payload.append(request.sha256);
  payload.push_back('\n');
  payload.append(std::to_string(request.size_bytes));
  payload.push_back('\n');
  payload.append(request.signature_algorithm);
  payload.push_back('\n');
  payload.append(request.signature_key_id);
  return payload;
}

bool calculate_manifest_hmac(const OtaRequest &request, char *target, size_t target_size) {
  const mbedtls_md_info_t *md_info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (md_info == nullptr || target == nullptr || target_size < 65) {
    return false;
  }
  const std::string payload = canonical_manifest_payload(request);
  unsigned char digest[32] = {};
  const unsigned char *key = reinterpret_cast<const unsigned char *>(hexe::config::kEndpointOtaManifestSigningKey);
  size_t key_len = std::strlen(hexe::config::kEndpointOtaManifestSigningKey);
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

bool constant_time_equal(const char *left, const char *right) {
  if (left == nullptr || right == nullptr || std::strlen(left) != std::strlen(right)) {
    return false;
  }
  unsigned char diff = 0;
  for (size_t index = 0; left[index] != '\0'; ++index) {
    diff |= static_cast<unsigned char>(left[index] ^ right[index]);
  }
  return diff == 0;
}

bool verify_ota_manifest_signature(const OtaRequest &request) {
  char expected[65] = {};
  return calculate_manifest_hmac(request, expected, sizeof(expected)) &&
         constant_time_equal(expected, request.manifest_signature);
}

bool validate_ota_manifest(const OtaRequest &request, char *error_code, size_t error_code_size) {
  if (request.url[0] == '\0') {
    set_error_code(error_code, error_code_size, "missing_url");
    return false;
  }
  if (request.version[0] == '\0' || !version_is_newer(request.version, current_firmware_version())) {
    set_error_code(error_code, error_code_size, "downgrade_or_replay");
    return false;
  }
  if (std::strcmp(request.profile, hexe::config::kEndpointBoardProfile) != 0) {
    set_error_code(error_code, error_code_size, "unsupported_profile");
    return false;
  }
  if (request.size_bytes <= 0) {
    set_error_code(error_code, error_code_size, "invalid_size");
    return false;
  }
  if (!is_hex_digest(request.sha256)) {
    set_error_code(error_code, error_code_size, request.sha256[0] == '\0' ? "missing_checksum" : "invalid_checksum");
    return false;
  }
  if (std::strcmp(request.signature_algorithm, kOtaSignatureAlgorithm) != 0) {
    set_error_code(error_code, error_code_size, "unsupported_signature_algorithm");
    return false;
  }
  if (std::strcmp(request.signature_key_id, hexe::config::kEndpointOtaManifestKeyId) != 0) {
    set_error_code(error_code, error_code_size, "unknown_signature_key");
    return false;
  }
  if (!is_hex_digest(request.manifest_signature)) {
    set_error_code(
        error_code,
        error_code_size,
        request.manifest_signature[0] == '\0' ? "missing_signature" : "invalid_signature");
    return false;
  }
  if (!verify_ota_manifest_signature(request)) {
    set_error_code(error_code, error_code_size, "invalid_signature");
    return false;
  }
  return true;
}

esp_err_t ota_http_event_handler(esp_http_client_event_t *event) {
  if (event == nullptr || event->event_id != HTTP_EVENT_ON_DATA || event->data == nullptr || event->data_len <= 0) {
    return ESP_OK;
  }
  auto *context = static_cast<OtaDownloadContext *>(event->user_data);
  if (context == nullptr) {
    return ESP_OK;
  }
  if (mbedtls_md_update(
          &context->sha256,
          reinterpret_cast<const unsigned char *>(event->data),
          static_cast<size_t>(event->data_len)) != 0) {
    return ESP_FAIL;
  }
  context->bytes_seen += event->data_len;
  return ESP_OK;
}

QueueHandle_t g_ota_queue = nullptr;
TaskHandle_t g_ota_task = nullptr;

void ota_task(void *arg) {
  (void)arg;
  OtaRequest request = {};
  while (true) {
    if (xQueueReceive(g_ota_queue, &request, portMAX_DELAY) != pdTRUE) {
      continue;
    }

    ESP_LOGI(kTag, "Starting OTA update version=%s profile=%s url=%s", request.version, request.profile, request.url);
    auto &app_state = hexe::state();
    app_state.phase = hexe::AppPhase::kUpdating;
    app_state.ota_active = true;
    app_state.ota_progress_percent = 0;
    app_state.ota_bytes_read = 0;
    app_state.ota_size_bytes = request.size_bytes > 0 ? request.size_bytes : 0;

    OtaDownloadContext download_context = {};
    mbedtls_md_init(&download_context.sha256);
    const mbedtls_md_info_t *sha256_info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    esp_err_t result = (
                           sha256_info != nullptr &&
                           mbedtls_md_setup(&download_context.sha256, sha256_info, 0) == 0 &&
                           mbedtls_md_starts(&download_context.sha256) == 0)
                           ? ESP_OK
                           : ESP_FAIL;

    esp_http_client_config_t http_config = {};
    http_config.url = request.url;
    http_config.timeout_ms = kOtaTimeoutMs;
    http_config.keep_alive_enable = true;
    http_config.event_handler = ota_http_event_handler;
    http_config.user_data = &download_context;

    esp_https_ota_config_t ota_config = {};
    ota_config.http_config = &http_config;

    esp_https_ota_handle_t ota_handle = nullptr;
    if (result == ESP_OK) {
      result = esp_https_ota_begin(&ota_config, &ota_handle);
    }
    if (result == ESP_OK) {
      const int image_size = esp_https_ota_get_image_size(ota_handle);
      if (image_size > 0) {
        app_state.ota_size_bytes = image_size;
      }

      do {
        result = esp_https_ota_perform(ota_handle);
        const int bytes_read = esp_https_ota_get_image_len_read(ota_handle);
        if (bytes_read >= 0) {
          app_state.ota_bytes_read = bytes_read;
          if (app_state.ota_size_bytes > 0) {
            int percent = (bytes_read * 100) / app_state.ota_size_bytes;
            if (percent > 100) {
              percent = 100;
            }
            app_state.ota_progress_percent = percent;
          }
        }
      } while (result == ESP_ERR_HTTPS_OTA_IN_PROGRESS);

      if (result == ESP_OK && !esp_https_ota_is_complete_data_received(ota_handle)) {
        result = ESP_ERR_INVALID_SIZE;
      }

      if (result == ESP_OK) {
        unsigned char digest[32] = {};
        char calculated_sha256[65] = {};
        if (mbedtls_md_finish(&download_context.sha256, digest) != 0) {
          result = ESP_FAIL;
        } else {
          bytes_to_hex(digest, sizeof(digest), calculated_sha256, sizeof(calculated_sha256));
          if (request.size_bytes > 0 && download_context.bytes_seen != request.size_bytes) {
            ESP_LOGE(
                kTag,
                "OTA update failed size check: expected=%d actual=%d",
                request.size_bytes,
                download_context.bytes_seen);
            result = ESP_ERR_INVALID_SIZE;
          } else if (!constant_time_equal(calculated_sha256, request.sha256)) {
            ESP_LOGE(
                kTag,
                "OTA update failed checksum_mismatch: expected=%s actual=%s",
                request.sha256,
                calculated_sha256);
            result = ESP_ERR_INVALID_CRC;
          }
        }
      }

      if (result == ESP_OK) {
        result = esp_https_ota_finish(ota_handle);
        ota_handle = nullptr;
      }
    }

    if (result == ESP_OK) {
      app_state.ota_progress_percent = 100;
      ESP_LOGI(kTag, "OTA update installed; restarting into version=%s", request.version);
      mbedtls_md_free(&download_context.sha256);
      esp_restart();
    }

    if (ota_handle != nullptr) {
      esp_https_ota_abort(ota_handle);
    }
    mbedtls_md_free(&download_context.sha256);
    ESP_LOGE(kTag, "OTA update failed: %s", esp_err_to_name(result));
    app_state.ota_active = false;
    app_state.phase = hexe::AppPhase::kError;
  }
}
}

namespace hexe::system {

void init_ota() {
  const esp_err_t valid_result = esp_ota_mark_app_valid_cancel_rollback();
  if (valid_result != ESP_OK && valid_result != ESP_ERR_OTA_ROLLBACK_INVALID_STATE) {
    ESP_LOGW(kTag, "Failed to mark app valid: %s", esp_err_to_name(valid_result));
  }

  if (g_ota_queue != nullptr) {
    return;
  }
  g_ota_queue = xQueueCreate(kOtaQueueDepth, sizeof(OtaRequest));
  if (g_ota_queue == nullptr) {
    ESP_LOGE(kTag, "Failed to create OTA request queue");
    return;
  }
  xTaskCreate(ota_task, "hexe_ota", kOtaTaskStackBytes, nullptr, kOtaTaskPriority, &g_ota_task);
  ESP_LOGI(kTag, "OTA client initialized");
}

bool start_ota_update(const OtaUpdateManifest &manifest, char *error_code, size_t error_code_size) {
  if (g_ota_queue == nullptr) {
    set_error_code(error_code, error_code_size, "ota_not_initialized");
    return false;
  }
  if (hexe::state().ota_active) {
    ESP_LOGW(kTag, "OTA update already active");
    set_error_code(error_code, error_code_size, "ota_update_active");
    return false;
  }

  OtaRequest request = {};
  std::snprintf(request.request_id, sizeof(request.request_id), "%s", manifest.request_id == nullptr ? "" : manifest.request_id);
  std::snprintf(request.url, sizeof(request.url), "%s", manifest.url == nullptr ? "" : manifest.url);
  std::snprintf(request.version, sizeof(request.version), "%s", manifest.version == nullptr ? "" : manifest.version);
  std::snprintf(request.profile, sizeof(request.profile), "%s", manifest.profile == nullptr ? "" : manifest.profile);
  std::snprintf(request.sha256, sizeof(request.sha256), "%s", manifest.sha256 == nullptr ? "" : manifest.sha256);
  request.size_bytes = manifest.size_bytes;
  std::snprintf(
      request.signature_algorithm,
      sizeof(request.signature_algorithm),
      "%s",
      manifest.signature_algorithm == nullptr ? "" : manifest.signature_algorithm);
  std::snprintf(
      request.signature_key_id,
      sizeof(request.signature_key_id),
      "%s",
      manifest.signature_key_id == nullptr ? "" : manifest.signature_key_id);
  std::snprintf(
      request.manifest_signature,
      sizeof(request.manifest_signature),
      "%s",
      manifest.manifest_signature == nullptr ? "" : manifest.manifest_signature);

  if (!validate_ota_manifest(request, error_code, error_code_size)) {
    ESP_LOGW(kTag, "Rejected OTA manifest: reason=%s version=%s profile=%s", error_code, request.version, request.profile);
    return false;
  }

  if (request.sha256[0] != '\0') {
    ESP_LOGI(
        kTag,
        "OTA manifest accepted profile=%s version=%s sha256=%s key_id=%s",
        request.profile,
        request.version,
        request.sha256,
        request.signature_key_id);
  }

  if (xQueueSend(g_ota_queue, &request, 0) != pdTRUE) {
    ESP_LOGW(kTag, "OTA update already queued or running");
    set_error_code(error_code, error_code_size, "ota_update_busy");
    return false;
  }

  auto &app_state = hexe::state();
  app_state.phase = hexe::AppPhase::kUpdating;
  app_state.ota_active = true;
  app_state.ota_progress_percent = 0;
  app_state.ota_bytes_read = 0;
  app_state.ota_size_bytes = request.size_bytes > 0 ? request.size_bytes : 0;
  return true;
}

}  // namespace hexe::system
