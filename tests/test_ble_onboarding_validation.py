from pathlib import Path

import pytest
import yaml

from fake_ble_gatt_harness import (
    BLE_PROVISIONING_OPERATION,
    BLE_PROVISIONING_SCOPE,
    FakeBleGattEndpoint,
    FakeBleLease,
    FakeSupervisorBleBroker,
    contains_secret,
    endpoint_provisioning_context,
    voice_credential_payload,
)


BLE_DOC = Path("docs/firmware-ble-onboarding-integration.md")
PHYSICAL_DOC = Path("docs/ble-onboarding-physical-validation.md")
ENDPOINT_BLE_SOURCE = Path("firmware/components/endpoint_runtime/system/ble_provisioning.cpp")
RECOVERY_BLE_SOURCE = Path("firmware/components/recovery_runtime/recovery_ble_provisioning.cpp")
BACKEND_CLIENT_SOURCE = Path("firmware/components/endpoint_runtime/voice/backend_client.cpp")
BOARD_ROOT = Path("firmware/boards")


def test_fake_supervisor_and_endpoint_gatt_harness_completes_without_leaking_credentials():
    target = FakeBleGattEndpoint()
    broker = FakeSupervisorBleBroker()
    credential_payload = voice_credential_payload()

    result = broker.provision_wifi(
        lease=FakeBleLease(),
        adapter="hci0",
        target=target,
        provisioning_context=endpoint_provisioning_context(target),
        credential_payload=credential_payload,
    )

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert target.saved_payload == credential_payload
    assert result["credential_payload"]["wifi_password"] == "[REDACTED]"
    assert target.read_status()["saved_payload"]["wifi_password"] == "[REDACTED]"
    assert not contains_secret(result, "correct-password")
    assert not contains_secret(target.read_status(), "correct-password")


@pytest.mark.parametrize(
    ("lease", "adapter", "expected_error"),
    (
        (FakeBleLease(scope="hardware.bluetooth.ble.scan"), "hci0", "lease_scope_mismatch"),
        (FakeBleLease(operation="ble.status", scope="hardware.bluetooth.ble.status"), "hci0", "lease_scope_mismatch"),
        (FakeBleLease(adapter="hci1"), "hci0", "wrong_adapter"),
        (FakeBleLease(expires_at="2000-01-01T00:00:00+00:00"), "hci0", "lease_expired"),
    ),
)
def test_fake_supervisor_rejects_scope_adapter_and_expired_lease_reuse(lease, adapter, expected_error):
    target = FakeBleGattEndpoint()
    broker = FakeSupervisorBleBroker()

    result = broker.provision_wifi(
        lease=lease,
        adapter=adapter,
        target=target,
        provisioning_context=endpoint_provisioning_context(target),
        credential_payload=voice_credential_payload(),
    )

    assert result == {"ok": False, "status": "failed", "error": expected_error}
    assert target.saved_payload is None


def test_fake_supervisor_reports_absent_adapter_before_touching_gatt_target():
    target = FakeBleGattEndpoint()
    broker = FakeSupervisorBleBroker(adapter_present=False)

    result = broker.provision_wifi(
        lease=FakeBleLease(),
        adapter="hci0",
        target=target,
        provisioning_context=endpoint_provisioning_context(target),
        credential_payload=voice_credential_payload(),
    )

    assert result == {"ok": False, "status": "failed", "error": "bluetooth_adapter_absent"}
    assert target.saved_payload is None


@pytest.mark.parametrize(
    ("context_overrides", "expected_error"),
    (
        ({"target_node_id": "wrong-node"}, "invalid_target_node_id"),
        ({"pairing_nonce": "wrong-nonce"}, "invalid_pairing_nonce"),
        ({"payload_schema_id": "unexpected.schema"}, "invalid_payload_schema_id"),
    ),
)
def test_fake_gatt_endpoint_rejects_wrong_node_nonce_and_schema(context_overrides, expected_error):
    target = FakeBleGattEndpoint()
    context = endpoint_provisioning_context(target, **context_overrides)

    result = target.write_credentials({**context, "credential_payload": voice_credential_payload()})

    assert result == {"ok": False, "status": "failed", "error": expected_error}
    assert target.saved_payload is None
    assert target.read_status()["last_error"] == expected_error


def test_fake_gatt_endpoint_rejects_replay_and_malformed_payloads():
    target = FakeBleGattEndpoint()
    first = target.write_credentials(
        {
            **endpoint_provisioning_context(target, sequence=1),
            "credential_payload": voice_credential_payload(),
        }
    )
    replay = target.write_credentials(
        {
            **endpoint_provisioning_context(target, sequence=1),
            "credential_payload": voice_credential_payload(),
        }
    )
    malformed = target.write_credentials_json("{not-json")

    assert first["status"] == "completed"
    assert replay == {"ok": False, "status": "failed", "error": "replay_detected"}
    assert malformed == {"ok": False, "status": "failed", "error": "malformed_payload"}


