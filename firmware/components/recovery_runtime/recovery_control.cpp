#include "recovery_control.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "board_profile_pins.h"
#include "cJSON.h"
#include "endpoint_config.h"
#include "recovery_ble_provisioning.h"
#include "recovery_status.h"
#include "esp_app_desc.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/md.h"
#include "nvs.h"

namespace {

constexpr char kTag[] = "hexe_recovery_control";
constexpr char kSettingsNamespace[] = "hexe_settings";
constexpr char kRecoveryApiVersion[] = "hexe-recovery-api-v1";
constexpr char kOtaSignatureAlgorithm[] = "hmac-sha256";
constexpr size_t kSha256BlockBytes = 64;
constexpr size_t kMaxJsonBodyBytes = 2048;
constexpr size_t kInstallBufferBytes = 4096;

constexpr char kEndpointIdKey[] = "endpoint_id";
constexpr char kDisplayNameKey[] = "display_name";
constexpr char kBackendHostKey[] = "backend_host";
constexpr char kHttpPortKey[] = "http_port";
constexpr char kWsPortKey[] = "ws_port";
constexpr char kUseTlsKey[] = "use_tls";
constexpr char kWifiSsidKey[] = "wifi_ssid";
constexpr char kWifiPasswordKey[] = "wifi_password";
constexpr char kProvisionedKey[] = "provisioned";
constexpr char kBleOnboardingSessionIdKey[] = "ble_session";
constexpr char kBleDeviceIdKey[] = "ble_device_id";
constexpr char kVolumeKey[] = "volume_percent";
constexpr char kMutedKey[] = "muted";
constexpr char kMicroVadPauseMsKey[] = "micro_vad_pause_ms";
constexpr char kMicroVadEnergyThresholdKey[] = "micro_vad_energy_threshold";
constexpr int kStationReconnectAttempts = 10;
constexpr int kRecoveryDiscoveryAttempts = 5;
constexpr int kRecoveryDiscoveryDelayMs = 5000;
constexpr int kRecoveryDiscoveryHttpTimeoutMs = 5000;
constexpr char kDiscoverySchemaVersion[] = "hexevoice.endpoint.discovery.v1";

httpd_handle_t g_http_server = nullptr;
bool g_wifi_initialized = false;
bool g_http_api_active = false;
bool g_full_http_rescue_active = false;
bool g_temporary_ap_active = false;
bool g_station_configured = false;
int g_station_reconnect_attempts = 0;
bool g_recovery_discovery_completed = false;
TaskHandle_t g_recovery_discovery_task = nullptr;
char g_network_mode[12] = "not_started";
char g_ip_address[16] = "0.0.0.0";
char g_recovery_discovery_status[32] = "not_started";

struct InstallHeaders {
  char application_type[16];
  char board_profile[40];
  char partition_schema[24];
  char version[40];
  char sha256[65];
  char signature_algorithm[24];
  char signature_key_id[48];
  char manifest_signature[65];
  int size_bytes;
  bool reboot_after_install;
};

struct RecoveryDiscoveryContext {
  char endpoint_id[64];
  char device_id[64];
  char onboarding_session_id[64];
  char backend_host[96];
  int http_port;
  bool use_tls;
};

void copy_cstr(char *target, size_t target_size, const char *value) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  std::snprintf(target, target_size, "%s", value == nullptr ? "" : value);
}

void copy_wifi_field(uint8_t *target, size_t target_size, const char *value) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  std::memset(target, 0, target_size);
  std::strncpy(reinterpret_cast<char *>(target), value == nullptr ? "" : value, target_size - 1);
}

bool is_truthy(const cJSON *item) {
  return cJSON_IsTrue(item) || (cJSON_IsNumber(item) && item->valueint != 0);
}

