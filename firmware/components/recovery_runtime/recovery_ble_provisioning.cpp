#include "recovery_ble_provisioning.h"

#include <cstdio>
#include <cstring>
#include <cinttypes>
#include <string>

#include "board_profile_pins.h"
#include "cJSON.h"
#include "endpoint_config.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "nvs.h"

extern "C" int hexe_ble_provisioning_gatt_init(const char *device_name);
extern "C" int hexe_ble_provisioning_gatt_set_advertising(int enabled);
extern "C" int hexe_ble_pairing_central_set_scanning(int enabled);

namespace {
constexpr char kTag[] = "hexe_recovery_ble";
constexpr int64_t kPairingTtlUs = 10LL * 60LL * 1000000LL;
constexpr size_t kMaxBleBodyBytes = 4096;
constexpr char kSettingsNamespace[] = "hexe_settings";
constexpr char kContractVersion[] = "1.0";
constexpr char kPayloadSchemaId[] = "hexe.voice_node.wifi_backend.v1";
constexpr char kOperation[] = "ble.provision_wifi";
constexpr char kLeaseScope[] = "hardware.bluetooth.ble.provision_wifi";
constexpr char kServiceUuid[] = "7f9c0000-5f04-4d8b-9a46-7c0f7a100000";
constexpr char kDeviceIdentityUuid[] = "7f9c0000-5f04-4d8b-9a46-7c0f7a100001";
constexpr char kPairingNonceUuid[] = "7f9c0000-5f04-4d8b-9a46-7c0f7a100002";
constexpr char kStatusUuid[] = "7f9c0000-5f04-4d8b-9a46-7c0f7a100003";
constexpr char kEncryptedCredentialsUuid[] = "7f9c0000-5f04-4d8b-9a46-7c0f7a100004";
constexpr char kAckErrorUuid[] = "7f9c0000-5f04-4d8b-9a46-7c0f7a100005";

constexpr char kEndpointIdKey[] = "endpoint_id";
constexpr char kDisplayNameKey[] = "display_name";
constexpr char kBackendHostKey[] = "backend_host";
constexpr char kHttpPortKey[] = "http_port";
constexpr char kWsPortKey[] = "ws_port";
constexpr char kUseTlsKey[] = "use_tls";
constexpr char kWifiSsidKey[] = "wifi_ssid";
constexpr char kWifiPasswordKey[] = "wifi_password";
constexpr char kProvisionedKey[] = "provisioned";

struct RecoveryBleState {
  bool initialized{false};
  bool enabled{false};
  bool advertising{false};
  bool central_scanning{false};
  bool host_pairing_found{false};
  bool host_pairing_role_match{false};
  bool host_pairing_connected{false};
  bool host_pairing_offer_received{false};
  bool host_pairing_identity_sent{false};
  bool host_pairing_claim_code_required{false};
  char state[24]{"idle"};
  char reason[64]{"not_started"};
  char last_ack[32]{""};
  char last_error[64]{""};
  char host_pairing_address[18]{""};
  char host_pairing_name[40]{""};
  char host_pairing_session_id[64]{""};
  char host_pairing_session_hint[32]{""};
  char host_pairing_expires_at[32]{""};
  int host_pairing_rssi{0};
  int64_t host_pairing_seen_at_us{0};
  char onboarding_session_id[64]{""};
  char pairing_nonce[48]{""};
  int64_t issued_at_us{0};
};

RecoveryBleState g_ble;

bool nimble_config_enabled() {
#if defined(CONFIG_BT_ENABLED) && defined(CONFIG_BT_NIMBLE_ENABLED) && defined(CONFIG_BT_NIMBLE_ROLE_PERIPHERAL) && \
    defined(CONFIG_BT_NIMBLE_GATT_SERVER) && defined(CONFIG_BT_NIMBLE_ROLE_CENTRAL) && defined(CONFIG_BT_NIMBLE_GATT_CLIENT)
  return true;
#else
  return false;
#endif
}

bool board_supported() {
  return hexe::board::pins::kBleOnboardingSupported;
}

void copy_cstr(char *target, size_t target_size, const char *value) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  std::snprintf(target, target_size, "%s", value == nullptr ? "" : value);
}

