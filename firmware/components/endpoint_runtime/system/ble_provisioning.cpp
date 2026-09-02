#include "system/ble_provisioning.h"

#include <cstdio>
#include <cstring>
#include <cinttypes>
#include <string>

#include "board/wifi.h"
#include "board_profile_pins.h"
#include "cJSON.h"
#include "endpoint_config.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "system/settings.h"

extern "C" int hexe_ble_provisioning_gatt_init(const char *device_name);
extern "C" int hexe_ble_provisioning_gatt_set_advertising(int enabled);

namespace {
constexpr char kTag[] = "hexe_ble_prov";
constexpr int64_t kPairingTtlUs = 10LL * 60LL * 1000000LL;
constexpr size_t kMaxEncryptedEnvelopeBytes = 4096;

struct BleProvisioningState {
  bool initialized{false};
  bool gatt_ready{false};
  bool advertising{false};
  char state[24]{"idle"};
  char reason[48]{"not_started"};
  char last_ack[32]{""};
  char last_error[48]{""};
  char onboarding_session_id[64]{""};
  char pairing_nonce[48]{""};
  uint32_t last_sequence{0};
  int64_t issued_at_us{0};
};

BleProvisioningState g_ble;

bool nimble_config_enabled() {
#if defined(CONFIG_BT_ENABLED) && defined(CONFIG_BT_NIMBLE_ENABLED) && defined(CONFIG_BT_NIMBLE_ROLE_PERIPHERAL) && \
    defined(CONFIG_BT_NIMBLE_GATT_SERVER)
  return true;
#else
  return false;
#endif
}

bool board_supported() {
  return hexe::board::pins::kBleOnboardingSupported;
}

bool eligible_for_advertising() {
  return board_supported() && !hexe::system::provisioning_configured();
}

void set_state(const char *state, const char *reason) {
  std::strncpy(g_ble.state, state == nullptr ? "idle" : state, sizeof(g_ble.state) - 1);
  g_ble.state[sizeof(g_ble.state) - 1] = '\0';
  std::strncpy(g_ble.reason, reason == nullptr ? "" : reason, sizeof(g_ble.reason) - 1);
  g_ble.reason[sizeof(g_ble.reason) - 1] = '\0';
}

void set_ack(const char *ack) {
  std::strncpy(g_ble.last_ack, ack == nullptr ? "" : ack, sizeof(g_ble.last_ack) - 1);
  g_ble.last_ack[sizeof(g_ble.last_ack) - 1] = '\0';
  g_ble.last_error[0] = '\0';
}

void set_error(const char *error) {
  std::strncpy(g_ble.last_error, error == nullptr ? "invalid_payload" : error, sizeof(g_ble.last_error) - 1);
  g_ble.last_error[sizeof(g_ble.last_error) - 1] = '\0';
  g_ble.last_ack[0] = '\0';
  set_state("failed", g_ble.last_error);
}

const char *hardware_id() {
  static char buffer[32] = "";
  if (buffer[0] != '\0') {
    return buffer;
  }
  uint8_t mac[6] = {};
  if (esp_efuse_mac_get_default(mac) != ESP_OK) {
    std::snprintf(buffer, sizeof(buffer), "esp32-unknown");
    return buffer;
  }
  std::snprintf(
      buffer,
      sizeof(buffer),
      "esp32-%02x%02x%02x%02x%02x%02x",
      mac[0],
      mac[1],
      mac[2],
      mac[3],
      mac[4],
      mac[5]);
  return buffer;
}

void ensure_pairing_nonce() {
  if (g_ble.pairing_nonce[0] != '\0' && (esp_timer_get_time() - g_ble.issued_at_us) < kPairingTtlUs) {
    return;
  }
  const uint32_t a = esp_random();
  const uint32_t b = esp_random();
  const uint32_t c = esp_random();
  const uint32_t d = esp_random();
  std::snprintf(g_ble.pairing_nonce, sizeof(g_ble.pairing_nonce), "%08" PRIx32 "%08" PRIx32 "%08" PRIx32 "%08" PRIx32, a, b, c, d);
  std::snprintf(g_ble.onboarding_session_id, sizeof(g_ble.onboarding_session_id), "ble-%08" PRIx32 "%08" PRIx32, esp_random(), esp_random());
  g_ble.issued_at_us = esp_timer_get_time();
}

bool string_field(cJSON *obj, const char *key, const char **value) {
  cJSON *field = cJSON_IsObject(obj) ? cJSON_GetObjectItem(obj, key) : nullptr;
  if (!cJSON_IsString(field) || field->valuestring == nullptr || field->valuestring[0] == '\0') {
    return false;
  }
  *value = field->valuestring;
  return true;
}

bool optional_string_field(cJSON *obj, const char *key, const char **value) {
  cJSON *field = cJSON_IsObject(obj) ? cJSON_GetObjectItem(obj, key) : nullptr;
  if (field == nullptr || cJSON_IsNull(field)) {
    *value = "";
    return true;
  }
  if (!cJSON_IsString(field) || field->valuestring == nullptr) {
    return false;
  }
  *value = field->valuestring;
  return true;
}

bool int_field(cJSON *obj, const char *key, int *value) {
  cJSON *field = cJSON_IsObject(obj) ? cJSON_GetObjectItem(obj, key) : nullptr;
  if (!cJSON_IsNumber(field)) {
    return false;
  }
  *value = field->valueint;
  return true;
}

bool bool_field(cJSON *obj, const char *key, bool *value) {
  cJSON *field = cJSON_IsObject(obj) ? cJSON_GetObjectItem(obj, key) : nullptr;
  if (!cJSON_IsBool(field)) {
    return false;
  }
  *value = cJSON_IsTrue(field);
  return true;
}

bool bounded_copy(char *target, size_t target_size, const char *value, size_t min_len, size_t max_len) {
  const size_t length = std::strlen(value == nullptr ? "" : value);
  if (length < min_len || length > max_len || length >= target_size) {
    return false;
  }
  std::strncpy(target, value, target_size - 1);
  target[target_size - 1] = '\0';
  return true;
}

bool validate_and_save_payload(cJSON *payload) {
  const char *wifi_name = nullptr;
  const char *wifi_pass = "";
  const char *backend_host = nullptr;
  const char *endpoint_name = "";
  const char *display_name = "";
  int http_port = 0;
  int ws_port = 0;
  bool use_tls = true;

  if (!string_field(payload, "wifi_ssid", &wifi_name) ||
      !optional_string_field(payload, "wifi_password", &wifi_pass) ||
      !string_field(payload, "backend_host", &backend_host) ||
      !int_field(payload, "http_port", &http_port) ||
      !int_field(payload, "ws_port", &ws_port) ||
      !bool_field(payload, "use_tls", &use_tls) ||
      !optional_string_field(payload, "endpoint_name", &endpoint_name) ||
      !optional_string_field(payload, "display_name", &display_name)) {
    set_error("invalid_payload");
    return false;
  }
  if ((wifi_pass[0] != '\0' && (std::strlen(wifi_pass) < 8 || std::strlen(wifi_pass) > 63)) ||
      http_port < 1 || http_port > 65535 || ws_port < 1 || ws_port > 65535) {
    set_error("invalid_payload");
    return false;
  }

  hexe::system::EndpointProvisioningSettings settings = hexe::system::endpoint_provisioning_settings();
  if (!bounded_copy(settings.wifi_ssid, sizeof(settings.wifi_ssid), wifi_name, 1, 32) ||
      !bounded_copy(settings.backend_host, sizeof(settings.backend_host), backend_host, 1, 95) ||
      !bounded_copy(settings.wifi_password, sizeof(settings.wifi_password), wifi_pass, 0, 63)) {
    set_error("invalid_payload");
    return false;
  }
  if (endpoint_name[0] != '\0' && !bounded_copy(settings.endpoint_id, sizeof(settings.endpoint_id), endpoint_name, 1, 63)) {
    set_error("invalid_payload");
    return false;
  }
  if (display_name[0] != '\0' && !bounded_copy(settings.display_name, sizeof(settings.display_name), display_name, 1, 63)) {
    set_error("invalid_payload");
    return false;
  }
  if (settings.display_name[0] == '\0') {
    std::strncpy(settings.display_name, settings.endpoint_id, sizeof(settings.display_name) - 1);
    settings.display_name[sizeof(settings.display_name) - 1] = '\0';
  }
  settings.http_port = http_port;
  settings.ws_port = ws_port;
  settings.use_tls = use_tls;

  if (!hexe::system::save_endpoint_provisioning(settings)) {
    set_error("wifi_apply_failed");
    return false;
  }
  set_ack("succeeded");
  set_state("completed", "credentials_applied");
  hexe::board::reconnect_wifi();
  return true;
}

bool validate_envelope_binding(cJSON *root) {
  const char *schema_version = nullptr;
  const char *payload_schema_id = nullptr;
  const char *contract_version = nullptr;
  const char *target_node_id = nullptr;
  const char *pairing_nonce = nullptr;
  const char *algorithm = nullptr;
  const char *key_id = nullptr;
  const char *nonce = nullptr;
  const char *ciphertext = nullptr;
  int sequence = 0;

  if (!string_field(root, "schema_version", &schema_version) ||
      !string_field(root, "payload_schema_id", &payload_schema_id) ||
      !string_field(root, "contract_version", &contract_version) ||
      !string_field(root, "target_node_id", &target_node_id) ||
      !string_field(root, "pairing_nonce", &pairing_nonce) ||
      !string_field(root, "algorithm", &algorithm) ||
      !string_field(root, "key_id", &key_id) ||
      !string_field(root, "nonce", &nonce) ||
      !string_field(root, "ciphertext", &ciphertext) ||
      !int_field(root, "sequence", &sequence)) {
    set_error("invalid_payload");
    return false;
  }
  if (std::strcmp(schema_version, "1.0") != 0 ||
      std::strcmp(contract_version, hexe::system::kBleProvisioningContractVersion) != 0) {
    set_error("unsupported_schema");
    return false;
  }
  if (std::strcmp(payload_schema_id, hexe::system::kBleProvisioningPayloadSchemaId) != 0) {
    set_error("unsupported_schema");
    return false;
  }
  if (std::strcmp(target_node_id, hexe::system::endpoint_id()) != 0 && std::strcmp(target_node_id, hardware_id()) != 0) {
    set_error("invalid_payload");
    return false;
  }
  if (std::strcmp(pairing_nonce, g_ble.pairing_nonce) != 0) {
    set_error("invalid_nonce");
    return false;
  }
  if (sequence <= 0 || static_cast<uint32_t>(sequence) <= g_ble.last_sequence) {
    set_error("invalid_payload");
    return false;
  }
  g_ble.last_sequence = static_cast<uint32_t>(sequence);
  if (std::strcmp(algorithm, "xchacha20poly1305") != 0 && std::strcmp(algorithm, "aes-256-gcm") != 0) {
    set_error("decrypt_failed");
    return false;
  }
  (void) key_id;
  (void) nonce;
  (void) ciphertext;
  return true;
}

std::string print_json(cJSON *root) {
  char *rendered = cJSON_PrintUnformatted(root);
  std::string result = rendered == nullptr ? "{}" : rendered;
  cJSON_free(rendered);
  cJSON_Delete(root);
  return result;
}
}  // namespace