bool valid_port(int port) {
  return port > 0 && port <= 65535;
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

void bytes_to_hex(const unsigned char *bytes, size_t byte_count, char *target, size_t target_size) {
  if (target == nullptr || target_size < (byte_count * 2) + 1) {
    return;
  }
  for (size_t index = 0; index < byte_count; ++index) {
    std::snprintf(target + (index * 2), 3, "%02x", bytes[index]);
  }
  target[byte_count * 2] = '\0';
}

std::string bool_json(bool value) {
  return value ? "true" : "false";
}

std::string json_escape(const char *value) {
  std::string escaped;
  const char *source = value == nullptr ? "" : value;
  for (const char *cursor = source; *cursor != '\0'; ++cursor) {
    switch (*cursor) {
      case '\\':
        escaped.append("\\\\");
        break;
      case '"':
        escaped.append("\\\"");
        break;
      case '\n':
        escaped.append("\\n");
        break;
      case '\r':
        escaped.append("\\r");
        break;
      case '\t':
        escaped.append("\\t");
        break;
      default:
        escaped.push_back(*cursor);
        break;
    }
  }
  return escaped;
}

const char *firmware_version() {
  const esp_app_desc_t *app = esp_app_get_description();
  return app == nullptr ? "unknown" : app->version;
}

const char *hardware_id() {
  static char buffer[32] = "";
  if (buffer[0] != '\0') {
    return buffer;
  }
  uint8_t mac[6] = {};
  if (esp_efuse_mac_get_default(mac) != ESP_OK) {
    copy_cstr(buffer, sizeof(buffer), "esp32-unknown");
    return buffer;
  }
  std::snprintf(buffer, sizeof(buffer), "esp32-%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return buffer;
}

bool load_nvs_string(nvs_handle_t handle, const char *key, char *target, size_t target_size, bool required) {
  char loaded[128] = {};
  if (target_size > sizeof(loaded)) {
    return false;
  }
  size_t length = target_size;
  const esp_err_t err = nvs_get_str(handle, key, loaded, &length);
  if (err == ESP_ERR_NVS_NOT_FOUND) {
    return !required;
  }
  if (err != ESP_OK || (required && loaded[0] == '\0')) {
    return false;
  }
  copy_cstr(target, target_size, loaded);
  return true;
}

bool load_recovery_discovery_context(RecoveryDiscoveryContext *context) {
  if (context == nullptr) {
    return false;
  }
  *context = {};
  copy_cstr(context->endpoint_id, sizeof(context->endpoint_id), hexe::config::kEndpointId);
  copy_cstr(context->device_id, sizeof(context->device_id), hexe::config::kEndpointId);
  context->use_tls = hexe::config::kEndpointUseTls;

  nvs_handle_t handle = 0;
  const esp_err_t open_err = nvs_open(kSettingsNamespace, NVS_READONLY, &handle);
  if (open_err != ESP_OK) {
    copy_cstr(g_recovery_discovery_status, sizeof(g_recovery_discovery_status), "nvs_unavailable");
    return false;
  }

  int32_t http_port = 0;
  uint8_t use_tls = context->use_tls ? 1 : 0;
  const esp_err_t tls_err = nvs_get_u8(handle, kUseTlsKey, &use_tls);
  const bool loaded =
      load_nvs_string(handle, kEndpointIdKey, context->endpoint_id, sizeof(context->endpoint_id), false) &&
      load_nvs_string(handle, kBleDeviceIdKey, context->device_id, sizeof(context->device_id), false) &&
      load_nvs_string(handle, kBleOnboardingSessionIdKey, context->onboarding_session_id, sizeof(context->onboarding_session_id), true) &&
      load_nvs_string(handle, kBackendHostKey, context->backend_host, sizeof(context->backend_host), true) &&
      nvs_get_i32(handle, kHttpPortKey, &http_port) == ESP_OK &&
      (tls_err == ESP_OK || tls_err == ESP_ERR_NVS_NOT_FOUND);
  nvs_close(handle);

  context->http_port = static_cast<int>(http_port);
  context->use_tls = use_tls != 0;
  if (!loaded || !valid_port(context->http_port)) {
    copy_cstr(g_recovery_discovery_status, sizeof(g_recovery_discovery_status), "settings_missing");
    return false;
  }
  if (context->device_id[0] == '\0') {
    copy_cstr(context->device_id, sizeof(context->device_id), context->endpoint_id);
  }
  return true;
}

std::string print_json(cJSON *root) {
  char *rendered = cJSON_PrintUnformatted(root);
  std::string result = rendered == nullptr ? "{}" : rendered;
  cJSON_free(rendered);
  cJSON_Delete(root);
  return result;
}

std::string recovery_discovery_body(const RecoveryDiscoveryContext &context) {
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "schema_version", kDiscoverySchemaVersion);
  cJSON_AddStringToObject(root, "endpoint_id", context.endpoint_id);
  cJSON_AddStringToObject(root, "device_id", context.device_id);
  cJSON_AddStringToObject(root, "onboarding_session_id", context.onboarding_session_id);
  cJSON_AddStringToObject(root, "hardware_id", hardware_id());
  cJSON_AddStringToObject(root, "board_profile", hexe::board::pins::kBoardProfile);
  cJSON_AddStringToObject(root, "firmware_version", firmware_version());
  cJSON_AddStringToObject(root, "application_type", "recovery");
  cJSON *capabilities = cJSON_AddObjectToObject(root, "capabilities");
  cJSON_AddStringToObject(capabilities, "profile", "recovery");
  cJSON_AddStringToObject(capabilities, "application_type", "recovery");
  cJSON *identity = cJSON_AddObjectToObject(capabilities, "identity");
  cJSON_AddStringToObject(identity, "hardware_id", hardware_id());
  cJSON_AddStringToObject(identity, "device_id", context.device_id);
  cJSON *firmware = cJSON_AddObjectToObject(capabilities, "firmware");
  cJSON_AddStringToObject(firmware, "board_profile", hexe::board::pins::kBoardProfile);
  cJSON_AddStringToObject(firmware, "application_type", "recovery");
  cJSON_AddStringToObject(firmware, "version", firmware_version());
  cJSON *ble = cJSON_AddObjectToObject(capabilities, "ble");
  cJSON_AddStringToObject(ble, "onboarding_session_id", context.onboarding_session_id);
  return print_json(root);
}

bool response_accepted(const char *response, int response_len) {
  if (response == nullptr || response_len <= 0) {
    return true;
  }
  cJSON *root = cJSON_ParseWithLength(response, response_len);
  if (!cJSON_IsObject(root)) {
    cJSON_Delete(root);
    return true;
  }
  const cJSON *accepted = cJSON_GetObjectItem(root, "accepted");
  const bool ok = !cJSON_IsBool(accepted) || cJSON_IsTrue(accepted);
  cJSON_Delete(root);
  return ok;
}

bool post_recovery_discovery(const RecoveryDiscoveryContext &context) {
  char url[192];
  std::snprintf(
      url,
      sizeof(url),
      "%s://%s:%d/api/endpoint/discovery/offer",
      context.use_tls ? "https" : "http",
      context.backend_host,
      context.http_port);
  const std::string body = recovery_discovery_body(context);

  esp_http_client_config_t config = {};
  config.url = url;
  config.method = HTTP_METHOD_POST;
  config.timeout_ms = kRecoveryDiscoveryHttpTimeoutMs;
  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    return false;
  }
  esp_http_client_set_header(client, "Content-Type", "application/json");
  esp_err_t err = esp_http_client_open(client, static_cast<int>(body.size()));
  if (err == ESP_OK) {
    const int written = esp_http_client_write(client, body.c_str(), static_cast<int>(body.size()));
    if (written != static_cast<int>(body.size())) {
      err = ESP_FAIL;
    }
  }
  int status_code = 0;
  int response_len = 0;
  char response[512] = {};
  if (err == ESP_OK) {
    esp_http_client_fetch_headers(client);
    status_code = esp_http_client_get_status_code(client);
    response_len = esp_http_client_read_response(client, response, sizeof(response) - 1);
    if (response_len < 0) {
      response_len = 0;
    }
    response[response_len] = '\0';
  }
  esp_http_client_close(client);
  esp_http_client_cleanup(client);
  if (err != ESP_OK || status_code < 200 || status_code >= 300 || !response_accepted(response, response_len)) {
    ESP_LOGW(kTag, "Recovery discovery offer failed err=%s http=%d", esp_err_to_name(err), status_code);
    return false;
  }
  return true;
}

void recovery_discovery_task(void *arg) {
  (void)arg;
  RecoveryDiscoveryContext context = {};
  if (!load_recovery_discovery_context(&context)) {
    ESP_LOGW(kTag, "Recovery discovery skipped: %s", g_recovery_discovery_status);
    g_recovery_discovery_task = nullptr;
    vTaskDelete(nullptr);
    return;
  }

  for (int attempt = 1; attempt <= kRecoveryDiscoveryAttempts; ++attempt) {
    copy_cstr(g_recovery_discovery_status, sizeof(g_recovery_discovery_status), "posting");
    if (post_recovery_discovery(context)) {
      g_recovery_discovery_completed = true;
      copy_cstr(g_recovery_discovery_status, sizeof(g_recovery_discovery_status), "accepted");
      ESP_LOGI(
          kTag,
          "Recovery discovery accepted endpoint=%s device_id=%s session=%s backend=%s:%d",
          context.endpoint_id,
          context.device_id,
          context.onboarding_session_id,
          context.backend_host,
          context.http_port);
      g_recovery_discovery_task = nullptr;
      vTaskDelete(nullptr);
      return;
    }
    copy_cstr(g_recovery_discovery_status, sizeof(g_recovery_discovery_status), "retrying");
    ESP_LOGW(kTag, "Recovery discovery retry %d/%d", attempt, kRecoveryDiscoveryAttempts);
    vTaskDelay(pdMS_TO_TICKS(kRecoveryDiscoveryDelayMs));
  }
  copy_cstr(g_recovery_discovery_status, sizeof(g_recovery_discovery_status), "failed");
  g_recovery_discovery_task = nullptr;
  vTaskDelete(nullptr);
}

