#include "system/ble_provisioning.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <cinttypes>
#include <string>
#include <vector>

#include "board/wifi.h"
#include "board_profile_pins.h"
#include "cJSON.h"
#include "endpoint_config.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "psa/crypto.h"
#include "system/clock.h"
#include "system/settings.h"

extern "C" int hexe_ble_provisioning_gatt_init(const char *device_name);
extern "C" int hexe_ble_provisioning_gatt_set_advertising(int enabled);
extern "C" int hexe_ble_pairing_central_set_scanning(int enabled);

namespace {
constexpr char kTag[] = "hexe_ble_prov";
constexpr int64_t kPairingTtlUs = 10LL * 60LL * 1000000LL;
constexpr size_t kMaxEncryptedEnvelopeBytes = 4096;
constexpr size_t kX25519PublicKeyBytes = 32;
constexpr size_t kAes256KeyBytes = 32;
constexpr size_t kAesGcmNonceBytes = 12;
constexpr size_t kAesGcmTagBytes = 16;

struct BleProvisioningState {
  bool initialized{false};
  bool gatt_ready{false};
  bool crypto_ready{false};
  bool advertising{false};
  bool central_scanning{false};
  bool host_pairing_found{false};
  bool host_pairing_role_match{false};
  bool host_pairing_connected{false};
  bool host_pairing_offer_received{false};
  bool host_pairing_identity_sent{false};
  bool host_pairing_claim_code_required{false};
  char state[24]{"idle"};
  char reason[48]{"not_started"};
  char last_ack[32]{""};
  char last_error[48]{""};
  char host_pairing_address[18]{""};
  char host_pairing_name[40]{""};
  char host_pairing_session_id[64]{""};
  char host_pairing_session_hint[32]{""};
  char host_pairing_expires_at[32]{""};
  char crypto_session_id[64]{""};
  int host_pairing_rssi{0};
  int64_t host_pairing_seen_at_us{0};
  char onboarding_session_id[64]{""};
  char pairing_nonce[48]{""};
  char endpoint_public_key_b64[48]{""};
  psa_key_id_t endpoint_private_key{0};
  uint32_t last_sequence{0};
  int64_t issued_at_us{0};
};

struct EnvelopeFields {
  const char *payload_schema_id{nullptr};
  const char *onboarding_session_id{nullptr};
  const char *target_node_id{nullptr};
  const char *pairing_nonce{nullptr};
  const char *supervisor_public_key{nullptr};
  const char *key_id{nullptr};
  const char *nonce{nullptr};
  const char *aad{nullptr};
  const char *ciphertext{nullptr};
  const char *tag{nullptr};
  const char *expires_at{nullptr};
  int sequence{0};
};

BleProvisioningState g_ble;

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

void set_state(const char *state, const char *reason) {
  const char *next_state = state == nullptr ? "idle" : state;
  const char *next_reason = reason == nullptr ? "" : reason;
  const bool changed = std::strcmp(g_ble.state, next_state) != 0 || std::strcmp(g_ble.reason, next_reason) != 0;
  std::strncpy(g_ble.state, state == nullptr ? "idle" : state, sizeof(g_ble.state) - 1);
  g_ble.state[sizeof(g_ble.state) - 1] = '\0';
  std::strncpy(g_ble.reason, reason == nullptr ? "" : reason, sizeof(g_ble.reason) - 1);
  g_ble.reason[sizeof(g_ble.reason) - 1] = '\0';
  if (changed) {
    ESP_LOGI(kTag, "BLE provisioning state=%s reason=%s", g_ble.state, g_ble.reason);
  }
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

int base64url_value(char value) {
  if (value >= 'A' && value <= 'Z') {
    return value - 'A';
  }
  if (value >= 'a' && value <= 'z') {
    return value - 'a' + 26;
  }
  if (value >= '0' && value <= '9') {
    return value - '0' + 52;
  }
  if (value == '-' || value == '+') {
    return 62;
  }
  if (value == '_' || value == '/') {
    return 63;
  }
  return -1;
}

bool base64url_decode(const char *text, std::vector<uint8_t> *decoded) {
  if (text == nullptr || decoded == nullptr) {
    return false;
  }
  decoded->clear();
  uint32_t accumulator = 0;
  int bits = 0;
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    if (*cursor == '=') {
      break;
    }
    const int value = base64url_value(*cursor);
    if (value < 0) {
      return false;
    }
    accumulator = (accumulator << 6) | static_cast<uint32_t>(value);
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      decoded->push_back(static_cast<uint8_t>((accumulator >> bits) & 0xff));
    }
  }
  return true;
}

std::string base64url_encode(const uint8_t *data, size_t length) {
  static constexpr char kAlphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  std::string encoded;
  uint32_t accumulator = 0;
  int bits = 0;
  for (size_t index = 0; index < length; ++index) {
    accumulator = (accumulator << 8) | data[index];
    bits += 8;
    while (bits >= 6) {
      bits -= 6;
      encoded.push_back(kAlphabet[(accumulator >> bits) & 0x3f]);
    }
  }
  if (bits > 0) {
    encoded.push_back(kAlphabet[(accumulator << (6 - bits)) & 0x3f]);
  }
  return encoded;
}

