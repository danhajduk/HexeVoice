from pathlib import Path


DOC = Path("docs/firmware-ble-onboarding-integration.md")
PROVISIONING_DOC = Path("docs/firmware-provisioning.md")
FIRMWARE_README = Path("firmware/README.md")


def test_ble_onboarding_integration_locks_current_core_contract():
    doc = DOC.read_text(encoding="utf-8")

    for required_text in (
        "`ble.provision_wifi`",
        "`hardware.bluetooth.ble.provision_wifi`",
        "`1.0`",
        "`hexe.voice_node.wifi_backend.v1`",
        "`7f9c0000-5f04-4d8b-9a46-7c0f7a100000`",
        "`7f9c0001-5f04-4d8b-9a46-7c0f7a100000`",
        "`7f9c0002-5f04-4d8b-9a46-7c0f7a100000`",
        "`7f9c0003-5f04-4d8b-9a46-7c0f7a100000`",
        "`7f9c0004-5f04-4d8b-9a46-7c0f7a100000`",
        "`7f9c0005-5f04-4d8b-9a46-7c0f7a100000`",
        "GET /api/system/nodes/hardware/access-requests/schema",
        "GET /api/system/nodes/hardware/ble/provisioning/schemas/voice",
        "/api/supervisor/hardware/bluetooth/ble/provision-wifi",
    ):
        assert required_text in doc


def test_ble_onboarding_integration_maps_voice_payload_to_existing_nvs_keys():
    doc = DOC.read_text(encoding="utf-8")

    for payload_field, nvs_key in (
        ("`wifi_ssid`", "`wifi_ssid`"),
        ("`wifi_password`", "`wifi_password`"),
        ("`backend_host`", "`backend_host`"),
        ("`http_port`", "`http_port`"),
        ("`ws_port`", "`ws_port`"),
        ("`use_tls`", "`use_tls`"),
        ("`endpoint_name`", "`endpoint_id`"),
        ("`display_name`", "`display_name`"),
        ("successful apply", "`provisioned`"),
    ):
        assert payload_field in doc
        assert nvs_key in doc

    assert "save_endpoint_provisioning" in doc
    assert "Firmware must write through the existing endpoint provisioning settings path" in doc


def test_ble_onboarding_integration_preserves_security_and_failure_boundaries():
    doc = DOC.read_text(encoding="utf-8")

    for required_text in (
        "Core must not receive, persist, or log plaintext Wi-Fi credentials",
        "Presence of a Bluetooth adapter is not authorization",
        "Supervisor receives plaintext Voice payload fields only for the bounded broker operation",
        "`gatt_backend_unavailable`",
        "`credential_payload.wifi_password`",
        "`invalid_nonce`",
        "`invalid_claim_code`",
        "`decrypt_failed`",
        "`unsupported_schema`",
        "`wifi_apply_failed`",
        "`backend_unreachable`",
        "`already_provisioned`",
        "Do not persist partial credentials",
    ):
        assert required_text in doc


def test_ble_onboarding_integration_defines_hexevoice_owned_followup_tasks():
    doc = DOC.read_text(encoding="utf-8")

    for heading in (
        "### Task 288: Endpoint firmware peripheral",
        "### Task 289: Backend/operator orchestration",
        "### Task 290: Recovery support",
        "### Task 291: Validation",
    ):
        assert heading in doc

    assert "Supervisor-owned: provide and validate a real physical BLE/GATT backend" in doc
    assert "Current Supervisor behavior is intentionally pluggable and fails closed" in doc


def test_ble_onboarding_integration_is_linked_from_firmware_docs():
    target = "docs/firmware-ble-onboarding-integration.md"

    assert target in FIRMWARE_README.read_text(encoding="utf-8")
    assert "firmware-ble-onboarding-integration.md" in PROVISIONING_DOC.read_text(encoding="utf-8")