void start_recovery_discovery_task_once() {
  if (g_recovery_discovery_completed || g_recovery_discovery_task != nullptr || !g_station_configured) {
    return;
  }
  const BaseType_t created = xTaskCreate(
      recovery_discovery_task,
      "hexe_recovery_disc",
      8192,
      nullptr,
      4,
      &g_recovery_discovery_task);
  if (created != pdPASS) {
    copy_cstr(g_recovery_discovery_status, sizeof(g_recovery_discovery_status), "task_failed");
    g_recovery_discovery_task = nullptr;
    ESP_LOGW(kTag, "Recovery discovery task start failed");
  }
}

const char *partition_type_name(esp_partition_type_t type) {
  switch (type) {
    case ESP_PARTITION_TYPE_APP:
      return "app";
    case ESP_PARTITION_TYPE_DATA:
      return "data";
    default:
      return "unknown";
  }
}

const char *partition_subtype_name(esp_partition_type_t type, esp_partition_subtype_t subtype) {
  if (type == ESP_PARTITION_TYPE_APP) {
    switch (subtype) {
      case ESP_PARTITION_SUBTYPE_APP_FACTORY:
        return "factory";
      case ESP_PARTITION_SUBTYPE_APP_OTA_0:
        return "ota_0";
      case ESP_PARTITION_SUBTYPE_APP_OTA_1:
        return "ota_1";
      default:
        return "app";
    }
  }
  if (type == ESP_PARTITION_TYPE_DATA) {
    switch (subtype) {
      case ESP_PARTITION_SUBTYPE_DATA_NVS:
        return "nvs";
      case ESP_PARTITION_SUBTYPE_DATA_OTA:
        return "ota";
      case ESP_PARTITION_SUBTYPE_DATA_PHY:
        return "phy";
      case ESP_PARTITION_SUBTYPE_DATA_SPIFFS:
        return "spiffs";
      case ESP_PARTITION_SUBTYPE_DATA_COREDUMP:
        return "coredump";
      default:
        return "data";
    }
  }
  return "unknown";
}

const char *ota_state_name(esp_ota_img_states_t state) {
  switch (state) {
    case ESP_OTA_IMG_NEW:
      return "new";
    case ESP_OTA_IMG_PENDING_VERIFY:
      return "pending_verify";
    case ESP_OTA_IMG_VALID:
      return "valid";
    case ESP_OTA_IMG_INVALID:
      return "invalid";
    case ESP_OTA_IMG_ABORTED:
      return "aborted";
    case ESP_OTA_IMG_UNDEFINED:
      return "undefined";
    default:
      return "unknown";
  }
}

bool get_header(httpd_req_t *req, const char *name, char *target, size_t target_size) {
  if (target == nullptr || target_size == 0) {
    return false;
  }
  target[0] = '\0';
  const size_t value_len = httpd_req_get_hdr_value_len(req, name);
  if (value_len == 0 || value_len >= target_size) {
    return false;
  }
  return httpd_req_get_hdr_value_str(req, name, target, target_size) == ESP_OK;
}

bool parse_header_int(httpd_req_t *req, const char *name, int *target) {
  char value[24] = {};
  if (target == nullptr || !get_header(req, name, value, sizeof(value))) {
    return false;
  }
  errno = 0;
  char *end = nullptr;
  const long parsed = std::strtol(value, &end, 10);
  if (errno != 0 || end == value || *end != '\0' || parsed <= 0 || parsed > INT32_MAX) {
    return false;
  }
  *target = static_cast<int>(parsed);
  return true;
}

bool parse_header_bool(httpd_req_t *req, const char *name) {
  char value[12] = {};
  if (!get_header(req, name, value, sizeof(value))) {
    return false;
  }
  for (char &ch : value) {
    ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
  }
  return std::strcmp(value, "1") == 0 ||
         std::strcmp(value, "true") == 0 ||
         std::strcmp(value, "yes") == 0;
}

std::string read_json_body(httpd_req_t *req, bool *ok) {
  if (ok != nullptr) {
    *ok = false;
  }
  if (req->content_len <= 0 || req->content_len > static_cast<int>(kMaxJsonBodyBytes)) {
    return {};
  }
  std::string body;
  body.resize(req->content_len);
  int received = 0;
  while (received < req->content_len) {
    const int chunk = httpd_req_recv(req, body.data() + received, req->content_len - received);
    if (chunk == HTTPD_SOCK_ERR_TIMEOUT) {
      continue;
    }
    if (chunk <= 0) {
      return {};
    }
    received += chunk;
  }
  if (ok != nullptr) {
    *ok = true;
  }
  return body;
}

void send_json(httpd_req_t *req, const char *status, const std::string &body) {
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, "application/json");
  httpd_resp_send(req, body.c_str(), body.size());
}

void send_error_json(httpd_req_t *req, const char *status, const char *code, const char *message) {
  char body[256];
  std::snprintf(
      body,
      sizeof(body),
      "{\"ok\":false,\"schema_version\":\"%s\",\"error_code\":\"%s\",\"message\":\"%s\"}",
      kRecoveryApiVersion,
      code == nullptr ? "recovery_error" : code,
      message == nullptr ? "Recovery request failed" : message);
  send_json(req, status, body);
}

bool set_nvs_string(nvs_handle_t handle, const cJSON *root, const char *json_key, const char *nvs_key, bool required) {
  const cJSON *item = cJSON_GetObjectItem(root, json_key);
  if (!cJSON_IsString(item)) {
    return !required;
  }
  return nvs_set_str(handle, nvs_key, item->valuestring) == ESP_OK;
}

bool set_nvs_i32(nvs_handle_t handle, const cJSON *root, const char *json_key, const char *nvs_key, bool required) {
  const cJSON *item = cJSON_GetObjectItem(root, json_key);
  if (!cJSON_IsNumber(item)) {
    return !required;
  }
  if (!valid_port(item->valueint)) {
    return false;
  }
  return nvs_set_i32(handle, nvs_key, item->valueint) == ESP_OK;
}

bool set_nvs_bool(nvs_handle_t handle, const cJSON *root, const char *json_key, const char *nvs_key, bool required) {
  const cJSON *item = cJSON_GetObjectItem(root, json_key);
  if (!cJSON_IsBool(item)) {
    return !required;
  }
  return nvs_set_u8(handle, nvs_key, cJSON_IsTrue(item) ? 1 : 0) == ESP_OK;
}