namespace hexe::system {

void init_ble_provisioning() {
  if (g_ble.initialized) {
    return;
  }
  g_ble.initialized = true;
  if (!board_supported()) {
    set_state("idle", "board_profile_unsupported");
    return;
  }
  if (!nimble_config_enabled()) {
    set_state("failed", "nimble_disabled");
    std::strncpy(g_ble.last_error, "nimble_disabled", sizeof(g_ble.last_error) - 1);
    return;
  }
  ensure_pairing_nonce();
  const int rc = hexe_ble_provisioning_gatt_init(endpoint_display_name());
  g_ble.gatt_ready = rc == 0;
  if (!g_ble.gatt_ready) {
    set_error("gatt_backend_unavailable");
    ESP_LOGW(kTag, "BLE onboarding GATT init failed: %d", rc);
    return;
  }
  set_state("awaiting_credentials", eligible_for_advertising() ? "advertising_allowed" : "already_provisioned");
  update_ble_provisioning();
}

void update_ble_provisioning() {
  if (!g_ble.initialized || !g_ble.gatt_ready) {
    return;
  }
  const bool should_advertise = eligible_for_advertising();
  if (should_advertise) {
    ensure_pairing_nonce();
  }
  if (should_advertise == g_ble.advertising) {
    return;
  }
  if (hexe_ble_provisioning_gatt_set_advertising(should_advertise ? 1 : 0) == 0) {
    g_ble.advertising = should_advertise;
    set_state(should_advertise ? "awaiting_credentials" : "idle", should_advertise ? "advertising" : "already_provisioned");
  }
}

BleProvisioningStatus ble_provisioning_status() {
  const bool supported = board_supported();
  const bool enabled = supported && nimble_config_enabled() && g_ble.gatt_ready;
  const bool eligible = eligible_for_advertising();
  return {
      supported,
      enabled,
      eligible,
      g_ble.advertising,
      provisioning_configured(),
      hexe::board::pins::kBleOnboardingTransport,
      g_ble.state,
      g_ble.reason,
      g_ble.last_ack,
      g_ble.last_error,
      g_ble.issued_at_us == 0 ? 0 : (g_ble.issued_at_us + kPairingTtlUs) / 1000};
}

std::string ble_provisioning_device_identity_json() {
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "contract_version", kBleProvisioningContractVersion);
  cJSON_AddStringToObject(root, "node_hardware_id", hardware_id());
  cJSON_AddStringToObject(root, "target_node_id", endpoint_id());
  cJSON_AddStringToObject(root, "board_profile", hexe::config::kEndpointBoardProfile);
  cJSON_AddStringToObject(root, "firmware_version", esp_app_get_description()->version);
  cJSON_AddStringToObject(root, "protocol_version", kBleProvisioningContractVersion);
  cJSON *schemas = cJSON_AddArrayToObject(root, "supported_payload_schemas");
  cJSON_AddItemToArray(schemas, cJSON_CreateString(kBleProvisioningPayloadSchemaId));
  cJSON_AddStringToObject(root, "provisioning_state", g_ble.state);
  return print_json(root);
}