bool ensure_crypto_ready() {
  if (g_ble.crypto_ready && g_ble.endpoint_private_key != 0 && g_ble.endpoint_public_key_b64[0] != '\0') {
    return true;
  }
  if (g_ble.endpoint_private_key != 0) {
    psa_destroy_key(g_ble.endpoint_private_key);
    g_ble.endpoint_private_key = 0;
  }

  psa_status_t status = psa_crypto_init();
  if (status != PSA_SUCCESS) {
    ESP_LOGW(kTag, "PSA crypto init failed: %d", static_cast<int>(status));
    return false;
  }

  psa_key_attributes_t attributes = psa_key_attributes_init();
  psa_set_key_type(&attributes, PSA_KEY_TYPE_ECC_KEY_PAIR(PSA_ECC_FAMILY_MONTGOMERY));
  psa_set_key_bits(&attributes, 255);
  psa_set_key_lifetime(&attributes, PSA_KEY_LIFETIME_VOLATILE);
  psa_set_key_algorithm(&attributes, PSA_ALG_ECDH);
  psa_set_key_usage_flags(&attributes, PSA_KEY_USAGE_DERIVE);
  status = psa_generate_key(&attributes, &g_ble.endpoint_private_key);
  psa_reset_key_attributes(&attributes);
  if (status != PSA_SUCCESS) {
    ESP_LOGW(kTag, "Endpoint X25519 key generation failed: %d", static_cast<int>(status));
    return false;
  }

  uint8_t public_key[kX25519PublicKeyBytes] = {};
  size_t public_key_len = 0;
  status = psa_export_public_key(g_ble.endpoint_private_key, public_key, sizeof(public_key), &public_key_len);
  if (status != PSA_SUCCESS || public_key_len != sizeof(public_key)) {
    ESP_LOGW(kTag, "Endpoint X25519 public key export failed: %d len=%u", static_cast<int>(status), static_cast<unsigned>(public_key_len));
    psa_destroy_key(g_ble.endpoint_private_key);
    g_ble.endpoint_private_key = 0;
    return false;
  }

  const std::string encoded = base64url_encode(public_key, public_key_len);
  if (encoded.size() >= sizeof(g_ble.endpoint_public_key_b64)) {
    psa_destroy_key(g_ble.endpoint_private_key);
    g_ble.endpoint_private_key = 0;
    return false;
  }
  std::strncpy(g_ble.endpoint_public_key_b64, encoded.c_str(), sizeof(g_ble.endpoint_public_key_b64) - 1);
  g_ble.endpoint_public_key_b64[sizeof(g_ble.endpoint_public_key_b64) - 1] = '\0';
  g_ble.crypto_ready = true;
  return true;
}

void reset_crypto_session() {
  if (g_ble.endpoint_private_key != 0) {
    psa_destroy_key(g_ble.endpoint_private_key);
    g_ble.endpoint_private_key = 0;
  }
  g_ble.endpoint_public_key_b64[0] = '\0';
  g_ble.crypto_session_id[0] = '\0';
  g_ble.crypto_ready = false;
}

void ensure_pairing_nonce() {
  const char *session_id = g_ble.host_pairing_session_id[0] != '\0' ? g_ble.host_pairing_session_id : g_ble.onboarding_session_id;
  const bool host_session_bound =
      g_ble.host_pairing_session_id[0] != '\0' &&
      g_ble.crypto_session_id[0] != '\0' &&
      std::strcmp(g_ble.crypto_session_id, session_id) == 0;
  const bool existing_nonce_valid =
      g_ble.pairing_nonce[0] != '\0' && (host_session_bound || (esp_timer_get_time() - g_ble.issued_at_us) < kPairingTtlUs);
  if (existing_nonce_valid && ensure_crypto_ready()) {
    return;
  }
  reset_crypto_session();
  const uint32_t a = esp_random();
  const uint32_t b = esp_random();
  const uint32_t c = esp_random();
  const uint32_t d = esp_random();
  std::snprintf(g_ble.pairing_nonce, sizeof(g_ble.pairing_nonce), "%08" PRIx32 "%08" PRIx32 "%08" PRIx32 "%08" PRIx32, a, b, c, d);
  if (g_ble.host_pairing_session_id[0] != '\0') {
    std::strncpy(g_ble.onboarding_session_id, g_ble.host_pairing_session_id, sizeof(g_ble.onboarding_session_id) - 1);
    g_ble.onboarding_session_id[sizeof(g_ble.onboarding_session_id) - 1] = '\0';
  } else {
    std::snprintf(g_ble.onboarding_session_id, sizeof(g_ble.onboarding_session_id), "ble-%08" PRIx32 "%08" PRIx32, esp_random(), esp_random());
  }
  g_ble.last_sequence = 0;
  g_ble.issued_at_us = esp_timer_get_time();
  ensure_crypto_ready();
  std::strncpy(g_ble.crypto_session_id, g_ble.onboarding_session_id, sizeof(g_ble.crypto_session_id) - 1);
  g_ble.crypto_session_id[sizeof(g_ble.crypto_session_id) - 1] = '\0';
  ESP_LOGI(
      kTag,
      "BLE identity generated session=%s nonce_prefix=%.8s key_prefix=%.8s host_bound=%d",
      g_ble.crypto_session_id,
      g_ble.pairing_nonce,
      g_ble.endpoint_public_key_b64,
      g_ble.host_pairing_session_id[0] != '\0');
}

bool eligible_for_advertising() {
  return board_supported() && g_ble.crypto_ready && !hexe::system::provisioning_configured();
}