void erase_key_if_requested(nvs_handle_t handle, const char *key) {
  const esp_err_t err = nvs_erase_key(handle, key);
  if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(kTag, "Failed to erase recovery NVS key %s: %s", key, esp_err_to_name(err));
  }
}

std::string canonical_install_payload(const InstallHeaders &headers) {
  std::string payload;
  payload.reserve(256);
  payload.append(headers.application_type);
  payload.push_back('\n');
  payload.append(headers.board_profile);
  payload.push_back('\n');
  payload.append(headers.partition_schema);
  payload.push_back('\n');
  payload.append(headers.version);
  payload.push_back('\n');
  payload.append(headers.sha256);
  payload.push_back('\n');
  payload.append(std::to_string(headers.size_bytes));
  payload.push_back('\n');
  payload.append(headers.signature_algorithm);
  payload.push_back('\n');
  payload.append(headers.signature_key_id);
  return payload;
}

bool calculate_install_hmac(const InstallHeaders &headers, char *target, size_t target_size) {
  const mbedtls_md_info_t *md_info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (md_info == nullptr || target == nullptr || target_size < 65) {
    return false;
  }

  const std::string payload = canonical_install_payload(headers);
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
  if (mbedtls_md(md_info, reinterpret_cast<const unsigned char *>(inner.data()), inner.size(), inner_digest) != 0) {
    return false;
  }

  std::string outer(reinterpret_cast<const char *>(outer_pad), sizeof(outer_pad));
  outer.append(reinterpret_cast<const char *>(inner_digest), sizeof(inner_digest));
  unsigned char digest[32] = {};
  if (mbedtls_md(md_info, reinterpret_cast<const unsigned char *>(outer.data()), outer.size(), digest) != 0) {
    return false;
  }
  bytes_to_hex(digest, sizeof(digest), target, target_size);
  return true;
}

bool verify_install_signature(const InstallHeaders &headers) {
  char expected[65] = {};
  return calculate_install_hmac(headers, expected, sizeof(expected)) &&
         constant_time_equal(expected, headers.manifest_signature);
}

void start_http_server(bool full_rescue_routes);

bool read_install_headers(httpd_req_t *req, InstallHeaders *headers, char *error, size_t error_size) {
  if (headers == nullptr) {
    return false;
  }
  *headers = {};
  if (!get_header(req, "X-Hexe-Application-Type", headers->application_type, sizeof(headers->application_type)) ||
      std::strcmp(headers->application_type, "endpoint") != 0) {
    copy_cstr(error, error_size, "wrong_application_type");
    return false;
  }
  if (!get_header(req, "X-Hexe-Board-Profile", headers->board_profile, sizeof(headers->board_profile)) ||
      std::strcmp(headers->board_profile, hexe::board::pins::kBoardProfile) != 0) {
    copy_cstr(error, error_size, "unsupported_profile");
    return false;
  }
  if (!get_header(req, "X-Hexe-Partition-Schema", headers->partition_schema, sizeof(headers->partition_schema)) ||
      std::strcmp(headers->partition_schema, hexe::board::pins::kPartitionSchema) != 0) {
    copy_cstr(error, error_size, "partition_schema_mismatch");
    return false;
  }
  if (!get_header(req, "X-Hexe-Version", headers->version, sizeof(headers->version))) {
    copy_cstr(error, error_size, "missing_version");
    return false;
  }
  if (!get_header(req, "X-Hexe-Image-Sha256", headers->sha256, sizeof(headers->sha256)) ||
      !is_hex_digest(headers->sha256)) {
    copy_cstr(error, error_size, "invalid_checksum");
    return false;
  }
  if (!parse_header_int(req, "X-Hexe-Image-Size", &headers->size_bytes) ||
      headers->size_bytes != req->content_len) {
    copy_cstr(error, error_size, "invalid_size");
    return false;
  }
  if (!get_header(req, "X-Hexe-Signature-Algorithm", headers->signature_algorithm, sizeof(headers->signature_algorithm)) ||
      std::strcmp(headers->signature_algorithm, kOtaSignatureAlgorithm) != 0) {
    copy_cstr(error, error_size, "unsupported_signature_algorithm");
    return false;
  }
  if (!get_header(req, "X-Hexe-Signature-Key-Id", headers->signature_key_id, sizeof(headers->signature_key_id)) ||
      std::strcmp(headers->signature_key_id, hexe::config::kEndpointOtaManifestKeyId) != 0) {
    copy_cstr(error, error_size, "unknown_signature_key");
    return false;
  }
  if (!get_header(req, "X-Hexe-Manifest-Signature", headers->manifest_signature, sizeof(headers->manifest_signature)) ||
      !is_hex_digest(headers->manifest_signature)) {
    copy_cstr(error, error_size, "invalid_signature");
    return false;
  }
  if (!verify_install_signature(*headers)) {
    copy_cstr(error, error_size, "invalid_signature");
    return false;
  }
  headers->reboot_after_install = parse_header_bool(req, "X-Hexe-Reboot-After-Install");
  return true;
}

void delayed_reboot_task(void *arg) {
  (void)arg;
  vTaskDelay(pdMS_TO_TICKS(1200));
  ESP_LOGI(kTag, "Recovery firmware install requested reboot into selected endpoint partition");
  esp_restart();
}

void schedule_reboot_after_install() {
  const BaseType_t created = xTaskCreate(
      delayed_reboot_task,
      "hexe_recovery_reboot",
      2048,
      nullptr,
      5,
      nullptr);
  if (created != pdPASS) {
    ESP_LOGW(kTag, "Recovery firmware install could not schedule reboot");
  }
}

void update_network_mode() {
  if (g_temporary_ap_active && g_station_configured) {
    copy_cstr(g_network_mode, sizeof(g_network_mode), "apsta");
  } else if (g_temporary_ap_active) {
    copy_cstr(g_network_mode, sizeof(g_network_mode), "ap");
  } else if (g_station_configured) {
    copy_cstr(g_network_mode, sizeof(g_network_mode), "sta");
  } else {
    copy_cstr(g_network_mode, sizeof(g_network_mode), "not_started");
  }
}

void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data) {
  (void)arg;
  if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP && event_data != nullptr) {
    const auto *event = static_cast<const ip_event_got_ip_t *>(event_data);
    std::snprintf(g_ip_address, sizeof(g_ip_address), IPSTR, IP2STR(&event->ip_info.ip));
    g_station_reconnect_attempts = 0;
    ESP_LOGI(kTag, "Recovery STA connected at %s", g_ip_address);
    if (!g_http_api_active) {
      start_http_server(false);
    }
    start_recovery_discovery_task_once();
  } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED && g_station_configured) {
    if (g_station_reconnect_attempts < kStationReconnectAttempts) {
      ++g_station_reconnect_attempts;
      const esp_err_t err = esp_wifi_connect();
      ESP_LOGW(
          kTag,
          "Recovery STA disconnected; reconnect attempt %d/%d err=%s",
          g_station_reconnect_attempts,
          kStationReconnectAttempts,
          esp_err_to_name(err));
    } else {
      ESP_LOGW(kTag, "Recovery STA disconnected; reconnect attempts exhausted");
    }
  }
}