void set_state(const char *state, const char *reason) {
  copy_cstr(g_ble.state, sizeof(g_ble.state), state == nullptr ? "idle" : state);
  copy_cstr(g_ble.reason, sizeof(g_ble.reason), reason == nullptr ? "" : reason);
}

void set_ack(const char *ack) {
  copy_cstr(g_ble.last_ack, sizeof(g_ble.last_ack), ack == nullptr ? "" : ack);
  g_ble.last_error[0] = '\0';
}

void set_error(const char *error) {
  copy_cstr(g_ble.last_error, sizeof(g_ble.last_error), error == nullptr ? "invalid_payload" : error);
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
    copy_cstr(buffer, sizeof(buffer), "esp32-unknown");
    return buffer;
  }
  std::snprintf(buffer, sizeof(buffer), "esp32-%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return buffer;
}

void ensure_pairing_nonce() {
  const bool existing_valid =
      g_ble.pairing_nonce[0] != '\0' && (esp_timer_get_time() - g_ble.issued_at_us) < kPairingTtlUs;
  if (existing_valid) {
    return;
  }
  std::snprintf(
      g_ble.pairing_nonce,
      sizeof(g_ble.pairing_nonce),
      "%08" PRIx32 "%08" PRIx32 "%08" PRIx32 "%08" PRIx32,
      esp_random(),
      esp_random(),
      esp_random(),
      esp_random());
  if (g_ble.host_pairing_session_id[0] != '\0') {
    copy_cstr(g_ble.onboarding_session_id, sizeof(g_ble.onboarding_session_id), g_ble.host_pairing_session_id);
  } else {
    std::snprintf(g_ble.onboarding_session_id, sizeof(g_ble.onboarding_session_id), "recovery-ble-%08" PRIx32 "%08" PRIx32, esp_random(), esp_random());
  }
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

bool optional_bool_field(cJSON *obj, const char *key, bool *value) {
  cJSON *field = cJSON_IsObject(obj) ? cJSON_GetObjectItem(obj, key) : nullptr;
  if (field == nullptr || cJSON_IsNull(field)) {
    return true;
  }
  if (!cJSON_IsBool(field)) {
    return false;
  }
  *value = cJSON_IsTrue(field);
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

bool valid_port(int port) {
  return port > 0 && port <= 65535;
}

bool set_nvs_string(nvs_handle_t handle, const char *key, const char *value, bool required) {
  if ((value == nullptr || value[0] == '\0') && required) {
    return false;
  }
  if (value == nullptr || value[0] == '\0') {
    return true;
  }
  return nvs_set_str(handle, key, value) == ESP_OK;
}

bool remember_host_pairing_offer(cJSON *root) {
  const char *onboarding_session_id = nullptr;
  const char *session_hint = "";
  const char *expires_at = "";
  bool claim_code_required = false;
  if (!string_field(root, "onboarding_session_id", &onboarding_session_id) ||
      !optional_string_field(root, "session_hint", &session_hint) ||
      !optional_string_field(root, "expires_at", &expires_at) ||
      !optional_bool_field(root, "claim_code_required", &claim_code_required)) {
    set_error("invalid_pairing_offer");
    return false;
  }
  cJSON *contract_field = cJSON_GetObjectItem(root, "contract_version");
  if (contract_field != nullptr && (!cJSON_IsString(contract_field) || std::strcmp(contract_field->valuestring, kContractVersion) != 0)) {
    set_error("unsupported_pairing_offer");
    return false;
  }
  cJSON *schema_field = cJSON_GetObjectItem(root, "payload_schema_id");
  if (schema_field != nullptr && (!cJSON_IsString(schema_field) || std::strcmp(schema_field->valuestring, kPayloadSchemaId) != 0)) {
    set_error("unsupported_pairing_offer");
    return false;
  }
  copy_cstr(g_ble.host_pairing_session_id, sizeof(g_ble.host_pairing_session_id), onboarding_session_id);
  copy_cstr(g_ble.onboarding_session_id, sizeof(g_ble.onboarding_session_id), onboarding_session_id);
  copy_cstr(g_ble.host_pairing_session_hint, sizeof(g_ble.host_pairing_session_hint), session_hint);
  copy_cstr(g_ble.host_pairing_expires_at, sizeof(g_ble.host_pairing_expires_at), expires_at);
  g_ble.host_pairing_claim_code_required = claim_code_required;
  g_ble.host_pairing_offer_received = true;
  g_ble.last_error[0] = '\0';
  set_state("pairing_offer_received", "host_pairing_offer");
  return true;
}

bool save_local_recovery_payload(cJSON *credential_payload) {
  const char *wifi_ssid = nullptr;
  const char *wifi_password = "";
  const char *backend_host = nullptr;
  const char *endpoint_name = nullptr;
  const char *display_name = "";
  int http_port = 0;
  int ws_port = 0;
  bool use_tls = true;

  if (!string_field(credential_payload, "wifi_ssid", &wifi_ssid) ||
      !optional_string_field(credential_payload, "wifi_password", &wifi_password) ||
      !string_field(credential_payload, "backend_host", &backend_host) ||
      !int_field(credential_payload, "http_port", &http_port) ||
      !int_field(credential_payload, "ws_port", &ws_port) ||
      !bool_field(credential_payload, "use_tls", &use_tls) ||
      !optional_string_field(credential_payload, "endpoint_name", &endpoint_name) ||
      !optional_string_field(credential_payload, "display_name", &display_name) ||
      !valid_port(http_port) ||
      !valid_port(ws_port)) {
    set_error("invalid_payload");
    return false;
  }

  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kSettingsNamespace, NVS_READWRITE, &handle);
  if (err != ESP_OK) {
    set_error("wifi_apply_failed");
    return false;
  }
  const bool saved =
      set_nvs_string(handle, kEndpointIdKey, endpoint_name[0] == '\0' ? hardware_id() : endpoint_name, true) &&
      set_nvs_string(handle, kDisplayNameKey, display_name, false) &&
      set_nvs_string(handle, kBackendHostKey, backend_host, true) &&
      set_nvs_string(handle, kWifiSsidKey, wifi_ssid, true) &&
      set_nvs_string(handle, kWifiPasswordKey, wifi_password, false) &&
      nvs_set_i32(handle, kHttpPortKey, http_port) == ESP_OK &&
      nvs_set_i32(handle, kWsPortKey, ws_port) == ESP_OK &&
      nvs_set_u8(handle, kUseTlsKey, use_tls ? 1 : 0) == ESP_OK &&
      nvs_set_u8(handle, kProvisionedKey, 1) == ESP_OK &&
      nvs_commit(handle) == ESP_OK;
  nvs_close(handle);
  if (!saved) {
    set_error("wifi_apply_failed");
    return false;
  }
  set_ack("succeeded");
  set_state("completed", "local_recovery_credentials_applied");
  return true;
}

bool handle_local_recovery_write(cJSON *root) {
  const char *mode = nullptr;
  const char *contract_version = nullptr;
  const char *onboarding_session_id = nullptr;
  const char *target_node_id = nullptr;
  const char *pairing_nonce = nullptr;
  if (!string_field(root, "mode", &mode) ||
      std::strcmp(mode, "local_recovery") != 0 ||
      !string_field(root, "contract_version", &contract_version) ||
      std::strcmp(contract_version, kContractVersion) != 0 ||
      !string_field(root, "onboarding_session_id", &onboarding_session_id) ||
      !string_field(root, "target_node_id", &target_node_id) ||
      !string_field(root, "pairing_nonce", &pairing_nonce)) {
    set_error("invalid_payload");
    return false;
  }
  if (std::strcmp(onboarding_session_id, g_ble.onboarding_session_id) != 0 ||
      (std::strcmp(target_node_id, hexe::config::kEndpointId) != 0 && std::strcmp(target_node_id, hardware_id()) != 0)) {
    set_error("invalid_payload");
    return false;
  }
  if (std::strcmp(pairing_nonce, g_ble.pairing_nonce) != 0) {
    set_error("invalid_nonce");
    return false;
  }
  cJSON *credential_payload = cJSON_GetObjectItem(root, "credential_payload");
  if (!cJSON_IsObject(credential_payload)) {
    set_error("invalid_payload");
    return false;
  }
  set_state("applying", "local_recovery_payload_validated");
  return save_local_recovery_payload(credential_payload);
}

std::string print_json(cJSON *root) {
  char *rendered = cJSON_PrintUnformatted(root);
  std::string result = rendered == nullptr ? "{}" : rendered;
  cJSON_free(rendered);
  cJSON_Delete(root);
  return result;
}

}  // namespace