std::string ble_provisioning_pairing_nonce_json() {
  ensure_pairing_nonce();
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "onboarding_session_id", g_ble.onboarding_session_id);
  cJSON_AddStringToObject(root, "target_node_id", endpoint_id());
  cJSON_AddStringToObject(root, "pairing_nonce", g_ble.pairing_nonce);
  cJSON_AddBoolToObject(root, "claim_code_required", false);
  cJSON_AddNumberToObject(root, "expires_at_unix_ms", (g_ble.issued_at_us + kPairingTtlUs) / 1000);
  return print_json(root);
}

std::string ble_provisioning_status_json() {
  const BleProvisioningStatus status = ble_provisioning_status();
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "state", status.state);
  cJSON_AddBoolToObject(root, "supported", status.supported);
  cJSON_AddBoolToObject(root, "enabled", status.enabled);
  cJSON_AddBoolToObject(root, "eligible", status.eligible);
  cJSON_AddBoolToObject(root, "advertising", status.advertising);
  cJSON_AddBoolToObject(root, "provisioned", status.provisioned);
  cJSON_AddStringToObject(root, "reason", status.reason);
  cJSON_AddNumberToObject(root, "expires_at_unix_ms", status.expires_at_unix_ms);
  return print_json(root);
}

std::string ble_provisioning_ack_error_json() {
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "ack", g_ble.last_ack);
  cJSON_AddStringToObject(root, "error", g_ble.last_error);
  cJSON_AddStringToObject(root, "state", g_ble.state);
  return print_json(root);
}

