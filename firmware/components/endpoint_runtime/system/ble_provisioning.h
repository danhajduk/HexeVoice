#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace hexe::system {

constexpr const char *kBleProvisioningOperation = "ble.provision_wifi";
constexpr const char *kBleProvisioningLeaseScope = "hardware.bluetooth.ble.provision_wifi";
constexpr const char *kBleProvisioningContractVersion = "1.0";
constexpr const char *kBleProvisioningPayloadSchemaId = "hexe.voice_node.wifi_backend.v1";
constexpr const char *kBleProvisioningServiceUuid = "7f9c0000-5f04-4d8b-9a46-7c0f7a100000";
constexpr const char *kBleProvisioningDeviceIdentityUuid = "7f9c0001-5f04-4d8b-9a46-7c0f7a100000";
constexpr const char *kBleProvisioningPairingNonceUuid = "7f9c0002-5f04-4d8b-9a46-7c0f7a100000";
constexpr const char *kBleProvisioningStatusUuid = "7f9c0003-5f04-4d8b-9a46-7c0f7a100000";
constexpr const char *kBleProvisioningEncryptedCredentialsUuid = "7f9c0004-5f04-4d8b-9a46-7c0f7a100000";
constexpr const char *kBleProvisioningAckErrorUuid = "7f9c0005-5f04-4d8b-9a46-7c0f7a100000";

struct BleProvisioningStatus {
  bool supported;
  bool enabled;
  bool eligible;
  bool advertising;
  bool provisioned;
  const char *transport;
  const char *state;
  const char *reason;
  const char *last_ack;
  const char *last_error;
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