bool eligible_for_host_pairing_scan() {
  return board_supported() && g_ble.gatt_ready && !hexe::system::provisioning_configured();
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

bool json_escape_append(std::string *target, const char *value) {
  if (target == nullptr || value == nullptr) {
    return false;
  }
  target->push_back('"');
  for (const unsigned char *cursor = reinterpret_cast<const unsigned char *>(value); *cursor != '\0'; ++cursor) {
    switch (*cursor) {
      case '"':
        target->append("\\\"");
        break;
      case '\\':
        target->append("\\\\");
        break;
      case '\b':
        target->append("\\b");
        break;
      case '\f':
        target->append("\\f");
        break;
      case '\n':
        target->append("\\n");
        break;
      case '\r':
        target->append("\\r");
        break;
      case '\t':
        target->append("\\t");
        break;
      default:
        if (*cursor < 0x20) {
          char escaped[7];
          std::snprintf(escaped, sizeof(escaped), "\\u%04x", static_cast<unsigned>(*cursor));
          target->append(escaped);
        } else {
          target->push_back(static_cast<char>(*cursor));
        }
        break;
    }
  }
  target->push_back('"');
  return true;
}

void json_string_field(std::string *target, const char *key, const char *value, bool *first) {
  if (!*first) {
    target->push_back(',');
  }
  *first = false;
  json_escape_append(target, key);
  target->push_back(':');
  json_escape_append(target, value);
}

void json_int_field(std::string *target, const char *key, int value, bool *first) {
  if (!*first) {
    target->push_back(',');
  }
  *first = false;
  json_escape_append(target, key);
  target->push_back(':');
  char number[16];
  std::snprintf(number, sizeof(number), "%d", value);
  target->append(number);
}

std::string canonical_salt_json(const char *onboarding_session_id, const char *target_node_id, const char *pairing_nonce) {
  std::string out = "{";
  bool first = true;
  json_string_field(&out, "contract_version", hexe::system::kBleProvisioningContractVersion, &first);
  json_string_field(&out, "onboarding_session_id", onboarding_session_id, &first);
  json_string_field(&out, "pairing_nonce", pairing_nonce, &first);
  json_string_field(&out, "schema_version", hexe::system::kBleProvisioningEnvelopeSchemaVersion, &first);
  json_string_field(&out, "target_node_id", target_node_id, &first);
  out.push_back('}');
  return out;
}

std::string canonical_aad_json(
    const char *payload_schema_id,
    const char *onboarding_session_id,
    const char *target_node_id,
    const char *pairing_nonce,
    int sequence,
    const char *expires_at) {
  std::string out = "{";
  bool first = true;
  json_string_field(&out, "algorithm", hexe::system::kBleProvisioningEncryptionAlgorithm, &first);
  json_string_field(&out, "contract_version", hexe::system::kBleProvisioningContractVersion, &first);
  json_string_field(&out, "expires_at", expires_at, &first);
  json_string_field(&out, "key_agreement", hexe::system::kBleProvisioningKeyAgreement, &first);
  json_string_field(&out, "onboarding_session_id", onboarding_session_id, &first);
  json_string_field(&out, "pairing_nonce", pairing_nonce, &first);
  json_string_field(&out, "payload_schema_id", payload_schema_id, &first);
  json_string_field(&out, "schema_version", hexe::system::kBleProvisioningEnvelopeSchemaVersion, &first);
  json_int_field(&out, "sequence", sequence, &first);
  json_string_field(&out, "target_node_id", target_node_id, &first);
  out.push_back('}');
  return out;
}

std::string canonical_key_id_json(const char *supervisor_public_key, const char *onboarding_session_id, const char *target_node_id, int sequence) {
  std::string out = "{";
  bool first = true;
  json_string_field(&out, "endpoint_ephemeral_public_key", g_ble.endpoint_public_key_b64, &first);
  json_string_field(&out, "onboarding_session_id", onboarding_session_id, &first);
  json_int_field(&out, "sequence", sequence, &first);
  json_string_field(&out, "supervisor_ephemeral_public_key", supervisor_public_key, &first);
  json_string_field(&out, "target_node_id", target_node_id, &first);
  out.push_back('}');
  return out;
}

bool parse_iso_utc_seconds(const char *expires_at, int64_t *unix_seconds) {
  if (expires_at == nullptr || unix_seconds == nullptr) {
    return false;
  }
  int year = 0;
  int month = 0;
  int day = 0;
  int hour = 0;
  int minute = 0;
  int second = 0;
  if (std::sscanf(expires_at, "%4d-%2d-%2dT%2d:%2d:%2d", &year, &month, &day, &hour, &minute, &second) != 6) {
    return false;
  }
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour < 0 || hour > 23 || minute < 0 || minute > 59 || second < 0 ||
      second > 60) {
    return false;
  }
  year -= month <= 2;
  const int era = (year >= 0 ? year : year - 399) / 400;
  const unsigned yoe = static_cast<unsigned>(year - era * 400);
  const unsigned doy = (153 * static_cast<unsigned>(month + (month > 2 ? -3 : 9)) + 2) / 5 + static_cast<unsigned>(day) - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  const int64_t days = static_cast<int64_t>(era) * 146097 + static_cast<int64_t>(doe) - 719468;
  *unix_seconds = (days * 86400) + (hour * 3600) + (minute * 60) + second;
  return true;
}

bool expired_envelope(const char *expires_at) {
  int64_t expires_s = 0;
  if (!parse_iso_utc_seconds(expires_at, &expires_s)) {
    set_error("invalid_payload");
    return true;
  }
  int64_t now_ms = 0;
  if (!hexe::system::current_utc_unix_ms(&now_ms)) {
    return false;
  }
  if (now_ms >= (expires_s * 1000)) {
    set_error("invalid_payload");
    return true;
  }
  return false;
}

bool import_aes_decrypt_key(const uint8_t *key, size_t key_len, psa_key_id_t *key_id) {
  psa_key_attributes_t attributes = psa_key_attributes_init();
  psa_set_key_type(&attributes, PSA_KEY_TYPE_AES);
  psa_set_key_bits(&attributes, key_len * 8);
  psa_set_key_algorithm(&attributes, PSA_ALG_GCM);
  psa_set_key_usage_flags(&attributes, PSA_KEY_USAGE_DECRYPT);
  const psa_status_t status = psa_import_key(&attributes, key, key_len, key_id);
  psa_reset_key_attributes(&attributes);
  return status == PSA_SUCCESS;
}