namespace hexe::recovery {

void init_recovery_ble_provisioning() {
  if (g_ble.initialized) {
    return;
  }
  g_ble.initialized = true;
  if (!board_supported()) {
    set_state("idle", "board_profile_unsupported");
    return;
  }
  if (!nimble_config_enabled()) {
    set_error("nimble_disabled");
    return;
  }
  ensure_pairing_nonce();
  const int rc = hexe_ble_provisioning_gatt_init("HexeRecovery");
  if (rc != 0) {
    set_error("gatt_backend_unavailable");
    ESP_LOGW(kTag, "Recovery BLE GATT init failed: %d", rc);
    return;
  }
  g_ble.enabled = true;
  if (hexe_ble_provisioning_gatt_set_advertising(1) == 0) {
    g_ble.advertising = true;
    set_state("awaiting_credentials", "local_recovery_advertising");
  } else {
    set_error("gatt_backend_unavailable");
  }
  if (hexe_ble_pairing_central_set_scanning(1) != 0) {
    g_ble.central_scanning = false;
  }
}

bool recovery_ble_enabled() {
  return g_ble.enabled;
}

bool recovery_ble_advertising() {
  return g_ble.advertising;
}

const char *recovery_ble_state() {
  return g_ble.state;
}

const char *recovery_ble_reason() {
  return g_ble.reason;
}

std::string render_recovery_ble_status_json() {
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "schema_version", "hexe-recovery-ble-status-v1");
  cJSON_AddStringToObject(root, "operation", kOperation);
  cJSON_AddStringToObject(root, "lease_scope", kLeaseScope);
  cJSON_AddStringToObject(root, "contract_version", kContractVersion);
  cJSON_AddStringToObject(root, "payload_schema_id", kPayloadSchemaId);
  cJSON_AddBoolToObject(root, "supported", board_supported());
  cJSON_AddBoolToObject(root, "enabled", g_ble.enabled);
  cJSON_AddBoolToObject(root, "advertising", g_ble.advertising);
  cJSON_AddBoolToObject(root, "central_scanning", g_ble.central_scanning);
  cJSON_AddStringToObject(root, "mode", "local_recovery");
  cJSON_AddStringToObject(root, "core_governed_mode", "endpoint_app");
  cJSON_AddStringToObject(root, "state", g_ble.state);
  cJSON_AddStringToObject(root, "reason", g_ble.reason);
  cJSON_AddStringToObject(root, "service_uuid", kServiceUuid);
  cJSON *host_pairing = cJSON_AddObjectToObject(root, "host_pairing");
  cJSON_AddBoolToObject(host_pairing, "found", g_ble.host_pairing_found);
  cJSON_AddBoolToObject(host_pairing, "role_match", g_ble.host_pairing_role_match);
  cJSON_AddBoolToObject(host_pairing, "connected", g_ble.host_pairing_connected);
  cJSON_AddBoolToObject(host_pairing, "offer_received", g_ble.host_pairing_offer_received);
  cJSON_AddBoolToObject(host_pairing, "identity_sent", g_ble.host_pairing_identity_sent);
  cJSON_AddBoolToObject(host_pairing, "claim_code_required", g_ble.host_pairing_claim_code_required);
  if (g_ble.host_pairing_address[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "address", g_ble.host_pairing_address);
  }
  if (g_ble.host_pairing_name[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "name", g_ble.host_pairing_name);
  }
  if (g_ble.host_pairing_session_id[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "onboarding_session_id", g_ble.host_pairing_session_id);
  }
  if (g_ble.host_pairing_session_hint[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "session_hint", g_ble.host_pairing_session_hint);
  }
  if (g_ble.host_pairing_expires_at[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "expires_at", g_ble.host_pairing_expires_at);
  }
  cJSON_AddNumberToObject(host_pairing, "rssi", g_ble.host_pairing_rssi);
  cJSON_AddNumberToObject(host_pairing, "seen_at_unix_ms", g_ble.host_pairing_seen_at_us == 0 ? 0 : g_ble.host_pairing_seen_at_us / 1000);
  cJSON_AddStringToObject(root, "device_identity_uuid", kDeviceIdentityUuid);
  cJSON_AddStringToObject(root, "pairing_nonce_uuid", kPairingNonceUuid);
  cJSON_AddStringToObject(root, "provisioning_status_uuid", kStatusUuid);
  cJSON_AddStringToObject(root, "encrypted_credentials_uuid", kEncryptedCredentialsUuid);
  cJSON_AddStringToObject(root, "ack_error_uuid", kAckErrorUuid);
  if (g_ble.last_ack[0] != '\0') {
    cJSON_AddStringToObject(root, "last_ack", g_ble.last_ack);
  }
  if (g_ble.last_error[0] != '\0') {
    cJSON_AddStringToObject(root, "last_error", g_ble.last_error);
  }
  return print_json(root);
}

}  // namespace hexe::recovery