bool load_wifi_credentials(char *ssid, size_t ssid_size, char *password, size_t password_size) {
  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kSettingsNamespace, NVS_READONLY, &handle);
  if (err != ESP_OK) {
    return false;
  }
  size_t length = ssid_size;
  err = nvs_get_str(handle, kWifiSsidKey, ssid, &length);
  if (err == ESP_OK && password != nullptr && password_size > 0) {
    length = password_size;
    const esp_err_t password_err = nvs_get_str(handle, kWifiPasswordKey, password, &length);
    if (password_err == ESP_ERR_NVS_NOT_FOUND) {
      password[0] = '\0';
    }
  }
  nvs_close(handle);
  return err == ESP_OK && ssid[0] != '\0';
}

bool start_recovery_wifi(bool enable_access_point) {
  if (g_wifi_initialized) {
    return true;
  }

  char ssid[33] = {};
  char password[65] = {};
  g_station_configured = load_wifi_credentials(ssid, sizeof(ssid), password, sizeof(password));
  if (!enable_access_point && !g_station_configured) {
    ESP_LOGW(kTag, "Recovery STA start skipped: no saved Wi-Fi credentials");
    return false;
  }

  ESP_ERROR_CHECK(esp_netif_init());
  esp_err_t err = esp_event_loop_create_default();
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_ERROR_CHECK(err);
  }

  if (enable_access_point) {
    esp_netif_create_default_wifi_ap();
  }
  if (g_station_configured) {
    esp_netif_create_default_wifi_sta();
  }
  const wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&cfg));
  ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, nullptr));
  ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, nullptr));

  const wifi_mode_t mode = enable_access_point && g_station_configured
                               ? WIFI_MODE_APSTA
                               : enable_access_point ? WIFI_MODE_AP : WIFI_MODE_STA;
  ESP_ERROR_CHECK(esp_wifi_set_mode(mode));

  if (enable_access_point) {
    wifi_config_t ap_config = {};
    uint8_t ap_mac[6] = {};
    if (esp_read_mac(ap_mac, ESP_MAC_WIFI_SOFTAP) != ESP_OK) {
      ap_mac[3] = 0xaa;
      ap_mac[4] = 0xbb;
      ap_mac[5] = 0xcc;
    }
    std::snprintf(
        reinterpret_cast<char *>(ap_config.ap.ssid),
        sizeof(ap_config.ap.ssid),
        "HexeRecovery-%02x%02x%02x",
        ap_mac[3],
        ap_mac[4],
        ap_mac[5]);
    ap_config.ap.ssid_len = std::strlen(reinterpret_cast<char *>(ap_config.ap.ssid));
    ap_config.ap.channel = 6;
    ap_config.ap.max_connection = 2;
    ap_config.ap.authmode = WIFI_AUTH_OPEN;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap_config));
  }

  if (g_station_configured) {
    wifi_config_t sta_config = {};
    copy_wifi_field(sta_config.sta.ssid, sizeof(sta_config.sta.ssid), ssid);
    copy_wifi_field(sta_config.sta.password, sizeof(sta_config.sta.password), password);
    sta_config.sta.threshold.authmode = WIFI_AUTH_OPEN;
    sta_config.sta.pmf_cfg.capable = true;
    sta_config.sta.pmf_cfg.required = false;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_config));
  }

  ESP_ERROR_CHECK(esp_wifi_start());
  if (g_station_configured) {
    ESP_ERROR_CHECK(esp_wifi_connect());
  }

  std::memset(password, 0, sizeof(password));
  g_temporary_ap_active = enable_access_point;
  g_wifi_initialized = true;
  copy_cstr(g_ip_address, sizeof(g_ip_address), enable_access_point ? "192.168.4.1" : "0.0.0.0");
  update_network_mode();
  ESP_LOGI(
      kTag,
      "Recovery Wi-Fi started in %s mode temporary_ap=%d station_configured=%d",
      g_network_mode,
      g_temporary_ap_active,
      g_station_configured);
  return true;
}

esp_err_t status_handler(httpd_req_t *req) {
  send_json(req, "200 OK", hexe::recovery::render_status_json());
  return ESP_OK;
}

esp_err_t partitions_handler(httpd_req_t *req) {
  send_json(req, "200 OK", hexe::recovery::render_partitions_json());
  return ESP_OK;
}

esp_err_t diagnostics_handler(httpd_req_t *req) {
  send_json(req, "200 OK", hexe::recovery::render_diagnostics_json());
  return ESP_OK;
}

esp_err_t ble_status_handler(httpd_req_t *req) {
  send_json(req, "200 OK", hexe::recovery::render_recovery_ble_status_json());
  return ESP_OK;
}

esp_err_t root_handler(httpd_req_t *req) {
  constexpr char page[] =
      "<!doctype html><html><head><meta charset=\"utf-8\"><title>Hexe Recovery</title>"
      "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
      "<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:48rem}"
      "code{background:#eee;padding:.15rem .3rem}a{display:block;margin:.4rem 0}</style></head>"
      "<body><h1>Hexe Recovery</h1><p>Local recovery app is running.</p>"
      "<a href=\"/api/recovery/status\">Status JSON</a>"
      "<a href=\"/api/recovery/partitions\">Partition JSON</a>"
      "<a href=\"/api/recovery/diagnostics\">Diagnostics JSON</a>"
      "<a href=\"/api/recovery/ble/status\">BLE JSON</a>"
      "<p>POST JSON to <code>/api/recovery/wifi</code>, <code>/api/recovery/endpoint</code>, "
      "or <code>/api/recovery/config/reset</code> for rescue actions.</p></body></html>";
  httpd_resp_set_type(req, "text/html");
  httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);
  return ESP_OK;
}