bool derive_provisioning_key(const char *supervisor_public_key_b64, const std::string &salt_json, uint8_t key[kAes256KeyBytes]) {
  std::vector<uint8_t> supervisor_public_key;
  if (!base64url_decode(supervisor_public_key_b64, &supervisor_public_key) || supervisor_public_key.size() != kX25519PublicKeyBytes) {
    ESP_LOGW(kTag, "BLE supervisor public key invalid len=%u", static_cast<unsigned>(supervisor_public_key.size()));
    return false;
  }
  if (!ensure_crypto_ready()) {
    ESP_LOGW(kTag, "BLE crypto not ready for key derivation");
    return false;
  }

  uint8_t shared_secret[kX25519PublicKeyBytes] = {};
  size_t shared_secret_len = 0;
  psa_status_t status = psa_raw_key_agreement(
      PSA_ALG_ECDH,
      g_ble.endpoint_private_key,
      supervisor_public_key.data(),
      supervisor_public_key.size(),
      shared_secret,
      sizeof(shared_secret),
      &shared_secret_len);
  if (status != PSA_SUCCESS || shared_secret_len != sizeof(shared_secret)) {
    ESP_LOGW(kTag, "BLE X25519 agreement failed status=%d shared_len=%u", static_cast<int>(status), static_cast<unsigned>(shared_secret_len));
    std::fill(std::begin(shared_secret), std::end(shared_secret), 0);
    return false;
  }

  psa_key_derivation_operation_t derivation = PSA_KEY_DERIVATION_OPERATION_INIT;
  status = psa_key_derivation_setup(&derivation, PSA_ALG_HKDF(PSA_ALG_SHA_256));
  if (status == PSA_SUCCESS) {
    status = psa_key_derivation_input_bytes(
        &derivation,
        PSA_KEY_DERIVATION_INPUT_SALT,
        reinterpret_cast<const uint8_t *>(salt_json.data()),
        salt_json.size());
  }
  if (status == PSA_SUCCESS) {
    status = psa_key_derivation_input_bytes(&derivation, PSA_KEY_DERIVATION_INPUT_SECRET, shared_secret, shared_secret_len);
  }
  static constexpr char kInfo[] = "hexe:x25519-hkdf-sha256:ble.provision_wifi";
  if (status == PSA_SUCCESS) {
    status = psa_key_derivation_input_bytes(
        &derivation,
        PSA_KEY_DERIVATION_INPUT_INFO,
        reinterpret_cast<const uint8_t *>(kInfo),
        sizeof(kInfo) - 1);
  }
  if (status == PSA_SUCCESS) {
    status = psa_key_derivation_output_bytes(&derivation, key, kAes256KeyBytes);
  }
  psa_key_derivation_abort(&derivation);
  std::fill(std::begin(shared_secret), std::end(shared_secret), 0);
  if (status != PSA_SUCCESS) {
    ESP_LOGW(kTag, "BLE HKDF failed status=%d", static_cast<int>(status));
  }
  return status == PSA_SUCCESS;
}

bool decrypt_payload(
    const uint8_t key[kAes256KeyBytes],
    const std::vector<uint8_t> &nonce,
    const std::vector<uint8_t> &aad,
    const std::vector<uint8_t> &ciphertext,
    const std::vector<uint8_t> &tag,
    std::vector<uint8_t> *plaintext) {
  if (plaintext == nullptr || nonce.size() != kAesGcmNonceBytes || tag.size() != kAesGcmTagBytes || ciphertext.empty()) {
    return false;
  }
  std::vector<uint8_t> encrypted = ciphertext;
  encrypted.insert(encrypted.end(), tag.begin(), tag.end());
  plaintext->assign(ciphertext.size(), 0);
  psa_key_id_t aes_key = 0;
  if (!import_aes_decrypt_key(key, kAes256KeyBytes, &aes_key)) {
    ESP_LOGW(kTag, "BLE AES-GCM key import failed");
    return false;
  }
  size_t plaintext_len = 0;
  const psa_status_t status = psa_aead_decrypt(
      aes_key,
      PSA_ALG_GCM,
      nonce.data(),
      nonce.size(),
      aad.data(),
      aad.size(),
      encrypted.data(),
      encrypted.size(),
      plaintext->data(),
      plaintext->size(),
      &plaintext_len);
  psa_destroy_key(aes_key);
  if (status != PSA_SUCCESS) {
    ESP_LOGW(kTag, "BLE AES-GCM decrypt failed status=%d aad_len=%u ciphertext_len=%u tag_len=%u", static_cast<int>(status), static_cast<unsigned>(aad.size()), static_cast<unsigned>(ciphertext.size()), static_cast<unsigned>(tag.size()));
    plaintext->clear();
    return false;
  }
  plaintext->resize(plaintext_len);
  return true;
}

bool sha256_hex(const std::string &payload, std::string *hex) {
  if (hex == nullptr) {
    return false;
  }
  uint8_t digest[32] = {};
  size_t digest_len = 0;
  const psa_status_t status = psa_hash_compute(
      PSA_ALG_SHA_256,
      reinterpret_cast<const uint8_t *>(payload.data()),
      payload.size(),
      digest,
      sizeof(digest),
      &digest_len);
  if (status != PSA_SUCCESS || digest_len != sizeof(digest)) {
    return false;
  }
  static constexpr char kHex[] = "0123456789abcdef";
  hex->clear();
  hex->reserve(sizeof(digest) * 2);
  for (const uint8_t value : digest) {
    hex->push_back(kHex[(value >> 4) & 0x0f]);
    hex->push_back(kHex[value & 0x0f]);
  }
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

void copy_text(char *target, size_t target_size, const char *value) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  std::strncpy(target, value == nullptr ? "" : value, target_size - 1);
  target[target_size - 1] = '\0';
}