extern "C" const char *hexe_ble_provisioning_device_identity_json() {
  static std::string payload;
  cJSON *root = cJSON_CreateObject();
  ensure_pairing_nonce();
  cJSON_AddStringToObject(root, "contract_version", kContractVersion);
  cJSON_AddStringToObject(root, "onboarding_session_id", g_ble.onboarding_session_id);
  cJSON_AddStringToObject(root, "device_id", hexe::config::kEndpointId);
  cJSON_AddStringToObject(root, "node_hardware_id", hardware_id());
  cJSON_AddStringToObject(root, "target_node_id", hexe::config::kEndpointId);
  cJSON_AddStringToObject(root, "pairing_nonce", g_ble.pairing_nonce);
  cJSON_AddStringToObject(root, "board_profile", hexe::board::pins::kBoardProfile);
  cJSON_AddStringToObject(root, "firmware_version", esp_app_get_description()->version);
  cJSON_AddStringToObject(root, "application_type", "recovery");
  cJSON_AddStringToObject(root, "provisioning_mode", "local_recovery");
  cJSON_AddStringToObject(root, "core_governed_mode", "endpoint_app");
  cJSON *schemas = cJSON_AddArrayToObject(root, "supported_payload_schemas");
  cJSON_AddItemToArray(schemas, cJSON_CreateString(kPayloadSchemaId));
  cJSON_AddStringToObject(root, "provisioning_state", g_ble.state);
  cJSON_AddBoolToObject(root, "claim_code_required", false);
  cJSON_AddNumberToObject(root, "expires_at_unix_ms", (g_ble.issued_at_us + kPairingTtlUs) / 1000);
  payload = print_json(root);
  return payload.c_str();
}