esp_err_t wifi_post_handler(httpd_req_t *req) {
  bool ok = false;
  const std::string body = read_json_body(req, &ok);
  if (!ok) {
    send_error_json(req, "400 Bad Request", "invalid_json_body", "Expected a small JSON provisioning body");
    return ESP_OK;
  }
  cJSON *root = cJSON_ParseWithLength(body.data(), body.size());
  if (!cJSON_IsObject(root)) {
    cJSON_Delete(root);
    send_error_json(req, "400 Bad Request", "invalid_json_body", "Wi-Fi provisioning body must be a JSON object");
    return ESP_OK;
  }

  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kSettingsNamespace, NVS_READWRITE, &handle);
  if (err != ESP_OK) {
    cJSON_Delete(root);
    send_error_json(req, "500 Internal Server Error", "nvs_open_failed", esp_err_to_name(err));
    return ESP_OK;
  }
  const bool saved =
      set_nvs_string(handle, root, "wifi_ssid", kWifiSsidKey, true) &&
      set_nvs_string(handle, root, "wifi_password", kWifiPasswordKey, false) &&
      nvs_commit(handle) == ESP_OK;
  nvs_close(handle);
  cJSON_Delete(root);

  if (!saved) {
    send_error_json(req, "400 Bad Request", "invalid_wifi_provisioning", "wifi_ssid is required and must fit NVS");
    return ESP_OK;
  }
  g_station_configured = true;
  update_network_mode();
  send_json(req, "200 OK", "{\"ok\":true,\"message\":\"Wi-Fi settings saved; reboot recovery to connect STA mode\"}");
  return ESP_OK;
}

esp_err_t endpoint_post_handler(httpd_req_t *req) {
  bool ok = false;
  const std::string body = read_json_body(req, &ok);
  if (!ok) {
    send_error_json(req, "400 Bad Request", "invalid_json_body", "Expected a small JSON provisioning body");
    return ESP_OK;
  }
  cJSON *root = cJSON_ParseWithLength(body.data(), body.size());
  if (!cJSON_IsObject(root)) {
    cJSON_Delete(root);
    send_error_json(req, "400 Bad Request", "invalid_json_body", "Endpoint provisioning body must be a JSON object");
    return ESP_OK;
  }

  const cJSON *http_port = cJSON_GetObjectItem(root, "http_port");
  const cJSON *ws_port = cJSON_GetObjectItem(root, "ws_port");
  const bool ports_ok = cJSON_IsNumber(http_port) && cJSON_IsNumber(ws_port) &&
                        valid_port(http_port->valueint) && valid_port(ws_port->valueint);
  if (!ports_ok) {
    cJSON_Delete(root);
    send_error_json(req, "400 Bad Request", "invalid_endpoint_provisioning", "http_port and ws_port must be 1-65535");
    return ESP_OK;
  }

  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kSettingsNamespace, NVS_READWRITE, &handle);
  if (err != ESP_OK) {
    cJSON_Delete(root);
    send_error_json(req, "500 Internal Server Error", "nvs_open_failed", esp_err_to_name(err));
    return ESP_OK;
  }

  bool saved =
      set_nvs_string(handle, root, "endpoint_id", kEndpointIdKey, true) &&
      set_nvs_string(handle, root, "display_name", kDisplayNameKey, false) &&
      set_nvs_string(handle, root, "backend_host", kBackendHostKey, true) &&
      set_nvs_i32(handle, root, "http_port", kHttpPortKey, true) &&
      set_nvs_i32(handle, root, "ws_port", kWsPortKey, true) &&
      set_nvs_bool(handle, root, "use_tls", kUseTlsKey, false) &&
      nvs_set_u8(handle, kProvisionedKey, 1) == ESP_OK &&
      nvs_commit(handle) == ESP_OK;
  nvs_close(handle);
  cJSON_Delete(root);

  if (!saved) {
    send_error_json(req, "400 Bad Request", "invalid_endpoint_provisioning", "endpoint_id and backend_host are required");
    return ESP_OK;
  }
  send_json(req, "200 OK", "{\"ok\":true,\"message\":\"Endpoint provisioning saved; reboot to start the main app with new settings\"}");
  return ESP_OK;
}

esp_err_t reset_post_handler(httpd_req_t *req) {
  bool ok = false;
  const std::string body = read_json_body(req, &ok);
  if (!ok) {
    send_error_json(req, "400 Bad Request", "invalid_json_body", "Expected a small JSON reset body");
    return ESP_OK;
  }
  cJSON *root = cJSON_ParseWithLength(body.data(), body.size());
  if (!cJSON_IsObject(root)) {
    cJSON_Delete(root);
    send_error_json(req, "400 Bad Request", "invalid_json_body", "Reset body must be a JSON object");
    return ESP_OK;
  }

  const bool reset_provisioning = is_truthy(cJSON_GetObjectItem(root, "provisioning"));
  const bool reset_wifi = is_truthy(cJSON_GetObjectItem(root, "wifi"));
  const bool reset_settings = is_truthy(cJSON_GetObjectItem(root, "settings"));
  const bool reset_calibration = is_truthy(cJSON_GetObjectItem(root, "calibration"));
  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kSettingsNamespace, NVS_READWRITE, &handle);
  if (err != ESP_OK) {
    cJSON_Delete(root);
    send_error_json(req, "500 Internal Server Error", "nvs_open_failed", esp_err_to_name(err));
    return ESP_OK;
  }

  if (reset_provisioning) {
    erase_key_if_requested(handle, kEndpointIdKey);
    erase_key_if_requested(handle, kDisplayNameKey);
    erase_key_if_requested(handle, kBackendHostKey);
    erase_key_if_requested(handle, kHttpPortKey);
    erase_key_if_requested(handle, kWsPortKey);
    erase_key_if_requested(handle, kUseTlsKey);
    erase_key_if_requested(handle, kProvisionedKey);
  }
  if (reset_wifi) {
    erase_key_if_requested(handle, kWifiSsidKey);
    erase_key_if_requested(handle, kWifiPasswordKey);
    g_station_configured = false;
    update_network_mode();
  }
  if (reset_settings) {
    erase_key_if_requested(handle, kVolumeKey);
    erase_key_if_requested(handle, kMutedKey);
    erase_key_if_requested(handle, kMicroVadPauseMsKey);
    erase_key_if_requested(handle, kMicroVadEnergyThresholdKey);
  }
  if (reset_calibration) {
    erase_key_if_requested(handle, kMicroVadPauseMsKey);
    erase_key_if_requested(handle, kMicroVadEnergyThresholdKey);
  }
  err = nvs_commit(handle);
  nvs_close(handle);
  cJSON_Delete(root);

  if (err != ESP_OK) {
    send_error_json(req, "500 Internal Server Error", "nvs_commit_failed", esp_err_to_name(err));
    return ESP_OK;
  }
  char response[192];
  std::snprintf(
      response,
      sizeof(response),
      "{\"ok\":true,\"provisioning_reset\":%s,\"wifi_reset\":%s,\"settings_reset\":%s,\"calibration_reset\":%s}",
      bool_json(reset_provisioning).c_str(),
      bool_json(reset_wifi).c_str(),
      bool_json(reset_settings).c_str(),
      bool_json(reset_calibration).c_str());
  send_json(req, "200 OK", response);
  return ESP_OK;
}