bool remember_host_pairing_offer(cJSON *root) {
  const char *onboarding_session_id = nullptr;
  const char *session_hint = "";
  const char *expires_at = "";
  const char *contract_version = nullptr;
  const char *payload_schema_id = nullptr;
  bool claim_code_required = false;

  if (!string_field(root, "onboarding_session_id", &onboarding_session_id) ||
      !optional_string_field(root, "session_hint", &session_hint) ||
      !optional_string_field(root, "expires_at", &expires_at) ||
      !optional_bool_field(root, "claim_code_required", &claim_code_required)) {
    set_error("invalid_pairing_offer");
    return false;
  }
  cJSON *contract_field = cJSON_GetObjectItem(root, "contract_version");
  if (contract_field != nullptr &&
      (!cJSON_IsString(contract_field) ||
       std::strcmp(contract_field->valuestring, hexe::system::kBleProvisioningContractVersion) != 0)) {
    set_error("unsupported_pairing_offer");
    return false;
  }
  cJSON *schema_field = cJSON_GetObjectItem(root, "payload_schema_id");
  if (schema_field != nullptr &&
      (!cJSON_IsString(schema_field) ||
       std::strcmp(schema_field->valuestring, hexe::system::kBleProvisioningPayloadSchemaId) != 0)) {
    set_error("unsupported_pairing_offer");
    return false;
  }
  if (contract_field != nullptr) {
    contract_version = contract_field->valuestring;
  }
  if (schema_field != nullptr) {
    payload_schema_id = schema_field->valuestring;
  }
  (void) contract_version;
  (void) payload_schema_id;

  const bool session_changed =
      g_ble.host_pairing_session_id[0] != '\0' && std::strcmp(g_ble.host_pairing_session_id, onboarding_session_id) != 0;
  if (session_changed) {
    reset_crypto_session();
    g_ble.pairing_nonce[0] = '\0';
    g_ble.last_sequence = 0;
  }
  if (!bounded_copy(g_ble.host_pairing_session_id, sizeof(g_ble.host_pairing_session_id), onboarding_session_id, 1, 63)) {
    set_error("invalid_pairing_offer");
    return false;
  }
  copy_text(g_ble.onboarding_session_id, sizeof(g_ble.onboarding_session_id), onboarding_session_id);
  copy_text(g_ble.host_pairing_session_hint, sizeof(g_ble.host_pairing_session_hint), session_hint);
  copy_text(g_ble.host_pairing_expires_at, sizeof(g_ble.host_pairing_expires_at), expires_at);
  g_ble.host_pairing_claim_code_required = claim_code_required;
  g_ble.host_pairing_offer_received = true;
  g_ble.last_error[0] = '\0';
  ensure_pairing_nonce();
  set_state("pairing_offer_received", "host_pairing_offer");
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

bool validate_envelope_binding(cJSON *root, EnvelopeFields *fields) {
  const char *schema_version = nullptr;
  const char *contract_version = nullptr;
  const char *algorithm = nullptr;
  const char *key_agreement = nullptr;

  if (fields == nullptr) {
    set_error("invalid_payload");
    return false;
  }

  if (!string_field(root, "schema_version", &schema_version) ||
      !string_field(root, "payload_schema_id", &fields->payload_schema_id) ||
      !string_field(root, "contract_version", &contract_version) ||
      !string_field(root, "onboarding_session_id", &fields->onboarding_session_id) ||
      !string_field(root, "target_node_id", &fields->target_node_id) ||
      !string_field(root, "pairing_nonce", &fields->pairing_nonce) ||
      !int_field(root, "sequence", &fields->sequence) ||
      !string_field(root, "expires_at", &fields->expires_at) ||
      !string_field(root, "algorithm", &algorithm) ||
      !string_field(root, "key_agreement", &key_agreement) ||
      !string_field(root, "key_id", &fields->key_id) ||
      !string_field(root, "supervisor_ephemeral_public_key", &fields->supervisor_public_key) ||
      !string_field(root, "nonce", &fields->nonce) ||
      !string_field(root, "aad", &fields->aad) ||
      !string_field(root, "ciphertext", &fields->ciphertext) ||
      !string_field(root, "tag", &fields->tag)) {
    set_error("invalid_payload");
    return false;
  }
  if (std::strcmp(schema_version, hexe::system::kBleProvisioningEnvelopeSchemaVersion) != 0 ||
      std::strcmp(contract_version, hexe::system::kBleProvisioningContractVersion) != 0) {
    set_error("unsupported_schema");
    return false;
  }
  if (std::strcmp(fields->payload_schema_id, hexe::system::kBleProvisioningPayloadSchemaId) != 0) {
    set_error("unsupported_schema");
    return false;
  }
  if (std::strcmp(algorithm, hexe::system::kBleProvisioningEncryptionAlgorithm) != 0 ||
      std::strcmp(key_agreement, hexe::system::kBleProvisioningKeyAgreement) != 0) {
    set_error("decrypt_failed");
    return false;
  }
  if (std::strcmp(fields->onboarding_session_id, g_ble.onboarding_session_id) != 0) {
    set_error("invalid_payload");
    return false;
  }
  if (std::strcmp(fields->target_node_id, hexe::system::endpoint_id()) != 0 && std::strcmp(fields->target_node_id, hardware_id()) != 0) {
    set_error("invalid_payload");
    return false;
  }
  if (std::strcmp(fields->pairing_nonce, g_ble.pairing_nonce) != 0) {
    ESP_LOGW(
        kTag,
        "BLE encrypted credentials nonce mismatch session=%s payload_nonce_prefix=%.8s current_nonce_prefix=%.8s crypto_session=%s",
        fields->onboarding_session_id,
        fields->pairing_nonce,
        g_ble.pairing_nonce,
        g_ble.crypto_session_id);
    set_error("invalid_nonce");
    return false;
  }
  if (fields->sequence <= 0 || static_cast<uint32_t>(fields->sequence) <= g_ble.last_sequence) {
    set_error("invalid_payload");
    return false;
  }
  if (expired_envelope(fields->expires_at)) {
    return false;
  }

  std::vector<uint8_t> aad;
  if (!base64url_decode(fields->aad, &aad)) {
    set_error("invalid_payload");
    return false;
  }
  const std::string expected_aad = canonical_aad_json(
      fields->payload_schema_id,
      fields->onboarding_session_id,
      fields->target_node_id,
      fields->pairing_nonce,
      fields->sequence,
      fields->expires_at);
  if (aad.size() != expected_aad.size() ||
      std::memcmp(aad.data(), expected_aad.data(), expected_aad.size()) != 0) {
    set_error("invalid_payload");
    return false;
  }

  std::vector<uint8_t> supervisor_public_key;
  std::vector<uint8_t> nonce;
  std::vector<uint8_t> ciphertext;
  std::vector<uint8_t> tag;
  if (!base64url_decode(fields->supervisor_public_key, &supervisor_public_key) ||
      supervisor_public_key.size() != kX25519PublicKeyBytes ||
      !base64url_decode(fields->nonce, &nonce) ||
      nonce.size() != kAesGcmNonceBytes ||
      !base64url_decode(fields->ciphertext, &ciphertext) ||
      ciphertext.empty() ||
      !base64url_decode(fields->tag, &tag) ||
      tag.size() != kAesGcmTagBytes) {
    set_error("invalid_payload");
    return false;
  }

  std::string expected_key_id;
  if (!sha256_hex(
          canonical_key_id_json(fields->supervisor_public_key, fields->onboarding_session_id, fields->target_node_id, fields->sequence),
          &expected_key_id) ||
      std::strcmp(fields->key_id, expected_key_id.c_str()) != 0) {
    set_error("decrypt_failed");
    return false;
  }
  return true;
}

bool apply_encrypted_envelope(cJSON *root, const EnvelopeFields &fields) {
  std::vector<uint8_t> nonce;
  std::vector<uint8_t> aad;
  std::vector<uint8_t> ciphertext;
  std::vector<uint8_t> tag;
  if (!base64url_decode(fields.nonce, &nonce) ||
      !base64url_decode(fields.aad, &aad) ||
      !base64url_decode(fields.ciphertext, &ciphertext) ||
      !base64url_decode(fields.tag, &tag)) {
    set_error("invalid_payload");
    return false;
  }

  uint8_t key[kAes256KeyBytes] = {};
  const std::string salt_json = canonical_salt_json(fields.onboarding_session_id, fields.target_node_id, fields.pairing_nonce);
  if (!derive_provisioning_key(fields.supervisor_public_key, salt_json, key)) {
    std::fill(key, key + sizeof(key), 0);
    set_error("key_derivation_failed");
    return false;
  }

  std::vector<uint8_t> plaintext;
  const bool decrypted = decrypt_payload(key, nonce, aad, ciphertext, tag, &plaintext);
  std::fill(key, key + sizeof(key), 0);
  if (!decrypted) {
    set_error("aes_gcm_decrypt_failed");
    return false;
  }
  plaintext.push_back('\0');

  cJSON *decrypted_root = cJSON_ParseWithLength(reinterpret_cast<const char *>(plaintext.data()), plaintext.size() - 1);
  std::fill(plaintext.begin(), plaintext.end(), 0);
  if (decrypted_root == nullptr) {
    set_error("invalid_payload");
    return false;
  }

  const char *payload_schema_id = nullptr;
  cJSON *credential_payload = cJSON_GetObjectItem(decrypted_root, "credential_payload");
  const bool valid_payload =
      string_field(decrypted_root, "payload_schema_id", &payload_schema_id) &&
      std::strcmp(payload_schema_id, hexe::system::kBleProvisioningPayloadSchemaId) == 0 &&
      cJSON_IsObject(credential_payload);
  if (!valid_payload) {
    cJSON_Delete(decrypted_root);
    set_error("invalid_payload");
    return false;
  }

  (void) root;
  set_state("applying", "decrypted_payload_validated");
  const bool saved = validate_and_save_payload(credential_payload);
  cJSON_Delete(decrypted_root);
  if (saved) {
    g_ble.last_sequence = static_cast<uint32_t>(fields.sequence);
    reset_crypto_session();
  }
  return saved;
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
    ESP_LOGI(kTag, "BLE onboarding disabled board_profile=%s", hexe::config::kEndpointBoardProfile);
    return;
  }
  if (!nimble_config_enabled()) {
    set_state("failed", "nimble_disabled");
    std::strncpy(g_ble.last_error, "nimble_disabled", sizeof(g_ble.last_error) - 1);
    ESP_LOGW(kTag, "BLE onboarding disabled: NimBLE peripheral+central config is not enabled");
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
  ESP_LOGI(
      kTag,
      "BLE onboarding ready board_profile=%s device_id=%s advertising_eligible=%d host_pairing_scan_eligible=%d",
      hexe::config::kEndpointBoardProfile,
      endpoint_id(),
      eligible_for_advertising(),
      eligible_for_host_pairing_scan());
  set_state("awaiting_credentials", eligible_for_advertising() ? "advertising_allowed" : "already_provisioned");
  update_ble_provisioning();
}

void update_ble_provisioning() {
  if (!g_ble.initialized || !g_ble.gatt_ready) {
    return;
  }
  const bool should_advertise = eligible_for_advertising();
  const bool should_scan = eligible_for_host_pairing_scan();
  if (should_advertise) {
    ensure_pairing_nonce();
  }
  if (hexe_ble_pairing_central_set_scanning(should_scan ? 1 : 0) != 0 && should_scan) {
    g_ble.central_scanning = false;
  }
  if (should_advertise == g_ble.advertising) {
    return;
  }
  if (hexe_ble_provisioning_gatt_set_advertising(should_advertise ? 1 : 0) == 0) {
    g_ble.advertising = should_advertise;
    ESP_LOGI(
        kTag,
        "BLE onboarding advertising=%d eligible=%d provisioned=%d host_pairing_scan=%d",
        g_ble.advertising,
        should_advertise,
        provisioning_configured(),
        should_scan);
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
      g_ble.central_scanning,
      g_ble.host_pairing_found,
      g_ble.host_pairing_role_match,
      g_ble.host_pairing_connected,
      g_ble.host_pairing_offer_received,
      g_ble.host_pairing_identity_sent,
      g_ble.host_pairing_claim_code_required,
      provisioning_configured(),
      hexe::board::pins::kBleOnboardingTransport,
      g_ble.state,
      g_ble.reason,
      g_ble.last_ack,
      g_ble.last_error,
      g_ble.host_pairing_address,
      g_ble.host_pairing_name,
      g_ble.host_pairing_session_id,
      g_ble.host_pairing_session_hint,
      g_ble.host_pairing_expires_at,
      g_ble.host_pairing_rssi,
      g_ble.host_pairing_seen_at_us == 0 ? 0 : g_ble.host_pairing_seen_at_us / 1000,
      g_ble.issued_at_us == 0 ? 0 : (g_ble.issued_at_us + kPairingTtlUs) / 1000};
}

std::string ble_provisioning_device_identity_json() {
  ensure_pairing_nonce();
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "contract_version", kBleProvisioningContractVersion);
  cJSON_AddStringToObject(root, "envelope_schema_version", kBleProvisioningEnvelopeSchemaVersion);
  cJSON_AddStringToObject(root, "encryption_algorithm", kBleProvisioningEncryptionAlgorithm);
  cJSON_AddStringToObject(root, "key_agreement", kBleProvisioningKeyAgreement);
  cJSON_AddStringToObject(root, "onboarding_session_id", g_ble.onboarding_session_id);
  cJSON_AddStringToObject(root, "device_id", endpoint_id());
  cJSON_AddStringToObject(root, "node_hardware_id", hardware_id());
  cJSON_AddStringToObject(root, "target_node_id", endpoint_id());
  cJSON_AddStringToObject(root, "board_profile", hexe::config::kEndpointBoardProfile);
  cJSON_AddStringToObject(root, "firmware_version", esp_app_get_description()->version);
  cJSON_AddStringToObject(root, "application_type", "endpoint");
  cJSON_AddStringToObject(root, "provisioning_mode", "core_governed_pairing");
  cJSON_AddStringToObject(root, "core_governed_mode", "endpoint_app");
  cJSON_AddStringToObject(root, "protocol_version", kBleProvisioningContractVersion);
  cJSON_AddStringToObject(root, "pairing_nonce", g_ble.pairing_nonce);
  cJSON_AddStringToObject(root, "endpoint_ephemeral_public_key", g_ble.endpoint_public_key_b64);
  cJSON *schemas = cJSON_AddArrayToObject(root, "supported_payload_schemas");
  cJSON_AddItemToArray(schemas, cJSON_CreateString(kBleProvisioningPayloadSchemaId));
  cJSON_AddStringToObject(root, "provisioning_state", g_ble.state);
  cJSON_AddBoolToObject(root, "claim_code_required", false);
  cJSON_AddNumberToObject(root, "expires_at_unix_ms", (g_ble.issued_at_us + kPairingTtlUs) / 1000);
  return print_json(root);
}