extern "C" const char *hexe_ble_provisioning_pairing_nonce_json() {
  static std::string payload;
  ensure_pairing_nonce();
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "onboarding_session_id", g_ble.onboarding_session_id);
  cJSON_AddStringToObject(root, "target_node_id", hexe::config::kEndpointId);
  cJSON_AddStringToObject(root, "pairing_nonce", g_ble.pairing_nonce);
  cJSON_AddStringToObject(root, "mode", "local_recovery");
  cJSON_AddBoolToObject(root, "claim_code_required", false);
  cJSON_AddNumberToObject(root, "expires_at_unix_ms", (g_ble.issued_at_us + kPairingTtlUs) / 1000);
  payload = print_json(root);
  return payload.c_str();
}

extern "C" const char *hexe_ble_provisioning_status_json() {
  static std::string payload;
  payload = hexe::recovery::render_recovery_ble_status_json();
  return payload.c_str();
}

extern "C" const char *hexe_ble_provisioning_ack_error_json() {
  static std::string payload;
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "ack", g_ble.last_ack);
  cJSON_AddStringToObject(root, "error", g_ble.last_error);
  cJSON_AddStringToObject(root, "state", g_ble.state);
  payload = print_json(root);
  return payload.c_str();
}

extern "C" int hexe_ble_provisioning_handle_encrypted_credentials(const char *json, unsigned int length) {
  if (json == nullptr || length == 0 || length > kMaxBleBodyBytes) {
    set_error("invalid_payload");
    return -1;
  }
  cJSON *root = cJSON_ParseWithLength(json, length);
  if (!cJSON_IsObject(root)) {
    cJSON_Delete(root);
    set_error("invalid_payload");
    return -1;
  }
  const char *mode = nullptr;
  const bool local_mode = string_field(root, "mode", &mode) && std::strcmp(mode, "local_recovery") == 0;
  if (!local_mode) {
    cJSON_Delete(root);
    set_error("core_governed_requires_endpoint_app");
    return -1;
  }
  const bool applied = handle_local_recovery_write(root);
  cJSON_Delete(root);
  return applied ? 0 : -1;
}