bool ble_provisioning_handle_encrypted_credentials(const char *json, size_t length) {
  if (provisioning_configured()) {
    set_error("already_provisioned");
    return false;
  }
  if (json == nullptr || length == 0 || length > kMaxEncryptedEnvelopeBytes) {
    set_error("invalid_payload");
    return false;
  }
  set_state("validating", "encrypted_envelope_received");
  cJSON *root = cJSON_ParseWithLength(json, length);
  if (root == nullptr) {
    set_error("invalid_payload");
    return false;
  }
  const bool binding_ok = validate_envelope_binding(root);
  cJSON_Delete(root);
  if (!binding_ok) {
    return false;
  }
  set_error("decrypt_failed");
  return false;
}

bool ble_provisioning_apply_decrypted_payload_for_test(const char *json) {
  cJSON *root = cJSON_Parse(json);
  if (root == nullptr) {
    set_error("invalid_payload");
    return false;
  }
  set_state("applying", "decrypted_payload_validated");
  const bool saved = validate_and_save_payload(root);
  cJSON_Delete(root);
  update_ble_provisioning();
  return saved;
}

}  // namespace hexe::system

extern "C" const char *hexe_ble_provisioning_device_identity_json() {
  static std::string payload;
  payload = hexe::system::ble_provisioning_device_identity_json();
  return payload.c_str();
}

extern "C" const char *hexe_ble_provisioning_pairing_nonce_json() {
  static std::string payload;
  payload = hexe::system::ble_provisioning_pairing_nonce_json();
  return payload.c_str();
}

extern "C" const char *hexe_ble_provisioning_status_json() {
  static std::string payload;
  payload = hexe::system::ble_provisioning_status_json();
  return payload.c_str();
}

extern "C" const char *hexe_ble_provisioning_ack_error_json() {
  static std::string payload;
  payload = hexe::system::ble_provisioning_ack_error_json();
  return payload.c_str();
}

extern "C" int hexe_ble_provisioning_handle_encrypted_credentials(const char *json, unsigned int length) {
  return hexe::system::ble_provisioning_handle_encrypted_credentials(json, length) ? 0 : -1;
}