@pytest.mark.parametrize(
    ("target", "expected_error"),
    (
        (FakeBleGattEndpoint(wifi_result="failed"), "wifi_apply_failed"),
        (FakeBleGattEndpoint(backend_result="unreachable"), "backend_unreachable"),
        (FakeBleGattEndpoint(supports_ble=False), "gatt_backend_unavailable"),
    ),
)
def test_fake_gatt_endpoint_reports_runtime_failure_modes(target, expected_error):
    result = target.write_credentials(
        {
            **endpoint_provisioning_context(target),
            "credential_payload": voice_credential_payload(),
        }
    )

    assert result == {"ok": False, "status": "failed", "error": expected_error}
    assert target.saved_payload is None


def test_fake_recovery_gatt_requires_explicit_local_recovery_mode():
    target = FakeBleGattEndpoint(mode="recovery")
    core_governed = target.write_credentials(
        {
            **endpoint_provisioning_context(target),
            "credential_payload": voice_credential_payload(),
        }
    )
    local = target.write_credentials(
        {
            **endpoint_provisioning_context(target),
            "mode": "local_recovery",
            "credential_payload": voice_credential_payload(),
        }
    )

    assert core_governed == {"ok": False, "status": "failed", "error": "core_governed_requires_endpoint_app"}
    assert local["status"] == "completed"


def test_firmware_static_checks_cover_ble_state_machine_and_safe_heartbeat_fields():
    endpoint_source = ENDPOINT_BLE_SOURCE.read_text(encoding="utf-8")
    recovery_source = RECOVERY_BLE_SOURCE.read_text(encoding="utf-8")
    backend_client_source = BACKEND_CLIENT_SOURCE.read_text(encoding="utf-8")

    for required_text in (
        "kPairingTtlUs",
        "kMaxEncryptedEnvelopeBytes",
        "expired_envelope(fields->expires_at)",
        "fields->sequence <= 0",
        "fields->sequence) <= g_ble.last_sequence",
        "decrypt_payload",
        "hexe::system::save_endpoint_provisioning",
        "eligible_for_advertising()",
        "hexe_ble_provisioning_gatt_set_advertising(should_advertise ? 1 : 0)",
    ):
        assert required_text in endpoint_source

    for required_text in (
        "kPairingTtlUs",
        "kMaxBleBodyBytes",
        '"local_recovery"',
        '"core_governed_requires_endpoint_app"',
        "save_local_recovery_payload",
        "nvs_set_u8(handle, kProvisionedKey, 1)",
    ):
        assert required_text in recovery_source

    assert '"ble_onboarding"' in backend_client_source
    assert "cJSON_AddBoolToObject(ble, \"enabled\", ble_status.enabled)" in backend_client_source
    capabilities_slice = backend_client_source[backend_client_source.index("endpoint_capabilities_json") :]
    assert 'cJSON_AddStringToObject(ble, "pairing_nonce"' not in capabilities_slice
    assert "kBleProvisioningPairingNonceUuid" in capabilities_slice


def test_board_profile_ble_validation_covers_supported_and_future_unsupported_paths():
    profile_status = {}
    for profile_path in BOARD_ROOT.glob("*/board.yaml"):
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        wireless = profile["hardware"]["wireless"]
        profile_status[profile["board_profile"]] = {
            "buildable": profile["adapters"]["buildable"],
            "target": profile["build"]["idf_target"],
            "transport": wireless.get("transport"),
            "bluetooth": bool(wireless.get("bluetooth")),
            "support_status": profile["support_status"],
        }

    assert profile_status["ha_voice_pe"] == {
        "buildable": True,
        "target": "esp32s3",
        "transport": "native",
        "bluetooth": True,
        "support_status": "active",
    }
    assert profile_status["esp_box_3"]["transport"] == "native"
    assert profile_status["waveshare_s3_touch_lcd_1_85c_box_v2"]["transport"] == "native"
    assert profile_status["waveshare_p4_wifi6_touch_lcd_7b"]["target"] == "esp32p4"
    assert profile_status["waveshare_p4_wifi6_touch_lcd_7b"]["transport"] == "sdio"
    assert profile_status["waveshare_p4_wifi6_touch_lcd_7b"]["buildable"] is False


def test_validation_docs_define_ci_harnesses_and_physical_checklist():
    ble_doc = BLE_DOC.read_text(encoding="utf-8")
    physical_doc = PHYSICAL_DOC.read_text(encoding="utf-8")

    for required_text in (
        "FakeBleGattEndpoint",
        "FakeSupervisorBleBroker",
        BLE_PROVISIONING_OPERATION,
        BLE_PROVISIONING_SCOPE,
        "lease_scope_mismatch",
        "wrong_adapter",
        "replay_detected",
        "malformed_payload",
        "backend_unreachable",
    ):
        assert required_text in ble_doc

    for required_text in (
        "HA Voice PE",
        "ESP32-S3-BOX-3",
        "Waveshare S3 1.85C BOX V2",
        "future P4/C6",
        "absent Bluetooth adapter",
        "policy disabled",
        "policy ask pending",
        "lease expiry",
        "wrong adapter",
        "wrong node",
        "wrong pairing nonce",
        "malformed payload",
        "failed Wi-Fi association",
        "backend unreachable",
    ):
        assert required_text in physical_doc