std::string ble_provisioning_pairing_nonce_json() {
  ensure_pairing_nonce();
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "onboarding_session_id", g_ble.onboarding_session_id);
  cJSON_AddStringToObject(root, "target_node_id", endpoint_id());
  cJSON_AddStringToObject(root, "pairing_nonce", g_ble.pairing_nonce);
  cJSON_AddStringToObject(root, "endpoint_ephemeral_public_key", g_ble.endpoint_public_key_b64);
  cJSON_AddStringToObject(root, "key_agreement", kBleProvisioningKeyAgreement);
  cJSON_AddStringToObject(root, "algorithm", kBleProvisioningEncryptionAlgorithm);
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
  cJSON_AddBoolToObject(root, "central_scanning", status.central_scanning);
  cJSON_AddBoolToObject(root, "provisioned", status.provisioned);
  cJSON_AddStringToObject(root, "reason", status.reason);
  cJSON *host_pairing = cJSON_AddObjectToObject(root, "host_pairing");
  cJSON_AddBoolToObject(host_pairing, "found", status.host_pairing_found);
  cJSON_AddBoolToObject(host_pairing, "role_match", status.host_pairing_role_match);
  cJSON_AddBoolToObject(host_pairing, "connected", status.host_pairing_connected);
  cJSON_AddBoolToObject(host_pairing, "offer_received", status.host_pairing_offer_received);
  cJSON_AddBoolToObject(host_pairing, "identity_sent", status.host_pairing_identity_sent);
  cJSON_AddBoolToObject(host_pairing, "claim_code_required", status.host_pairing_claim_code_required);
  if (status.host_pairing_address[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "address", status.host_pairing_address);
  }
  if (status.host_pairing_name[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "name", status.host_pairing_name);
  }
  if (status.host_pairing_session_id[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "onboarding_session_id", status.host_pairing_session_id);
  }
  if (status.host_pairing_session_hint[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "session_hint", status.host_pairing_session_hint);
  }
  if (status.host_pairing_expires_at[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "expires_at", status.host_pairing_expires_at);
  }
  cJSON_AddNumberToObject(host_pairing, "rssi", status.host_pairing_rssi);
  cJSON_AddNumberToObject(host_pairing, "seen_at_unix_ms", status.host_pairing_seen_at_unix_ms);
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
  EnvelopeFields fields;
  const bool binding_ok = validate_envelope_binding(root, &fields);
  if (!binding_ok) {
    cJSON_Delete(root);
    return false;
  }
  const bool applied = apply_encrypted_envelope(root, fields);
  cJSON_Delete(root);
  update_ble_provisioning();
  return applied;
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