esp_err_t boot_select_post_handler(httpd_req_t *req) {
  bool ok = false;
  const std::string body = read_json_body(req, &ok);
  if (!ok) {
    send_error_json(req, "400 Bad Request", "invalid_json_body", "Expected a small JSON boot-select body");
    return ESP_OK;
  }
  cJSON *root = cJSON_ParseWithLength(body.data(), body.size());
  const cJSON *label = cJSON_IsObject(root) ? cJSON_GetObjectItem(root, "partition_label") : nullptr;
  if (!cJSON_IsString(label) || label->valuestring[0] == '\0') {
    cJSON_Delete(root);
    send_error_json(req, "400 Bad Request", "invalid_boot_selection", "partition_label is required");
    return ESP_OK;
  }
  const esp_partition_t *partition = esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, label->valuestring);
  if (partition == nullptr || partition->subtype == ESP_PARTITION_SUBTYPE_APP_FACTORY) {
    cJSON_Delete(root);
    send_error_json(req, "400 Bad Request", "invalid_boot_partition", "Only endpoint OTA app partitions may be selected");
    return ESP_OK;
  }
  const esp_err_t err = esp_ota_set_boot_partition(partition);
  cJSON_Delete(root);
  if (err != ESP_OK) {
    send_error_json(req, "500 Internal Server Error", "boot_select_failed", esp_err_to_name(err));
    return ESP_OK;
  }
  send_json(req, "200 OK", "{\"ok\":true,\"message\":\"Boot partition selected; reboot when ready\"}");
  return ESP_OK;
}

esp_err_t firmware_install_post_handler(httpd_req_t *req) {
  InstallHeaders headers = {};
  char error_code[48] = {};
  if (!read_install_headers(req, &headers, error_code, sizeof(error_code))) {
    send_error_json(req, "400 Bad Request", error_code, "Firmware install metadata was rejected");
    return ESP_OK;
  }

  const esp_partition_t *update_partition = esp_ota_get_next_update_partition(nullptr);
  if (update_partition == nullptr || headers.size_bytes > static_cast<int>(update_partition->size)) {
    send_error_json(req, "400 Bad Request", "image_too_large", "No inactive OTA slot can hold this endpoint image");
    return ESP_OK;
  }

  esp_ota_handle_t ota_handle = 0;
  esp_err_t err = esp_ota_begin(update_partition, headers.size_bytes, &ota_handle);
  if (err != ESP_OK) {
    send_error_json(req, "500 Internal Server Error", "ota_begin_failed", esp_err_to_name(err));
    return ESP_OK;
  }

  mbedtls_md_context_t sha256;
  mbedtls_md_init(&sha256);
  const mbedtls_md_info_t *sha256_info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  err = (sha256_info != nullptr && mbedtls_md_setup(&sha256, sha256_info, 0) == 0 && mbedtls_md_starts(&sha256) == 0)
            ? ESP_OK
            : ESP_FAIL;

  std::array<char, kInstallBufferBytes> buffer = {};
  int received = 0;
  while (err == ESP_OK && received < req->content_len) {
    const int remaining = static_cast<int>(req->content_len) - received;
    const int to_read = std::min(static_cast<int>(buffer.size()), remaining);
    const int chunk = httpd_req_recv(req, buffer.data(), to_read);
    if (chunk == HTTPD_SOCK_ERR_TIMEOUT) {
      continue;
    }
    if (chunk <= 0) {
      err = ESP_FAIL;
      break;
    }
    if (mbedtls_md_update(&sha256, reinterpret_cast<const unsigned char *>(buffer.data()), chunk) != 0) {
      err = ESP_FAIL;
      break;
    }
    err = esp_ota_write(ota_handle, buffer.data(), chunk);
    received += chunk;
  }

  unsigned char digest[32] = {};
  char calculated_sha256[65] = {};
  if (err == ESP_OK && mbedtls_md_finish(&sha256, digest) == 0) {
    bytes_to_hex(digest, sizeof(digest), calculated_sha256, sizeof(calculated_sha256));
    if (!constant_time_equal(calculated_sha256, headers.sha256)) {
      err = ESP_ERR_INVALID_CRC;
    }
  } else if (err == ESP_OK) {
    err = ESP_FAIL;
  }
  mbedtls_md_free(&sha256);

  if (err == ESP_OK) {
    err = esp_ota_end(ota_handle);
    ota_handle = 0;
  }
  if (err == ESP_OK) {
    err = esp_ota_set_boot_partition(update_partition);
  }
  if (err != ESP_OK) {
    if (ota_handle != 0) {
      esp_ota_abort(ota_handle);
    }
    send_error_json(req, "500 Internal Server Error", "firmware_install_failed", esp_err_to_name(err));
    return ESP_OK;
  }

  char response[256];
  std::snprintf(
      response,
      sizeof(response),
      "{\"ok\":true,\"installed_partition\":\"%s\",\"version\":\"%s\",\"sha256\":\"%s\","
      "\"reboot_required\":%s,\"reboot_scheduled\":%s}",
      update_partition->label,
      json_escape(headers.version).c_str(),
      headers.sha256,
      bool_json(!headers.reboot_after_install).c_str(),
      bool_json(headers.reboot_after_install).c_str());
  send_json(req, "200 OK", response);
  if (headers.reboot_after_install) {
    schedule_reboot_after_install();
  }
  return ESP_OK;
}

void register_uri(const char *uri, httpd_method_t method, esp_err_t (*handler)(httpd_req_t *)) {
  httpd_uri_t route = {};
  route.uri = uri;
  route.method = method;
  route.handler = handler;
  route.user_ctx = nullptr;
  const esp_err_t err = httpd_register_uri_handler(g_http_server, &route);
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to register recovery route %s: %s", uri, esp_err_to_name(err));
  }
}

void start_http_server(bool full_rescue_routes) {
  if (g_http_server != nullptr) {
    return;
  }

  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.uri_match_fn = httpd_uri_match_wildcard;
  const esp_err_t err = httpd_start(&g_http_server, &config);
  if (err != ESP_OK) {
    ESP_LOGE(kTag, "Failed to start recovery HTTP API: %s", esp_err_to_name(err));
    return;
  }

  register_uri("/", HTTP_GET, root_handler);
  register_uri("/api/recovery/status", HTTP_GET, status_handler);
  register_uri("/api/recovery/partitions", HTTP_GET, partitions_handler);
  register_uri("/api/recovery/diagnostics", HTTP_GET, diagnostics_handler);
  register_uri("/api/recovery/ble/status", HTTP_GET, ble_status_handler);
  register_uri("/api/recovery/firmware/install", HTTP_POST, firmware_install_post_handler);
  if (full_rescue_routes) {
    register_uri("/api/recovery/wifi", HTTP_POST, wifi_post_handler);
    register_uri("/api/recovery/endpoint", HTTP_POST, endpoint_post_handler);
    register_uri("/api/recovery/boot/select", HTTP_POST, boot_select_post_handler);
    register_uri("/api/recovery/config/reset", HTTP_POST, reset_post_handler);
  }
  g_http_api_active = true;
  g_full_http_rescue_active = full_rescue_routes;
  ESP_LOGI(kTag, "Recovery HTTP API started on port 80 mode=%s", hexe::recovery::recovery_http_mode());
}

}  // namespace

