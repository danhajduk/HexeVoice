from pathlib import Path


BLE_HEADER = Path("firmware/components/endpoint_runtime/system/ble_provisioning.h")
BLE_SOURCE = Path("firmware/components/endpoint_runtime/system/ble_provisioning.cpp")
BLE_GATT = Path("firmware/components/endpoint_runtime/system/ble_provisioning_gatt.c")
BACKEND_CLIENT = Path("firmware/components/endpoint_runtime/voice/backend_client.cpp")
APP_MAIN = Path("firmware/apps/endpoint/main/app_main.cpp")
WIFI_SOURCE = Path("firmware/components/endpoint_runtime/board/wifi.cpp")
WIFI_HEADER = Path("firmware/components/endpoint_runtime/board/wifi.h")
FIRMWARE_CMAKE = Path("firmware/components/endpoint_runtime/CMakeLists.txt")
SDKCONFIG_DEFAULTS = Path("firmware/sdkconfig.defaults")


def test_ble_onboarding_declares_core_gatt_contract_constants():
    header = BLE_HEADER.read_text(encoding="utf-8")
    gatt = BLE_GATT.read_text(encoding="utf-8")
    cmake = FIRMWARE_CMAKE.read_text(encoding="utf-8")

    assert 'kBleProvisioningOperation = "ble.provision_wifi"' in header
    assert 'kBleProvisioningLeaseScope = "hardware.bluetooth.ble.provision_wifi"' in header
    assert 'kBleProvisioningEnvelopeSchemaVersion = "1.0"' in header
    assert 'kBleProvisioningPayloadSchemaId = "hexe.voice_node.wifi_backend.v1"' in header
    assert 'kBleProvisioningEncryptionAlgorithm = "aes-256-gcm"' in header
    assert 'kBleProvisioningKeyAgreement = "x25519-hkdf-sha256"' in header
    assert 'kBleProvisioningServiceUuid = "7f9c0000-5f04-4d8b-9a46-7c0f7a100000"' in header
    assert 'kBleProvisioningDeviceIdentityUuid = "7f9c0001-5f04-4d8b-9a46-7c0f7a100000"' in header
    assert 'kBleProvisioningPairingNonceUuid = "7f9c0002-5f04-4d8b-9a46-7c0f7a100000"' in header
    assert 'kBleProvisioningStatusUuid = "7f9c0003-5f04-4d8b-9a46-7c0f7a100000"' in header
    assert 'kBleProvisioningEncryptedCredentialsUuid = "7f9c0004-5f04-4d8b-9a46-7c0f7a100000"' in header
    assert 'kBleProvisioningAckErrorUuid = "7f9c0005-5f04-4d8b-9a46-7c0f7a100000"' in header
    assert "BLE_UUID128_INIT(0x00, 0x00, 0x10, 0x7a" in gatt
    assert "BLE_GATT_CHR_F_WRITE" in gatt
    assert '"system/ble_provisioning.cpp"' in cmake
    assert '"system/ble_provisioning_gatt.c"' in cmake
    assert "bt" in cmake


def test_ble_onboarding_advertising_keeps_uuid_and_name_under_legacy_packet_limit():
    gatt = BLE_GATT.read_text(encoding="utf-8")
    advertise_start = gatt.index("static void advertise(void)")
    advertise = gatt[advertise_start : gatt.index("static int gap_event", advertise_start)]
    primary = advertise[: advertise.index("ble_gap_adv_set_fields(&fields)")]
    scan_response_start = gatt.index("static int set_scan_response_fields(void)")
    scan_response = gatt[scan_response_start:advertise_start]

    assert "fields.uuids128 = &service_uuid" in primary
    assert "fields.name =" not in primary
    assert "ble_gap_adv_rsp_set_fields(&rsp_fields)" in scan_response
    assert "rsp_fields.name = (uint8_t *)ble_svc_gap_device_name()" in scan_response
    assert "rsp_fields.mfg_data = advertising_timestamp_data" in scan_response
    assert "rsp_fields.mfg_data_len = sizeof(advertising_timestamp_data)" in scan_response
    assert "esp_timer_get_time()" in gatt[gatt.index("static void update_advertising_timestamp_data") :]
    assert "ble_gap_adv_stop()" in gatt[gatt.index("static void advertising_refresh_task") :]