extern "C" void hexe_ble_pairing_scan_state_changed(int scanning, const char *reason, int rc) {
  g_ble.central_scanning = scanning != 0;
  const char *safe_reason = reason == nullptr || reason[0] == '\0' ? "(none)" : reason;
  static int last_logged_scanning = -1;
  static int last_logged_rc = 0;
  static char last_logged_reason[48] = "";
  const bool changed =
      scanning != last_logged_scanning ||
      rc != last_logged_rc ||
      std::strncmp(safe_reason, last_logged_reason, sizeof(last_logged_reason) - 1) != 0;
  if (changed || rc != 0) {
    ESP_LOGI(kTag, "BLE host pairing scan_state scanning=%d reason=%s rc=%d", scanning, safe_reason, rc);
    last_logged_scanning = scanning;
    last_logged_rc = rc;
    std::strncpy(last_logged_reason, safe_reason, sizeof(last_logged_reason) - 1);
    last_logged_reason[sizeof(last_logged_reason) - 1] = '\0';
  }
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
  std::strncpy(g_ble.host_pairing_address, address, sizeof(g_ble.host_pairing_address) - 1);
  g_ble.host_pairing_address[sizeof(g_ble.host_pairing_address) - 1] = '\0';
  std::strncpy(g_ble.host_pairing_name, name == nullptr ? "" : name, sizeof(g_ble.host_pairing_name) - 1);
  g_ble.host_pairing_name[sizeof(g_ble.host_pairing_name) - 1] = '\0';
  g_ble.host_pairing_rssi = rssi;
  g_ble.host_pairing_role_match = host_pairing_role_match != 0;
  g_ble.host_pairing_found = true;
  g_ble.host_pairing_seen_at_us = esp_timer_get_time();
  set_state("pairing_advert_seen", g_ble.host_pairing_role_match ? "host_pairing_advert" : "uuid_match");
  ESP_LOGI(kTag, "BLE host pairing advert seen address=%s rssi=%d role_match=%d", g_ble.host_pairing_address, rssi, host_pairing_role_match);
}