extern "C" void hexe_ble_pairing_scan_state_changed(int scanning, const char *reason, int rc) {
  g_ble.central_scanning = scanning != 0;
  if (reason != nullptr && reason[0] != '\0' && scanning == 0 && rc != 0) {
    set_state("awaiting_credentials", reason);
  }
}

extern "C" void hexe_ble_pairing_host_advert_seen(
    const char *address,
    int rssi,
    const char *name,
    int host_pairing_role_match) {
  if (address == nullptr || address[0] == '\0') {
    return;
  }
  copy_cstr(g_ble.host_pairing_address, sizeof(g_ble.host_pairing_address), address);
  copy_cstr(g_ble.host_pairing_name, sizeof(g_ble.host_pairing_name), name == nullptr ? "" : name);
  g_ble.host_pairing_rssi = rssi;
  g_ble.host_pairing_role_match = host_pairing_role_match != 0;
  g_ble.host_pairing_found = true;
  g_ble.host_pairing_seen_at_us = esp_timer_get_time();
  set_state("pairing_advert_seen", g_ble.host_pairing_role_match ? "host_pairing_advert" : "uuid_match");
  ESP_LOGI(kTag, "BLE host pairing advert seen address=%s rssi=%d role_match=%d", g_ble.host_pairing_address, rssi, host_pairing_role_match);
}

extern "C" void hexe_ble_pairing_connection_state_changed(int connected, const char *reason, int rc) {
  g_ble.host_pairing_connected = connected != 0;
  if (connected) {
    set_state("pairing_connected", reason == nullptr || reason[0] == '\0' ? "host_connected" : reason);
    return;
  }
  if (reason != nullptr && reason[0] != '\0') {
    set_state(g_ble.host_pairing_offer_received ? "pairing_offer_received" : "pairing_advert_seen", reason);
  }
  if (rc != 0) {
    std::snprintf(g_ble.last_error, sizeof(g_ble.last_error), "%s:%d", reason == nullptr ? "pairing_connect_failed" : reason, rc);
  }
}

extern "C" int hexe_ble_pairing_offer_received(const char *json, unsigned int length) {
  if (json == nullptr || length == 0 || length > 1024) {
    set_error("invalid_pairing_offer");
    return -1;
  }
  cJSON *root = cJSON_ParseWithLength(json, length);
  if (!cJSON_IsObject(root)) {
    cJSON_Delete(root);
    set_error("invalid_pairing_offer");
    return -1;
  }
  const bool remembered = remember_host_pairing_offer(root);
  cJSON_Delete(root);
  if (remembered) {
    ESP_LOGI(kTag, "BLE host pairing offer accepted session_hint=%s", g_ble.host_pairing_session_hint);
  }
  return remembered ? 0 : -1;
}

extern "C" void hexe_ble_pairing_identity_write_result(int succeeded, const char *reason, int rc) {
  g_ble.host_pairing_identity_sent = succeeded != 0;
  if (succeeded) {
    set_ack("identity_sent");
    set_state("pairing_identity_sent", reason == nullptr || reason[0] == '\0' ? "endpoint_identity_written" : reason);
    return;
  }
  if (reason != nullptr && reason[0] != '\0') {
    set_state("pairing_failed", reason);
  }
  if (rc != 0) {
    std::snprintf(g_ble.last_error, sizeof(g_ble.last_error), "%s:%d", reason == nullptr ? "identity_write_failed" : reason, rc);
  }
}