def test_ble_onboarding_is_gated_by_board_profile_and_nimble_config():
    source = BLE_SOURCE.read_text(encoding="utf-8")
    generator = Path("firmware/tools/generate_board_profile_config.py").read_text(encoding="utf-8")
    defaults = SDKCONFIG_DEFAULTS.read_text(encoding="utf-8")

    assert "hexe::board::pins::kBleOnboardingSupported" in source
    assert "kBleOnboardingTransport" in source
    assert "CONFIG_BT_ENABLED" in source
    assert "CONFIG_BT_NIMBLE_ENABLED" in source
    assert "CONFIG_BT_NIMBLE_ROLE_PERIPHERAL" in source
    assert "CONFIG_BT_NIMBLE_GATT_SERVER" in source
    assert "ble_transport == \"native\"" in generator
    assert "kBleOnboardingSupported" in generator
    assert "CONFIG_BT_NIMBLE_ENABLED=y" in defaults


def test_ble_onboarding_rejects_unusable_envelopes_before_writing_settings():
    source = BLE_SOURCE.read_text(encoding="utf-8")

    assert 'set_error("invalid_nonce")' in source
    assert 'set_error("unsupported_schema")' in source
    assert 'set_error("decrypt_failed")' in source
    assert 'set_error("invalid_payload")' in source
    assert 'set_error("already_provisioned")' in source
    assert "sequence <= 0" in source
    assert "last_sequence" in source
    assert "expired_envelope(fields->expires_at)" in source
    assert "canonical_aad_json(" in source
    assert "canonical_key_id_json(" in source
    assert "std::strcmp(fields->key_id, expected_key_id.c_str())" in source
    assert "kMaxEncryptedEnvelopeBytes = 4096" in source
    assert "save_endpoint_provisioning(settings)" in source
    assert "hexe::board::reconnect_wifi()" in source


def test_ble_onboarding_implements_supervisor_envelope_crypto():
    source = BLE_SOURCE.read_text(encoding="utf-8")

    for required_text in (
        "PSA_ALG_ECDH",
        "PSA_ALG_HKDF(PSA_ALG_SHA_256)",
        "PSA_ALG_GCM",
        "psa_raw_key_agreement",
        "psa_key_derivation_output_bytes",
        "psa_aead_decrypt",
        '"hexe:x25519-hkdf-sha256:ble.provision_wifi"',
        '"supervisor_ephemeral_public_key"',
        '"endpoint_ephemeral_public_key"',
        '"key_agreement"',
        '"aad"',
        '"ciphertext"',
        '"tag"',
        '"expires_at"',
    ):
        assert required_text in source

    assert '"xchacha20poly1305"' not in source
    assert 'set_error("decrypt_failed");\n  return false;\n}' not in source[source.index("ble_provisioning_handle_encrypted_credentials") :]


def test_ble_onboarding_reports_status_without_secret_material():
    source = BLE_SOURCE.read_text(encoding="utf-8")
    backend = BACKEND_CLIENT.read_text(encoding="utf-8")

    assert '"provisioning", "ble"' not in backend
    assert 'cJSON *ble = cJSON_AddObjectToObject(provisioning, "ble")' in backend
    assert '"pairing_nonce_available"' in backend
    assert '"claim_code_ref_available"' in backend
    assert '"encrypted_credentials_uuid"' in backend
    assert '"ble_onboarding"' in backend
    assert '"wifi_password"' not in source[source.index("ble_provisioning_status_json") :]
    assert '"ciphertext"' not in backend
    assert '"pairing_nonce"' not in backend
    assert '"endpoint_ephemeral_public_key"' in source[source.index("ble_provisioning_device_identity_json") :]
    assert '"endpoint_ephemeral_public_key"' not in source[source.index("ble_provisioning_status_json") : source.index("ble_provisioning_ack_error_json")]


def test_ble_onboarding_starts_after_settings_and_updates_in_main_loop():
    app_main = APP_MAIN.read_text(encoding="utf-8")
    wifi_source = WIFI_SOURCE.read_text(encoding="utf-8")
    wifi_header = WIFI_HEADER.read_text(encoding="utf-8")

    assert '#include "system/ble_provisioning.h"' in app_main
    assert "hexe::system::init_ble_provisioning();" in app_main
    assert "hexe::system::update_ble_provisioning();" in app_main
    assert "void reconnect_wifi();" in wifi_header
    assert "void reconnect_wifi()" in wifi_source
    assert "Wi-Fi reconnect requested for updated provisioning settings" in wifi_source