extern "C" void hexe_ble_pairing_connection_state_changed(int connected, const char *reason, int rc) {
  g_ble.host_pairing_connected = connected != 0;
  ESP_LOGI(
      kTag,
      "BLE host pairing connection_state connected=%d reason=%s rc=%d offer_received=%d identity_sent=%d",
      connected,
      reason == nullptr || reason[0] == '\0' ? "(none)" : reason,
      rc,
      g_ble.host_pairing_offer_received,
      g_ble.host_pairing_identity_sent);
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
    ESP_LOGI(
        kTag,
        "BLE host pairing offer accepted session_hint=%s session=%s nonce_prefix=%.8s key_prefix=%.8s",
        g_ble.host_pairing_session_hint,
        g_ble.onboarding_session_id,
        g_ble.pairing_nonce,
        g_ble.endpoint_public_key_b64);
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

extern "C" void hexe_ble_pairing_credentials_read_result(int succeeded, const char *reason, int rc) {
  ESP_LOGI(
      kTag,
      "BLE host pairing credentials_result succeeded=%d reason=%s rc=%d ble_state=%s ble_error=%s",
      succeeded,
      reason == nullptr || reason[0] == '\0' ? "(none)" : reason,
      rc,
      g_ble.state,
      g_ble.last_error[0] == '\0' ? "(none)" : g_ble.last_error);
  if (succeeded) {
    set_ack("credentials_applied");
    set_state("completed", reason == nullptr || reason[0] == '\0' ? "credentials_applied" : reason);
    const int stop_rc = hexe_ble_pairing_central_set_scanning(0);
    if (stop_rc == 0) {
      g_ble.central_scanning = false;
    } else {
      ESP_LOGW(kTag, "BLE host pairing scan stop after credentials failed: %d", stop_rc);
    }
    return;
  }
  if (reason != nullptr && std::strcmp(reason, "credentials_pending") == 0) {
    set_state("pairing_identity_sent", "waiting_for_operator_approval");
    return;
  }
  if (reason != nullptr && reason[0] != '\0') {
    set_state("pairing_identity_sent", reason);
  }
  if (rc != 0) {
    std::snprintf(g_ble.last_error, sizeof(g_ble.last_error), "%s:%d", reason == nullptr ? "credentials_read_failed" : reason, rc);
  }
}