namespace hexe::recovery {

void init_recovery_controls() {
  const bool wifi_recovery_enabled = recovery_wifi_recovery_enabled();
  if (wifi_recovery_enabled) {
    start_recovery_wifi(true);
  } else {
    update_network_mode();
    ESP_LOGI(kTag, "Recovery Wi-Fi/HTTP disabled for BLE-only board profile %s", hexe::board::pins::kBoardProfile);
  }
  init_recovery_ble_provisioning();
  if (wifi_recovery_enabled) {
    start_http_server(true);
  }
}

bool recovery_wifi_recovery_enabled() {
  return std::strcmp(hexe::board::pins::kBoardProfile, "ha_voice_pe") != 0;
}

bool recovery_full_http_rescue_enabled() {
  return g_full_http_rescue_active;
}

bool start_recovery_wifi_after_ble_credentials() {
  ESP_LOGI(kTag, "Starting recovery STA from BLE credentials");
  return start_recovery_wifi(false);
}

bool recovery_http_api_active() {
  return g_http_api_active;
}

const char *recovery_http_mode() {
  if (!g_http_api_active) {
    return "disabled";
  }
  return g_full_http_rescue_active ? "full_rescue" : "firmware_install_only";
}

const char *recovery_network_mode() {
  return g_network_mode;
}

const char *recovery_ip_address() {
  return g_ip_address;
}

bool recovery_temporary_ap_active() {
  return g_temporary_ap_active;
}

const char *recovery_discovery_status() {
  return g_recovery_discovery_status;
}

std::string render_partitions_json() {
  const esp_partition_t *running_partition = esp_ota_get_running_partition();
  const esp_partition_t *boot_partition = esp_ota_get_boot_partition();
  const esp_partition_t *next_update = esp_ota_get_next_update_partition(nullptr);
  std::string json =
      "{\"schema_version\":\"hexe-recovery-partitions-v1\",\"application_type\":\"recovery\",\"recovery_api_version\":\"";
  json.append(kRecoveryApiVersion);
  json.append("\",\"board_profile\":\"");
  json.append(json_escape(hexe::board::pins::kBoardProfile));
  json.append("\",\"partition_schema\":\"");
  json.append(json_escape(hexe::board::pins::kPartitionSchema));
  json.append("\",\"running_partition\":\"");
  json.append(running_partition == nullptr ? "unknown" : json_escape(running_partition->label));
  json.append("\",\"boot_partition\":\"");
  json.append(boot_partition == nullptr ? "unknown" : json_escape(boot_partition->label));
  json.append("\",\"next_update_partition\":\"");
  json.append(next_update == nullptr ? "unknown" : json_escape(next_update->label));
  json.append("\",\"partitions\":[");

  bool first = true;
  esp_partition_iterator_t iterator = esp_partition_find(ESP_PARTITION_TYPE_ANY, ESP_PARTITION_SUBTYPE_ANY, nullptr);
  while (iterator != nullptr) {
    const esp_partition_t *partition = esp_partition_get(iterator);
    if (partition != nullptr) {
      if (!first) {
        json.push_back(',');
      }
      first = false;
      esp_ota_img_states_t ota_state = ESP_OTA_IMG_UNDEFINED;
      const bool state_readable = partition->type == ESP_PARTITION_TYPE_APP &&
                                  esp_ota_get_state_partition(partition, &ota_state) == ESP_OK;
      char item[384];
      std::snprintf(
          item,
          sizeof(item),
          "{\"label\":\"%s\",\"type\":\"%s\",\"subtype\":\"%s\",\"offset\":%lu,\"size\":%lu,"
          "\"running\":%s,\"selected_for_boot\":%s,\"next_update\":%s,\"state\":\"%s\",\"state_readable\":%s}",
          json_escape(partition->label).c_str(),
          partition_type_name(partition->type),
          partition_subtype_name(partition->type, partition->subtype),
          static_cast<unsigned long>(partition->address),
          static_cast<unsigned long>(partition->size),
          bool_json(partition == running_partition).c_str(),
          bool_json(partition == boot_partition).c_str(),
          bool_json(partition == next_update).c_str(),
          ota_state_name(ota_state),
          bool_json(state_readable).c_str());
      json.append(item);
    }
    iterator = esp_partition_next(iterator);
  }
  if (iterator != nullptr) {
    esp_partition_iterator_release(iterator);
  }
  json.append("]}");
  return json;
}

std::string render_diagnostics_json() {
  char body[1024];
  std::snprintf(
      body,
      sizeof(body),
      "{\"schema_version\":\"hexe-recovery-diagnostics-v1\",\"application_type\":\"recovery\","
      "\"recovery_api_version\":\"%s\",\"board_profile\":\"%s\",\"soc\":\"%s\",\"idf_target\":\"%s\","
      "\"network\":{\"mode\":\"%s\",\"ip_address\":\"%s\",\"temporary_ap_active\":%s,\"station_configured\":%s},"
      "\"interfaces\":{\"serial_console\":true,\"http_api\":%s,\"http_mode\":\"%s\",\"status_page\":%s,\"ble\":false},"
      "\"subsystems\":{\"core_required\":false,\"sd_required\":false,\"wake_models_linked\":false,"
      "\"endpoint_runtime_linked\":false,\"speaker_id_linked\":false},"
      "\"actions\":{\"wifi_provisioning\":%s,\"endpoint_provisioning\":%s,\"firmware_upload\":%s,"
      "\"partition_inspection\":%s,\"boot_select\":%s,\"selective_config_reset\":%s}}",
      kRecoveryApiVersion,
      hexe::board::pins::kBoardProfile,
      hexe::board::pins::kSoc,
      hexe::board::pins::kIdfTarget,
      g_network_mode,
      g_ip_address,
      bool_json(g_temporary_ap_active).c_str(),
      bool_json(g_station_configured).c_str(),
      bool_json(g_http_api_active).c_str(),
      recovery_http_mode(),
      bool_json(g_http_api_active).c_str(),
      bool_json(g_full_http_rescue_active).c_str(),
      bool_json(g_full_http_rescue_active).c_str(),
      bool_json(g_http_api_active).c_str(),
      bool_json(g_full_http_rescue_active).c_str(),
      bool_json(g_full_http_rescue_active).c_str(),
      bool_json(g_full_http_rescue_active).c_str());
  return std::string(body);
}

}  // namespace hexe::recovery
