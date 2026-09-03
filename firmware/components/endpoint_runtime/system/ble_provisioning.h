#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace hexe::system {

constexpr const char *kBleProvisioningOperation = "ble.provision_wifi";
constexpr const char *kBleProvisioningLeaseScope = "hardware.bluetooth.ble.provision_wifi";
constexpr const char *kBleProvisioningContractVersion = "1.0";
constexpr const char *kBleProvisioningEnvelopeSchemaVersion = "1.0";
constexpr const char *kBleProvisioningPayloadSchemaId = "hexe.voice_node.wifi_backend.v1";
constexpr const char *kBleProvisioningEncryptionAlgorithm = "aes-256-gcm";
constexpr const char *kBleProvisioningKeyAgreement = "x25519-hkdf-sha256";
constexpr const char *kBleProvisioningServiceUuid = "7f9c0000-5f04-4d8b-9a46-7c0f7a100000";
constexpr const char *kBleProvisioningDeviceIdentityUuid = "7f9c0000-5f04-4d8b-9a46-7c0f7a100001";
constexpr const char *kBleProvisioningPairingNonceUuid = "7f9c0000-5f04-4d8b-9a46-7c0f7a100002";
constexpr const char *kBleProvisioningStatusUuid = "7f9c0000-5f04-4d8b-9a46-7c0f7a100003";
constexpr const char *kBleProvisioningEncryptedCredentialsUuid = "7f9c0000-5f04-4d8b-9a46-7c0f7a100004";
constexpr const char *kBleProvisioningAckErrorUuid = "7f9c0000-5f04-4d8b-9a46-7c0f7a100005";
constexpr const char *kBleHostPairingAdvertOperation = "ble.host_pairing_advert";
constexpr const char *kBleHostPairingAdvertRole = "host_pairing_advert";

struct BleProvisioningStatus {
  bool supported;
  bool enabled;
  bool eligible;
  bool advertising;
  bool central_scanning;
  bool host_pairing_found;
  bool host_pairing_role_match;
  bool provisioned;
  const char *transport;
  const char *state;
  const char *reason;
  const char *last_ack;
  const char *last_error;
  const char *host_pairing_address;
  const char *host_pairing_name;
  int host_pairing_rssi;
  int64_t host_pairing_seen_at_unix_ms;
  int64_t expires_at_unix_ms;
};

void init_ble_provisioning();
void update_ble_provisioning();
BleProvisioningStatus ble_provisioning_status();
std::string ble_provisioning_device_identity_json();
std::string ble_provisioning_pairing_nonce_json();
std::string ble_provisioning_status_json();
std::string ble_provisioning_ack_error_json();
bool ble_provisioning_handle_encrypted_credentials(const char *json, size_t length);
bool ble_provisioning_apply_decrypted_payload_for_test(const char *json);

}  // namespace hexe::system

extern "C" {
const char *hexe_ble_provisioning_device_identity_json();
const char *hexe_ble_provisioning_pairing_nonce_json();
const char *hexe_ble_provisioning_status_json();
const char *hexe_ble_provisioning_ack_error_json();
int hexe_ble_provisioning_handle_encrypted_credentials(const char *json, unsigned int length);
}
