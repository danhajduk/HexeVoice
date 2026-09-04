import base64
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import os
import wave

from fastapi.testclient import TestClient
import httpx
import hexevoice.main as main_module
import hexevoice.endpoint.ble_onboarding as ble_onboarding_module

from hexevoice.api.models import AssistantTurnRequest
from hexevoice.assistant import AiNodeAssistantAdapter, AssistantTurnService, ConversationTurn, LocalEchoAssistantAdapter
from hexevoice.capabilities.service import VOICE_NODE_CAPABILITIES
from hexevoice.main import (
    OTA_MANIFEST_SIGNATURE_ALGORITHM,
    _seconds_until_next_local_midnight,
    _tts_warmup_voices,
    cleanup_voice_artifacts_once,
    create_app,
    ota_manifest_key_id,
    ota_manifest_signature_payload,
    ota_manifest_signing_key,
)
from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState
from hexevoice.runtime.service import NodeRuntimeService
from hexevoice.tts.service import resolve_piper_voice_model_id
from hexevoice.voice import DeterministicWakeDetector


def test_status_endpoint(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    response = client.get("/api/node/status")
    assert response.status_code == 200
    assert response.json()["trust_state"] == "untrusted"
    assert response.json()["lifecycle_state"] == "unconfigured"
    assert response.json()["current_step_id"] == "node_identity"
    assert response.json()["capability_status"] == "missing"
    assert response.json()["governance_sync_status"] == "pending_capability"


def test_standard_route_groups_exist(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    onboarding = client.get("/api/onboarding/status")
    assert onboarding.status_code == 200
    assert onboarding.json()["current_step_id"] == "node_identity"
    assert len(onboarding.json()["steps"]) == 10
    assert onboarding.json()["capability_setup"]["readiness_flags"]["trust_state_valid"] is False
    assert client.get("/api/onboarding/local-setup").status_code == 200
    assert client.get("/api/onboarding/bootstrap-discovery").status_code == 200
    assert client.post("/api/onboarding/session/start").status_code == 400
    assert client.post("/api/onboarding/session/poll").status_code == 400
    assert client.post("/api/onboarding/trust-activation/finalize").status_code == 400
    assert client.post("/api/onboarding/trust-status/refresh").status_code == 400
    assert client.get("/api/providers/setup").status_code == 200
    assert client.post("/api/engines/heartbeat", json={"engine_id": "piper_tts", "health_state": "ok"}).status_code == 200
    assert client.get("/api/endpoint/status/box-1").status_code == 404
    assert client.post("/api/endpoint/heartbeat", json={"endpoint_id": "box-1"}).status_code == 200
    assert client.get("/api/endpoint/time").status_code == 200
    assert client.post("/api/endpoint/ble/provision-wifi", json={}).status_code == 422
    assert client.post("/api/endpoint/ble/scan", json={}).status_code == 400
    assert client.post("/api/endpoint/ble/identity", json={}).status_code == 422
    assert client.get("/api/endpoint/media").status_code == 200
    assert client.get("/api/firmware/manifest").status_code in {200, 404}
    assert client.get("/api/capabilities").status_code == 200
    assert client.post("/api/capabilities/declaration").status_code == 400
    assert client.get("/api/governance/current").status_code == 400
    assert client.post("/api/governance/refresh").status_code == 400
    assert client.get("/api/governance/readiness").status_code == 200
    assert client.get("/api/node/operational-status").status_code == 400
    assert client.get("/api/services/status").status_code == 200
    assert client.get("/api/providers/voice/status").status_code == 200
    assert client.get("/api/voice/intents").status_code == 200
    assistant_turn = client.post("/api/assistant/turn", json={"endpoint_id": "box-1", "text": "hello"})
    assert assistant_turn.status_code == 200
    assert assistant_turn.json()["heard_text"] == "hello"


def test_endpoint_heartbeat_records_latest_status(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    heartbeat = client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "hardware_id": "esp32s3-b43a4512ab90",
            "device_state": "listening",
            "session_id": "session-voice-1",
            "firmware_version": "0.1.0",
            "ip_address": "10.0.0.55",
            "rssi_dbm": -58,
        },
    )

    assert heartbeat.status_code == 200
    heartbeat_payload = heartbeat.json()
    assert heartbeat_payload["accepted"] is True
    assert heartbeat_payload["endpoint_id"] == "esp-box-1"
    assert heartbeat_payload["device_state"] == "listening"
    assert heartbeat_payload["session_id"] == "session-voice-1"
    assert heartbeat_payload["last_seen_at"]

    status = client.get("/api/endpoint/status/esp-box-1")
    assert status.status_code == 200
    status_payload = status.json()
    assert status_payload["endpoint_id"] == "esp-box-1"
    assert status_payload["hardware_id"] == "esp32s3-b43a4512ab90"
    assert status_payload["device_state"] == "listening"
    assert status_payload["session_id"] == "session-voice-1"
    assert status_payload["firmware_version"] == "0.1.0"
    assert status_payload["ip_address"] == "10.0.0.55"
    assert status_payload["rssi_dbm"] == -58


def test_endpoint_registry_delete_removes_endpoint(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    heartbeat = client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "hardware_id": "esp32s3-b43a4512ab90",
            "device_state": "idle",
            "firmware_version": "0.1.0",
        },
    )
    assert heartbeat.status_code == 200

    deleted = client.delete("/api/endpoints/esp-box-1")

    assert deleted.status_code == 200
    deleted_payload = deleted.json()
    assert deleted_payload["deleted"] is True
    assert deleted_payload["endpoint_id"] == "esp-box-1"
    assert deleted_payload["endpoint"]["hardware_id"] == "esp32s3-b43a4512ab90"
    assert client.get("/api/endpoints").json()["endpoints"] == []
    assert client.get("/api/endpoint/status/esp-box-1").status_code == 404
    assert client.delete("/api/endpoints/esp-box-1").status_code == 404


def test_endpoint_heartbeat_accepts_ota_device_state(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    heartbeat = client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "device_state": "ota",
            "capabilities": {
                "firmware": {
                    "ota": {
                        "active": True,
                        "status": "running",
                        "progress_percent": 42,
                    }
                }
            },
        },
    )

    assert heartbeat.status_code == 200
    assert heartbeat.json()["device_state"] == "ota"

    status = client.get("/api/endpoint/status/esp-box-1")
    assert status.status_code == 200
    payload = status.json()
    assert payload["device_state"] == "ota"
    assert payload["capabilities"]["firmware"]["ota"]["active"] is True
    assert payload["capabilities"]["firmware"]["ota"]["progress_percent"] == 42


def test_endpoint_time_returns_clock_sync_payload(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    response = client.get("/api/endpoint/time")

    assert response.status_code == 200
    payload = response.json()
    assert payload["server_time"]
    assert payload["server_unix_ms"] > 1_600_000_000_000
    assert isinstance(payload["timezone"], str)
    assert isinstance(payload["utc_offset_seconds"], int)
    assert payload["sync_interval_ms"] == 300_000


def test_endpoint_media_inventory_projects_heartbeat_storage_inventory(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    heartbeat = client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "capabilities": {
                "storage": {
                    "sd_card_available": True,
                    "media_inventory": {
                        "pictures": [{"filename": "Idle.rgb565", "size_bytes": 153600}],
                        "sprites": [{"filename": "badge.rgb565", "size_bytes": 2048}],
                        "sounds": [{"filename": "ready.wav", "size_bytes": 8820}],
                        "truncated": True,
                    },
                }
            },
        },
    )

    assert heartbeat.status_code == 200
    inventory = client.get("/api/endpoint/media/inventory/esp-box-1")
    assert inventory.status_code == 200
    payload = inventory.json()
    assert payload["endpoint_id"] == "esp-box-1"
    assert payload["pictures"] == [{"filename": "Idle.rgb565", "size_bytes": 153600, "sha256": None, "content_type": None, "updated_at": None}]
    assert payload["sprites"][0]["filename"] == "badge.rgb565"
    assert payload["sounds"][0]["filename"] == "ready.wav"
    assert payload["truncated"] is True
    assert payload["last_seen_at"]


def test_firmware_manifest_serves_runtime_artifact(tmp_path):
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    (firmware_dir / "hexe_firmware.bin").write_bytes(b"firmware-bin")
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                firmware_artifact_dir=firmware_dir,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    manifest = client.get("/api/firmware/manifest")
    artifact = client.get("/api/firmware/artifacts/hexe_firmware.bin")

    assert manifest.status_code == 200
    manifest_payload = manifest.json()
    assert manifest_payload["url"] == "http://voice-node.local:9004/api/firmware/artifacts/hexe_firmware.bin"
    assert manifest_payload["size_bytes"] == len(b"firmware-bin")
    assert manifest_payload["profile"] == "esp_box_3"
    assert manifest_payload["signature_algorithm"] == OTA_MANIFEST_SIGNATURE_ALGORITHM
    assert manifest_payload["signature_key_id"] == ota_manifest_key_id()
    signed_payload = ota_manifest_signature_payload(
        profile=manifest_payload["profile"],
        url=manifest_payload["url"],
        version=None,
        sha256=manifest_payload["sha256"],
        size_bytes=manifest_payload["size_bytes"],
        application_type=manifest_payload["application_type"],
        board_profile=manifest_payload["board_profile"],
        soc=manifest_payload["soc"],
        idf_target=manifest_payload["idf_target"],
        flash_size=manifest_payload["flash_size"],
        psram_size=manifest_payload["psram_size"],
        partition_schema=manifest_payload["partition_schema"],
        app_slot_size=manifest_payload["app_slot_size"],
        firmware_api_version=manifest_payload["firmware_api_version"],
        model_api_version=manifest_payload["model_api_version"],
        asset_api_version=manifest_payload["asset_api_version"],
        calibration_schema_version=manifest_payload["calibration_schema_version"],
        release_channel=manifest_payload["release_channel"],
        security_policy=manifest_payload["security_policy"],
        signature_algorithm=manifest_payload["signature_algorithm"],
        signature_key_id=manifest_payload["signature_key_id"],
    )
    assert manifest_payload["manifest_signature"] == hmac.new(
        ota_manifest_signing_key().encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert artifact.status_code == 200
    assert artifact.content == b"firmware-bin"


def test_endpoint_status_includes_firmware_update_metadata(tmp_path):
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    (firmware_dir / "hexe_firmware_ha_voice_pe.bin").write_bytes(b"pe-firmware")
    (firmware_dir / "manifest-ha_voice_pe.json").write_text(
        json.dumps(
            {
                "version": "recovery-0.1.0",
                "application_type": "recovery",
                "board_profile": "ha_voice_pe",
                "filename": "hexe_recovery_ha_voice_pe.bin",
            }
        ),
        encoding="utf-8",
    )
    (firmware_dir / "manifest-endpoint-ha_voice_pe.json").write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "application_type": "endpoint",
                "board_profile": "ha_voice_pe",
                "soc": "esp32s3",
                "idf_target": "esp32s3",
                "flash_size": "16MiB",
                "psram_size": "8MiB",
                "partition_schema": "s3-16m-recovery-v1",
                "app_slot_size": "4MiB",
                "firmware_api_version": "hexe-firmware-main-api-v1",
                "model_api_version": "hexe-model-bundle-api-v1",
                "asset_api_version": "hexe-asset-bundle-api-v1",
                "calibration_schema_version": "hexe-calibration-schema-v1",
                "release_channel": "dev",
                "security_policy": "signed_manifest_sha256_required",
                "filename": "hexe_firmware_ha_voice_pe.bin",
                "sha256": "abc123",
                "created_at_utc": "2026-05-09T20:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                firmware_artifact_dir=firmware_dir,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    heartbeat = client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-pe-1",
            "firmware_version": "0.1.0",
            "capabilities": {"firmware": {"board_profile": "ha_voice_pe"}},
        },
    )
    status = client.get("/api/endpoint/status/esp-pe-1")

    assert heartbeat.status_code == 200
    assert status.status_code == 200
    firmware_update = status.json()["firmware_update"]
    assert firmware_update["board_profile"] == "ha_voice_pe"
    assert firmware_update["current_version"] == "0.1.0"
    assert firmware_update["latest_version"] == "0.2.0"
    assert firmware_update["update_available"] is True
    assert firmware_update["filename"] == "hexe_firmware_ha_voice_pe.bin"
    assert firmware_update["url"] == "http://voice-node.local:9004/api/firmware/artifacts/hexe_firmware_ha_voice_pe.bin"
    assert firmware_update["sha256"] == hashlib.sha256(b"pe-firmware").hexdigest()
    assert firmware_update["profile"] == "ha_voice_pe"
    assert firmware_update["size_bytes"] == len(b"pe-firmware")
    assert firmware_update["image_size_bytes"] == len(b"pe-firmware")
    assert firmware_update["application_type"] == "endpoint"
    assert firmware_update["soc"] == "esp32s3"
    assert firmware_update["idf_target"] == "esp32s3"
    assert firmware_update["flash_size"] == "16MiB"
    assert firmware_update["psram_size"] == "8MiB"
    assert firmware_update["partition_schema"] == "s3-16m-recovery-v1"
    assert firmware_update["app_slot_size"] == "4MiB"
    assert firmware_update["firmware_api_version"] == "hexe-firmware-main-api-v1"
    assert firmware_update["model_api_version"] == "hexe-model-bundle-api-v1"
    assert firmware_update["asset_api_version"] == "hexe-asset-bundle-api-v1"
    assert firmware_update["calibration_schema_version"] == "hexe-calibration-schema-v1"
    assert firmware_update["release_channel"] == "dev"
    assert firmware_update["security_policy"] == "signed_manifest_sha256_required"
    assert firmware_update["signature_algorithm"] == OTA_MANIFEST_SIGNATURE_ALGORITHM
    assert firmware_update["signature_key_id"] == ota_manifest_key_id()
    assert len(firmware_update["manifest_signature"]) == 64


def test_endpoint_status_marks_minimal_firmware_update_required(tmp_path):
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    (firmware_dir / "hexe_firmware_ha_voice_pe.bin").write_bytes(b"pe-firmware")
    (firmware_dir / "manifest-endpoint-ha_voice_pe.json").write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "application_type": "endpoint",
                "board_profile": "ha_voice_pe",
                "filename": "hexe_firmware_ha_voice_pe.bin",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                firmware_artifact_dir=firmware_dir,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    heartbeat = client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-pe-1",
            "firmware_version": "min-z20260904175449-c4897ab",
            "capabilities": {
                "firmware": {
                    "board_profile": "ha_voice_pe",
                    "application_type": "recovery",
                }
            },
        },
    )
    status = client.get("/api/endpoint/status/esp-pe-1")

    assert heartbeat.status_code == 200
    assert status.status_code == 200
    firmware_update = status.json()["firmware_update"]
    assert firmware_update["current_version"] == "min-z20260904175449-c4897ab"
    assert firmware_update["latest_version"] == "0.2.0"
    assert firmware_update["update_available"] is True
    assert firmware_update["reason"] == "minimal_firmware_update_required"
    assert firmware_update["filename"] == "hexe_firmware_ha_voice_pe.bin"
    assert firmware_update["application_type"] == "endpoint"


class FakePairingCoreClient:
    def __init__(self, *, approved_device_id: str = "esp-pe-1") -> None:
        self.approved_device_id = approved_device_id

    def get_ble_pairing_session(self, *, core_base_url: str, node_trust_token: str, node_id: str, session_id: str, refresh: bool = True) -> dict:
        assert core_base_url == "http://core.local"
        assert node_trust_token == "node-token"
        assert node_id == "voice-node-main"
        return {
            "ok": True,
            "pairing_session": {
                "session_id": session_id,
                "status": "approved",
                "approved_device_id": self.approved_device_id,
                "endpoint_identity": {
                    "device_id": "esp-pe-1",
                    "target_node_id": "esp-pe-1",
                    "board_profile": "ha_voice_pe",
                    "firmware_version": "z-recovery",
                    "application_type": "recovery",
                    "onboarding_session_id": session_id,
                },
            },
        }


class FakeRecoveryInstallClient:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict, content: bytes):
        self.__class__.calls.append({"url": url, "headers": headers, "content": content})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "installed_partition": "ota_0",
                "version": headers["X-Hexe-Version"],
                "reboot_scheduled": True,
            },
        )


def save_trusted_node_state(path):
    store = OnboardingStateStore(path=path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {"core_base_url": "http://core.local"},
                "trust_activation": {
                    "node_id": "voice-node-main",
                    "node_trust_token": "node-token",
                    "trust_status": "trusted",
                },
            }
        )
    )


def test_ble_pairing_firmware_handoff_installs_endpoint_image_only_after_identity_match(tmp_path, monkeypatch):
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    firmware_bytes = b"endpoint-pe-firmware"
    (firmware_dir / "hexe_firmware_ha_voice_pe.bin").write_bytes(firmware_bytes)
    (firmware_dir / "manifest-endpoint-ha_voice_pe.json").write_text(
        json.dumps(
            {
                "version": "endpoint-0.2.0",
                "application_type": "endpoint",
                "board_profile": "ha_voice_pe",
                "soc": "esp32s3",
                "idf_target": "esp32s3",
                "flash_size": "16MiB",
                "psram_size": "8MiB",
                "partition_schema": "s3-16m-recovery-v1",
                "app_slot_size": "4MiB",
                "firmware_api_version": "hexe-firmware-main-api-v1",
                "model_api_version": "hexe-model-bundle-api-v1",
                "asset_api_version": "hexe-asset-bundle-api-v1",
                "calibration_schema_version": "hexe-calibration-schema-v1",
                "release_channel": "dev",
                "security_policy": "signed_manifest_sha256_required",
                "filename": "hexe_firmware_ha_voice_pe.bin",
            }
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"
    save_trusted_node_state(state_path)
    FakeRecoveryInstallClient.calls = []
    monkeypatch.setattr(ble_onboarding_module, "CoreOnboardingClient", lambda: FakePairingCoreClient())
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeRecoveryInstallClient)
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=state_path,
                firmware_artifact_dir=firmware_dir,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    discovery = client.post(
        "/api/endpoint/discovery/offer",
        json={
            "endpoint_id": "esp-pe-1",
            "device_id": "esp-pe-1",
            "onboarding_session_id": "blepair-test",
            "board_profile": "ha_voice_pe",
            "firmware_version": "z-recovery",
            "application_type": "recovery",
        },
    )
    response = client.post("/api/endpoint/ble/pairing-sessions/blepair-test/firmware-handoff", json={"auto_reboot": True})

    assert discovery.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "rebooting"
    assert payload["ui_state"] == "rebooting"
    assert payload["endpoint_id"] == "esp-pe-1"
    assert payload["firmware"]["application_type"] == "endpoint"
    assert payload["firmware"]["filename"] == "hexe_firmware_ha_voice_pe.bin"
    assert len(FakeRecoveryInstallClient.calls) == 1
    call = FakeRecoveryInstallClient.calls[0]
    assert call["url"] == "http://testclient/api/recovery/firmware/install"
    assert call["content"] == firmware_bytes
    assert call["headers"]["X-Hexe-Application-Type"] == "endpoint"
    assert call["headers"]["X-Hexe-Board-Profile"] == "ha_voice_pe"
    assert call["headers"]["X-Hexe-Partition-Schema"] == "s3-16m-recovery-v1"
    assert call["headers"]["X-Hexe-Reboot-After-Install"] == "true"
    install_signature_payload = "\n".join(
        [
            "endpoint",
            "ha_voice_pe",
            "s3-16m-recovery-v1",
            "endpoint-0.2.0",
            hashlib.sha256(firmware_bytes).hexdigest(),
            str(len(firmware_bytes)),
            OTA_MANIFEST_SIGNATURE_ALGORITHM,
            ota_manifest_key_id(),
        ]
    )
    expected_install_signature = hmac.new(
        ota_manifest_signing_key().encode("utf-8"),
        install_signature_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert call["headers"]["X-Hexe-Manifest-Signature"] == expected_install_signature
    assert call["headers"]["X-Hexe-Manifest-Signature"] != payload["firmware"]["manifest_signature"]


def test_ble_pairing_firmware_handoff_rejects_unapproved_or_mismatched_identity(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    save_trusted_node_state(state_path)
    monkeypatch.setattr(ble_onboarding_module, "CoreOnboardingClient", lambda: FakePairingCoreClient(approved_device_id="other-device"))
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=state_path,
                endpoint_discovery_udp_enabled=False,
            )
        )
    )

    response = client.post("/api/endpoint/ble/pairing-sessions/blepair-test/firmware-handoff", json={})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"] == "ble_pairing_not_approved"


def test_firmware_ota_push_sends_update_event_to_connected_endpoint(tmp_path):
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    (firmware_dir / "hexe_firmware.bin").write_bytes(b"firmware-bin")
    (firmware_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": "0.1.1",
                "application_type": "endpoint",
                "board_profile": "esp_box_3",
                "soc": "esp32s3",
                "idf_target": "esp32s3",
                "flash_size": "16MiB",
                "psram_size": "16MiB",
                "partition_schema": "s3-16m-recovery-v1",
                "app_slot_size": "4MiB",
                "firmware_api_version": "hexe-firmware-main-api-v1",
                "model_api_version": "hexe-model-bundle-api-v1",
                "asset_api_version": "hexe-asset-bundle-api-v1",
                "calibration_schema_version": "hexe-calibration-schema-v1",
                "release_channel": "dev",
                "security_policy": "signed_manifest_sha256_required",
                "filename": "hexe_firmware.bin",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                firmware_artifact_dir=firmware_dir,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {"firmware_version": "0.1.0"},
            }
        )
        websocket.receive_json()
        response = client.post(
            "/api/firmware/ota/push",
            json={"endpoint_id": "esp-box-1", "version": "0.1.1"},
        )
        event = websocket.receive_json()
        status = client.get("/api/voice/status").json()

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert event["event_type"] == "ota.update"
    assert event["endpoint_id"] == "esp-box-1"
    assert event["payload"]["url"] == "http://voice-node.local:9004/api/firmware/artifacts/hexe_firmware.bin"
    assert event["payload"]["version"] == "0.1.1"
    assert event["payload"]["profile"] == "esp_box_3"
    assert event["payload"]["size_bytes"] == len(b"firmware-bin")
    assert event["payload"]["application_type"] == "endpoint"
    assert event["payload"]["board_profile"] == "esp_box_3"
    assert event["payload"]["soc"] == "esp32s3"
    assert event["payload"]["idf_target"] == "esp32s3"
    assert event["payload"]["flash_size"] == "16MiB"
    assert event["payload"]["psram_size"] == "16MiB"
    assert event["payload"]["partition_schema"] == "s3-16m-recovery-v1"
    assert event["payload"]["app_slot_size"] == "4MiB"
    assert event["payload"]["firmware_api_version"] == "hexe-firmware-main-api-v1"
    assert event["payload"]["model_api_version"] == "hexe-model-bundle-api-v1"
    assert event["payload"]["asset_api_version"] == "hexe-asset-bundle-api-v1"
    assert event["payload"]["calibration_schema_version"] == "hexe-calibration-schema-v1"
    assert event["payload"]["release_channel"] == "dev"
    assert event["payload"]["security_policy"] == "signed_manifest_sha256_required"
    assert event["payload"]["signature_algorithm"] == OTA_MANIFEST_SIGNATURE_ALGORITHM
    assert event["payload"]["signature_key_id"] == ota_manifest_key_id()
    signed_payload = ota_manifest_signature_payload(
        profile=event["payload"]["profile"],
        url=event["payload"]["url"],
        version=event["payload"]["version"],
        sha256=event["payload"]["sha256"],
        size_bytes=event["payload"]["size_bytes"],
        application_type=event["payload"]["application_type"],
        board_profile=event["payload"]["board_profile"],
        soc=event["payload"]["soc"],
        idf_target=event["payload"]["idf_target"],
        flash_size=event["payload"]["flash_size"],
        psram_size=event["payload"]["psram_size"],
        partition_schema=event["payload"]["partition_schema"],
        app_slot_size=event["payload"]["app_slot_size"],
        firmware_api_version=event["payload"]["firmware_api_version"],
        model_api_version=event["payload"]["model_api_version"],
        asset_api_version=event["payload"]["asset_api_version"],
        calibration_schema_version=event["payload"]["calibration_schema_version"],
        release_channel=event["payload"]["release_channel"],
        security_policy=event["payload"]["security_policy"],
        signature_algorithm=event["payload"]["signature_algorithm"],
        signature_key_id=event["payload"]["signature_key_id"],
    )
    assert event["payload"]["manifest_signature"] == hmac.new(
        ota_manifest_signing_key().encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    command = next(command for command in status["commands"] if command["command_type"] == "ota.update")
    timeout_at = datetime.fromtimestamp(command["timeout_at"], tz=timezone.utc)
    created_at = datetime.fromisoformat(command["created_at"])
    assert (timeout_at - created_at).total_seconds() >= 170


def test_firmware_manifest_rejects_minimal_artifacts_and_manifests(tmp_path):
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    (firmware_dir / "hexe_min_ha_voice_pe.bin").write_bytes(b"minimal")
    (firmware_dir / "hexe_firmware_ha_voice_pe.bin").write_bytes(b"pe-firmware")
    (firmware_dir / "manifest-endpoint-ha_voice_pe.json").write_text(
        json.dumps(
            {
                "version": "min-z-test",
                "application_type": "endpoint",
                "board_profile": "ha_voice_pe",
                "filename": "hexe_firmware_ha_voice_pe.bin",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                firmware_artifact_dir=firmware_dir,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    minimal_filename = client.get("/api/firmware/manifest?filename=hexe_min_ha_voice_pe.bin")
    minimal_manifest = client.get("/api/firmware/manifest?filename=hexe_firmware_ha_voice_pe.bin")

    assert minimal_filename.status_code == 400
    assert minimal_filename.json()["detail"] == "minimal_firmware_not_allowed_for_ota"
    assert minimal_manifest.status_code == 400
    assert minimal_manifest.json()["detail"] == "minimal_firmware_not_allowed_for_ota"


def test_firmware_ota_push_rejects_minimal_artifacts_and_versions(tmp_path):
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    (firmware_dir / "hexe_firmware.bin").write_bytes(b"endpoint")
    (firmware_dir / "hexe_min_ha_voice_pe.bin").write_bytes(b"minimal")
    (firmware_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "application_type": "endpoint",
                "board_profile": "esp_box_3",
                "filename": "hexe_firmware.bin",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                firmware_artifact_dir=firmware_dir,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    minimal_filename = client.post(
        "/api/firmware/ota/push",
        json={
            "endpoint_id": "esp-pe-1",
            "filename": "hexe_min_ha_voice_pe.bin",
            "version": "0.2.0",
        },
    )
    minimal_version = client.post(
        "/api/firmware/ota/push",
        json={
            "endpoint_id": "esp-pe-1",
            "filename": "hexe_firmware.bin",
            "version": "min-z-test",
        },
    )

    assert minimal_filename.status_code == 400
    assert minimal_filename.json()["detail"] == "minimal_firmware_not_allowed_for_ota"
    assert minimal_version.status_code == 400
    assert minimal_version.json()["detail"] == "minimal_firmware_not_allowed_for_ota"


def test_firmware_ota_clear_removes_ota_command_records(tmp_path):
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    (firmware_dir / "hexe_firmware.bin").write_bytes(b"firmware-bin")
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                firmware_artifact_dir=firmware_dir,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {"firmware_version": "0.1.0"},
            }
        )
        websocket.receive_json()
        client.post(
            "/api/firmware/ota/push",
            json={"endpoint_id": "esp-box-1", "version": "0.1.1"},
        )
        websocket.receive_json()

    assert len(client.get("/api/voice/status").json()["commands"]) == 1

    response = client.post("/api/firmware/ota/clear")

    assert response.status_code == 200
    assert response.json() == {"cleared": 1, "endpoint_id": None}
    assert client.get("/api/voice/status").json()["commands"] == []


def test_endpoint_volume_command_sends_event_to_connected_endpoint(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {"firmware_version": "0.1.0"},
            }
        )
        websocket.receive_json()
        response = client.post(
            "/api/endpoint/volume",
            json={"endpoint_id": "esp-box-1", "volume_percent": 42},
        )
        event = websocket.receive_json()

    assert response.status_code == 200
    assert response.json() == {
        "accepted": True,
        "endpoint_id": "esp-box-1",
        "volume_percent": 42,
        "request_id": response.json()["request_id"],
        "status": "pending",
        "reason": None,
    }
    assert event["event_type"] == "endpoint.volume"
    assert event["endpoint_id"] == "esp-box-1"
    assert event["direction"] == "backend_to_endpoint"
    assert event["payload"]["request_id"] == response.json()["request_id"]
    assert event["payload"]["volume_percent"] == 42


def test_endpoint_volume_command_requires_valid_percent(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    response = client.post(
        "/api/endpoint/volume",
        json={"endpoint_id": "esp-box-1", "volume_percent": 101},
    )

    assert response.status_code == 422


def test_endpoint_micro_vad_command_sends_event_to_connected_endpoint(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {"firmware_version": "0.1.0"},
            }
        )
        websocket.receive_json()
        response = client.post(
            "/api/endpoint/micro-vad",
            json={"endpoint_id": "esp-box-1", "pause_ms": 2200, "energy_threshold": 300},
        )
        event = websocket.receive_json()

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["command_type"] == "endpoint.micro_vad.set"
    assert response.json()["status"] == "pending"
    assert event["event_type"] == "endpoint.micro_vad"
    assert event["endpoint_id"] == "esp-box-1"
    assert event["payload"]["request_id"] == response.json()["request_id"]
    assert event["payload"]["pause_ms"] == 2200
    assert event["payload"]["energy_threshold"] == 300


def test_endpoint_micro_vad_command_requires_valid_pause(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    response = client.post(
        "/api/endpoint/micro-vad",
        json={"endpoint_id": "esp-box-1", "pause_ms": 20},
    )

    assert response.status_code == 422


def test_endpoint_micro_vad_command_requires_a_setting(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    response = client.post(
        "/api/endpoint/micro-vad",
        json={"endpoint_id": "esp-box-1"},
    )

    assert response.status_code == 422


def test_endpoint_volume_status_reports_latest_command(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {"firmware_version": "0.1.0"},
            }
        )
        websocket.receive_json()
        response = client.post(
            "/api/endpoint/volume",
            json={"endpoint_id": "esp-box-1", "volume_percent": 42},
        )
        volume_event = websocket.receive_json()
        websocket.send_json(
            {
                "event_type": "command.ack",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {
                    "request_id": volume_event["payload"]["request_id"],
                    "command_type": "endpoint.volume.set",
                    "status": "succeeded",
                },
            }
        )
        websocket.receive_json()
        status = client.get("/api/endpoint/volume/esp-box-1")

    assert status.status_code == 200
    assert status.json()["volume_percent"] == 42
    assert status.json()["latest_command"]["request_id"] == response.json()["request_id"]
    assert status.json()["latest_command"]["status"] == "succeeded"
    assert status.json()["latest_command"]["terminal"] is True


def test_endpoint_mute_cancel_and_replay_commands_send_events(tmp_path):
    client = TestClient(
        create_app(
            Settings(onboarding_state_path=tmp_path / "state.json"),
            voice_wake_detector=DeterministicWakeDetector(detect_on_chunk_index=0),
        )
    )

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {"firmware_version": "0.1.0"},
            }
        )
        websocket.receive_json()
        websocket.send_json(
            {
                "event_type": "audio.chunk",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {"chunk_index": 0, "audio_format": {"sample_rate_hz": 16000}},
            }
        )
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json(
            {
                "event_type": "audio.end",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {},
            }
        )
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()

        mute_response = client.post("/api/endpoint/mute", json={"endpoint_id": "esp-box-1", "muted": True})
        mute_event = websocket.receive_json()
        replay_response = client.post("/api/endpoint/replay", json={"endpoint_id": "esp-box-1"})
        replay_event = websocket.receive_json()
        speak_response = client.post("/api/endpoint/speak", json={"endpoint_id": "esp-box-1", "text": "Vioce test"})
        speak_event = websocket.receive_json()
        play_sound_response = client.post(
            "/api/interaction/ui/play-sound",
            json={
                "endpoint_id": "esp-box-1",
                "audio_url": "/api/voice/tts/tts-kiosk/48k",
                "stream_id": "tts-kiosk",
                "source_event_id": "interaction-ui-play-sound-1",
                "loop": True,
                "mic_mode": "interrupt_only",
            },
        )
        play_sound_event = websocket.receive_json()
        playback_stop_response = client.post("/api/endpoint/playback/stop", json={"endpoint_id": "esp-box-1"})
        playback_stop_event = websocket.receive_json()
        led_response = client.post(
            "/api/endpoint/led/simulate",
            json={"endpoint_id": "esp-box-1", "pattern": "all", "duration_ms": 900},
        )
        led_event = websocket.receive_json()
        listen_response = client.post("/api/endpoint/session/listen", json={"endpoint_id": "esp-box-1"})
        listen_event = websocket.receive_json()

        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-2",
                "payload": {"firmware_version": "0.1.0"},
            }
        )
        websocket.receive_json()
        cancel_response = client.post("/api/endpoint/session/cancel", json={"endpoint_id": "esp-box-1"})
        cancel_event = websocket.receive_json()

    assert mute_response.status_code == 200
    assert mute_response.json()["status"] == "pending"
    assert mute_event["event_type"] == "endpoint.mute"
    assert mute_event["payload"]["muted"] is True
    assert mute_event["payload"]["request_id"] == mute_response.json()["request_id"]
    assert replay_response.status_code == 200
    assert replay_event["event_type"] == "endpoint.replay"
    assert replay_event["payload"]["request_id"] == replay_response.json()["request_id"]
    assert replay_event["payload"]["stream_id"].startswith("tts-")
    assert speak_response.status_code == 200
    assert speak_response.json()["command_type"] == "endpoint.speak"
    assert speak_event["event_type"] == "endpoint.replay"
    assert speak_event["payload"]["request_id"] == speak_response.json()["request_id"]
    assert speak_event["payload"]["stream_id"].startswith("tts-")
    assert speak_event["payload"]["text"] == "Vioce test"
    assert play_sound_response.status_code == 200
    assert play_sound_response.json()["command_type"] == "endpoint.play_sound"
    assert play_sound_event["event_type"] == "endpoint.replay"
    assert play_sound_event["payload"]["request_id"] == play_sound_response.json()["request_id"]
    assert play_sound_event["payload"]["command"] == "ui.play_sound"
    assert play_sound_event["payload"]["stream_id"] == "tts-kiosk"
    assert play_sound_event["payload"]["audio_url"] == "/api/voice/tts/tts-kiosk/48k"
    assert play_sound_event["payload"]["source_event_id"] == "interaction-ui-play-sound-1"
    assert play_sound_event["payload"]["loop"] is True
    assert play_sound_event["payload"]["mic_mode"] == "interrupt_only"
    assert playback_stop_response.status_code == 200
    assert playback_stop_response.json()["command_type"] == "playback.stop"
    assert playback_stop_event["event_type"] == "playback.stop"
    assert playback_stop_event["payload"]["request_id"] == playback_stop_response.json()["request_id"]
    assert playback_stop_event["payload"]["reason"] == "operator_stop"
    assert led_response.status_code == 200
    assert led_response.json()["command_type"] == "endpoint.led.simulate"
    assert led_event["event_type"] == "endpoint.led.simulate"
    assert led_event["payload"]["request_id"] == led_response.json()["request_id"]
    assert led_event["payload"]["pattern"] == "all"
    assert led_event["payload"]["duration_ms"] == 900
    assert listen_response.status_code == 200
    assert listen_response.json()["command_type"] == "endpoint.listen"
    assert listen_event["event_type"] == "endpoint.listen"
    assert listen_event["payload"]["request_id"] == listen_response.json()["request_id"]
    assert cancel_response.status_code == 200
    assert cancel_event["event_type"] == "endpoint.cancel"
    assert cancel_event["payload"]["request_id"] == cancel_response.json()["request_id"]


def test_endpoint_beep_command_defaults_to_connected_endpoint_and_stages_sound(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                endpoint_media_dir=tmp_path / "media",
            )
        )
    )

    with client.websocket_connect("/api/voice/ws?endpoint_id=esp-pe-1") as websocket:
        response = client.post("/api/endpoint/beep", json={})
        event = websocket.receive_json()
        served = client.get("/api/endpoint/media/files/test_beep_short_500ms")

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["endpoint_id"] == "esp-pe-1"
    assert response.json()["command_type"] == "endpoint.beep"
    assert response.json()["status"] == "pending"
    assert event["event_type"] == "endpoint.replay"
    assert event["endpoint_id"] == "esp-pe-1"
    assert event["payload"]["command"] == "ui.play_sound"
    assert event["payload"]["stream_id"].startswith("beep-short-")
    assert event["payload"]["audio_url"] == "/api/endpoint/media/files/test_beep_short_500ms"
    assert event["payload"]["metadata"]["beep"] == "short"
    assert event["payload"]["metadata"]["asset_id"] == "test_beep_short_500ms"
    assert event["payload"]["metadata"]["duration_ms"] == 500
    assert served.status_code == 200
    assert served.content.startswith(b"RIFF")


def test_endpoint_beep_command_can_select_done_profile(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                endpoint_media_dir=tmp_path / "media",
            )
        )
    )

    with client.websocket_connect("/api/voice/ws?endpoint_id=esp-box-1") as websocket:
        response = client.post(
            "/api/endpoint/beep",
            json={"endpoint_id": "esp-box-1", "beep": "done", "source_event_id": "remote-beep-check"},
        )
        event = websocket.receive_json()

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["command_type"] == "endpoint.beep"
    assert event["payload"]["stream_id"].startswith("beep-done-")
    assert event["payload"]["audio_url"] == "/api/endpoint/media/files/test_beep_loud_1s"
    assert event["payload"]["source_event_id"] == "remote-beep-check"
    assert event["payload"]["metadata"]["beep"] == "done"
    assert event["payload"]["metadata"]["duration_ms"] == 1000


def test_endpoint_beep_command_reports_when_no_endpoint_is_connected(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    response = client.post("/api/endpoint/beep", json={})

    assert response.status_code == 200
    assert response.json() == {
        "accepted": False,
        "endpoint_id": "",
        "command_type": "endpoint.beep",
        "request_id": None,
        "status": "failed",
        "reason": "no_connected_endpoint",
    }


def test_endpoint_media_upload_validates_and_serves_picture_rgb565(tmp_path):
    payload = bytes(320 * 240 * 2)
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                endpoint_media_dir=tmp_path / "media",
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    upload = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "idle",
            "media_type": "picture",
            "filename": "Idle.rgb565",
            "content_base64": base64.b64encode(payload).decode("ascii"),
            "overwrite": True,
        },
    )
    listing = client.get("/api/endpoint/media")
    served = client.get("/api/endpoint/media/files/idle")

    assert upload.status_code == 200
    asset = upload.json()
    assert asset["asset_id"] == "idle"
    assert asset["destination"] == "picture"
    assert asset["endpoint_path"] == "/sdcard/hexe/pictures/Idle.rgb565"
    assert asset["size_bytes"] == 153600
    assert asset["metadata"]["pixel_format"] == "rgb565"
    assert asset["download_url"] == "http://voice-node.local:9004/api/endpoint/media/files/idle"
    assert listing.status_code == 200
    assert listing.json()["assets"][0]["asset_id"] == "idle"
    assert served.status_code == 200
    assert served.content == payload


def test_endpoint_media_upload_rejects_unsafe_filename(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", endpoint_media_dir=tmp_path / "media")))

    response = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "bad",
            "media_type": "picture",
            "filename": "../Idle.rgb565",
            "content_base64": base64.b64encode(bytes(320 * 240 * 2)).decode("ascii"),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_filename"


def test_endpoint_media_upload_rejects_invalid_base64_and_duplicate_asset(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", endpoint_media_dir=tmp_path / "media")))

    invalid = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "idle",
            "media_type": "picture",
            "filename": "Idle.rgb565",
            "content_base64": "not base64!",
        },
    )
    created = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "idle",
            "media_type": "picture",
            "filename": "Idle.rgb565",
            "content_base64": base64.b64encode(bytes(320 * 240 * 2)).decode("ascii"),
        },
    )
    duplicate = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "idle",
            "media_type": "picture",
            "filename": "Idle.rgb565",
            "content_base64": base64.b64encode(bytes(320 * 240 * 2)).decode("ascii"),
        },
    )
    rewritten = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "idle",
            "media_type": "picture",
            "filename": "Idle.rgb565",
            "content_base64": base64.b64encode(bytes(320 * 240 * 2)).decode("ascii"),
            "rewrite": True,
        },
    )

    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "invalid_content_base64"
    assert created.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "duplicate_media_asset"
    assert rewritten.status_code == 200


def test_endpoint_media_upload_rejects_sprite_without_dimensions(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", endpoint_media_dir=tmp_path / "media")))

    response = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "badge",
            "media_type": "sprite",
            "filename": "badge.rgb565",
            "content_base64": base64.b64encode(bytes(32 * 32 * 2)).decode("ascii"),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "missing_sprite_dimensions"


def test_endpoint_media_upload_accepts_alpha_mask_sprite(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", endpoint_media_dir=tmp_path / "media")))
    payload = bytes([0, 64, 128, 255])

    response = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "idle-alpha",
            "media_type": "sprite",
            "filename": "idle.alpha8",
            "content_base64": base64.b64encode(payload).decode("ascii"),
            "metadata": {"asset_class": "alpha_mask"},
        },
    )

    assert response.status_code == 200
    asset = response.json()
    assert asset["filename"] == "idle.alpha8"
    assert asset["destination"] == "sprite"
    assert asset["endpoint_path"] == "/sdcard/hexe/sprites/idle.alpha8"
    assert asset["metadata"]["alpha_format"] == "alpha8"
    assert asset["metadata"]["asset_class"] == "alpha_mask"


def test_endpoint_media_delete_removes_staged_payload_and_listing(tmp_path):
    media_dir = tmp_path / "media"
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", endpoint_media_dir=media_dir)))
    upload = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "idle",
            "media_type": "picture",
            "filename": "Idle.rgb565",
            "content_base64": base64.b64encode(bytes(320 * 240 * 2)).decode("ascii"),
        },
    )
    assert upload.status_code == 200
    payload_path = media_dir / "idle" / "Idle.rgb565"
    assert payload_path.exists()

    deleted = client.delete("/api/endpoint/media/idle")
    listing = client.get("/api/endpoint/media")

    assert deleted.status_code == 200
    assert deleted.json()["asset_id"] == "idle"
    assert not payload_path.exists()
    assert listing.json()["assets"] == []


def test_endpoint_media_deliver_sends_transfer_command(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                endpoint_media_dir=tmp_path / "media",
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )
    upload = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "logo",
            "media_type": "picture",
            "filename": "Logo.rgb565",
            "content_base64": base64.b64encode(bytes(320 * 240 * 2)).decode("ascii"),
            "overwrite": True,
        },
    )
    assert upload.status_code == 200

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {"firmware_version": "0.1.0"},
            }
        )
        websocket.receive_json()
        response = client.post(
            "/api/endpoint/media/logo/deliver",
            json={"endpoint_id": "esp-box-1", "overwrite": True, "activate": True},
        )
        event = websocket.receive_json()

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["status"] == "pending"
    assert event["event_type"] == "endpoint.media.transfer"
    assert event["payload"]["request_id"] == response.json()["request_id"]
    assert event["payload"]["media_type"] == "picture"
    assert event["payload"]["filename"] == "Logo.rgb565"
    assert event["payload"]["destination"] == "picture"
    assert event["payload"]["download_url"] == "http://voice-node.local:9004/api/endpoint/media/files/logo"
    assert event["payload"]["size_bytes"] == 153600
    assert event["payload"]["rewrite"] is True


def test_endpoint_media_deliver_reports_disconnected_endpoint(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                endpoint_media_dir=tmp_path / "media",
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )
    upload = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "logo",
            "media_type": "picture",
            "filename": "Logo.rgb565",
            "content_base64": base64.b64encode(bytes(320 * 240 * 2)).decode("ascii"),
            "overwrite": True,
        },
    )
    assert upload.status_code == 200

    response = client.post(
        "/api/endpoint/media/logo/deliver",
        json={"endpoint_id": "esp-box-1", "overwrite": True, "activate": True},
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["status"] == "failed"
    assert response.json()["reason"] == "endpoint_not_connected"


def test_endpoint_storage_reformat_sends_command(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {"firmware_version": "0.1.0"},
            }
        )
        websocket.receive_json()
        response = client.post("/api/endpoint/storage/reformat", json={"endpoint_id": "esp-box-1"})
        event = websocket.receive_json()

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["command_type"] == "endpoint.storage.reformat"
    assert event["event_type"] == "endpoint.storage.reformat"
    assert event["payload"]["request_id"] == response.json()["request_id"]


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 160)
    return buffer.getvalue()


def test_endpoint_media_upload_validates_sound_wav(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", endpoint_media_dir=tmp_path / "media")))

    response = client.post(
        "/api/endpoint/media",
        json={
            "asset_id": "wake",
            "media_type": "sound",
            "filename": "wake.wav",
            "content_base64": base64.b64encode(_wav_bytes()).decode("ascii"),
        },
    )

    assert response.status_code == 200
    assert response.json()["destination"] == "sound"
    assert response.json()["endpoint_path"] == "/sdcard/hexe/sounds/wake.wav"
    assert response.json()["metadata"]["audio_format"] == "wav_pcm"
    assert response.json()["metadata"]["sample_rate_hz"] == 16000


def test_assistant_turn_echoes_transcript_without_ai(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "resume": {
                    "current_step_id": "ready",
                },
                "operational_status": {
                    "operational_ready": True,
                    "active_governance_version": "gov-1",
                    "governance_freshness_state": "fresh",
                },
            }
        )
    )
    client = TestClient(create_app(Settings(onboarding_state_path=state_path, node_name="kitchen-voice")))

    response = client.post("/api/assistant/turn", json={"endpoint_id": "box-1", "text": "Hexa, status"})

    assert response.status_code == 200
    assert response.json()["heard_text"] == "status"
    assert response.json()["command"] is None
    assert response.json()["handled_locally"] is False
    assert response.json()["reply_text"] == "I heard status"
    assert response.json()["device_state"] == "speaking"
    assert response.json()["provider_id"] == "local_echo"
    assert response.json()["error"] is None


def test_tts_synthesize_returns_fetchable_audio_url(tmp_path):
    public_base_url = "http://voice-node.local:9004"
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                public_api_base_url=public_base_url,
            )
        )
    )

    response = client.post(
        "/api/tts/synthesize",
        json={
            "intent": "tts.speak",
            "target": {
                "device_id": "kiosk_kitchen_1",
                "location": "kitchen",
                "client_ip": "10.0.0.137",
                "playback": "browser_audio",
            },
            "text": "The kitchen timer is done.",
            "voice": "default",
            "format": "wav",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["audio_url"].startswith(f"{public_base_url}/api/tts/audio/")
    assert payload["audio_url"].endswith("/")
    assert payload["endpoint_audio_url"] == payload["audio_url"]
    assert payload["stream_url"] == payload["endpoint_audio_url"]
    assert payload["audio_urls"]["raw"] == payload["audio_url"]
    assert payload["stream_urls"]["raw"] == payload["audio_url"]
    assert payload["content_type"] == "audio/wav"
    assert payload["duration_ms"] is not None
    assert payload["expires_at"]
    assert payload["stream_id"].startswith("tts-")
    metadata = json.loads((tmp_path / "voice_tts" / f"{payload['stream_id']}.json").read_text(encoding="utf-8"))
    assert metadata["model_id"] == "deterministic"
    assert metadata["voice_id"] == "default"
    assert metadata["audio_url"] == payload["audio_url"]
    assert metadata["endpoint_audio_url"] == payload["endpoint_audio_url"]
    assert metadata["stream_url"] == payload["stream_url"]
    assert metadata["audio_url_raw"] == payload["audio_url"]
    assert metadata["ttl_seconds"] == 3600
    assert metadata["expires_at"] == payload["expires_at"]

    audio = client.get(payload["audio_url"].removeprefix(public_base_url))
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.content.startswith(b"RIFF")

    stream = client.get(payload["stream_url"].removeprefix(public_base_url))
    assert stream.status_code == 200
    assert stream.headers["content-type"] == "audio/wav"


def test_tts_common_clip_reuses_cached_audio(tmp_path):
    public_base_url = "http://voice-node.local:9004"
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                public_api_base_url=public_base_url,
            )
        )
    )
    payload = {
        "intent": "tts.speak",
        "text": "Welcome to the kiosk.",
        "voice": "default",
        "format": "wav",
        "cache_key": "kiosk-welcome",
    }

    first = client.post("/api/tts/common-clips", json=payload)
    second = client.post("/api/tts/common-clips", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["cache_key"] == "kiosk-welcome"
    assert first_payload["cache_hit"] is False
    assert second_payload["cache_hit"] is True
    assert second_payload["stream_id"] == first_payload["stream_id"] == "common-kiosk-welcome"
    assert second_payload["stream_url"] == first_payload["stream_url"]
    audio = client.get(second_payload["stream_url"].removeprefix(public_base_url))
    assert audio.status_code == 200
    assert audio.content.startswith(b"RIFF")


def test_tts_voices_lists_models_and_languages(tmp_path):
    model_dir = tmp_path / "piper-models"
    model_dir.mkdir()
    (model_dir / "en_US-kathleen-low.onnx").write_bytes(b"model")
    (model_dir / "en_US-kathleen-low.onnx.json").write_text(
        json.dumps({"dataset": "kathleen", "audio": {"sample_rate": 22050, "quality": "low"}, "language": {"code": "en_US"}}),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                piper_tts_model_dir=model_dir,
                voice_tts_piper_voice="en_US-kathleen-low",
                piper_tts_warm_voices="en_US-kathleen-low",
            )
        )
    )

    response = client.get("/api/tts/voices")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_voice"] == "en_US-kathleen-low"
    assert payload["warm_voices"] == ["en_US-kathleen-low"]
    assert payload["count"] == 1
    assert payload["voices"][0]["model_id"] == "en_US-kathleen-low"
    assert payload["voices"][0]["display_name"] == "Kathleen"
    assert payload["languages"][0]["language"] == "en_US"
    assert payload["languages"][0]["voices"] == ["en_US-kathleen-low"]


def test_tts_artifacts_debug_api_lists_recent_streams(tmp_path):
    public_base_url = "http://voice-node.local:9004"
    tts_dir = tmp_path / "voice_tts"
    tts_dir.mkdir()
    (tts_dir / "tts-debug.raw.wav").write_bytes(b"RIFFraw")
    (tts_dir / "tts-debug.48k.wav").write_bytes(b"RIFF48k")
    (tts_dir / "tts-debug.16k.wav").write_bytes(b"RIFF16k")
    (tts_dir / "tts-debug.json").write_text(
        json.dumps(
            {
                "stream_id": "tts-debug",
                "created_at": "2026-05-09T19:00:00+00:00",
                "expires_at": "2999-01-01T00:00:00+00:00",
                "provider_id": "piper",
                "model_id": "en_US-jenny-high",
                "voice_id": "en_US-jenny-high",
                "audio_url": "/api/voice/tts/tts-debug/",
                "endpoint_audio_url": "/api/voice/tts/tts-debug/48k",
                "audio_urls": {
                    "raw": "/api/voice/tts/tts-debug/raw",
                    "48k": "/api/voice/tts/tts-debug/48k",
                    "16k": "/api/voice/tts/tts-debug/16k",
                },
                "audio_variant": "48k",
                "raw_sample_rate_hz": 22050,
                "output_sample_rate_hz": 48000,
                "variant_sample_rates_hz": {"raw": 22050, "48k": 48000, "16k": 16000},
                "tts_timing_breakdown_ms": {"piper_generation_ms": 4.2},
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                public_api_base_url=public_base_url,
            )
        )
    )

    response = client.get("/api/tts/artifacts")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    artifact = payload["artifacts"][0]
    assert artifact["stream_id"] == "tts-debug"
    assert artifact["provider_id"] == "piper"
    assert artifact["model_id"] == "en_US-jenny-high"
    assert artifact["audio_variant"] == "48k"
    assert artifact["raw_sample_rate_hz"] == 22050
    assert artifact["output_sample_rate_hz"] == 48000
    assert artifact["variant_sample_rates_hz"]["16k"] == 16000
    assert artifact["file_sizes"]["raw"] == len(b"RIFFraw")
    assert artifact["file_sizes"]["48k"] == len(b"RIFF48k")
    assert artifact["playable_urls"]["16k"] == "/api/voice/tts/tts-debug/16k"
    assert artifact["audio_files"]["16k"]["audio_url"] == f"{public_base_url}/api/tts/audio/tts-debug/16k"
    assert artifact["tts_timing_breakdown_ms"]["piper_generation_ms"] == 4.2


def test_voice_artifact_delete_routes_remove_tts_and_wake_files(tmp_path):
    tts_dir = tmp_path / "voice_tts"
    wake_dir = tmp_path / "wake-recordings"
    tts_dir.mkdir()
    wake_dir.mkdir()
    for suffix in (".json", ".raw.wav", ".48k.wav", ".16k.wav"):
        (tts_dir / f"tts-delete{suffix}").write_bytes(b"delete")
    (wake_dir / "wake-delete.wav").write_bytes(b"wake")
    (wake_dir / "wake-delete.json").write_text('{"recording_id":"wake-delete"}\n', encoding="utf-8")
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                voice_wake_recordings_enabled=True,
                voice_wake_recording_dir=wake_dir,
            )
        )
    )

    tts_response = client.delete("/api/voice/tts/artifacts/tts-delete")
    wake_response = client.delete("/api/voice/wake-recordings/wake-delete")

    assert tts_response.status_code == 200
    assert tts_response.json()["deleted_count"] == 4
    assert wake_response.status_code == 200
    assert wake_response.json()["deleted_count"] == 2
    assert not list(tts_dir.glob("tts-delete*"))
    assert not list(wake_dir.glob("wake-delete*"))


def test_endpoint_voice_artifact_delete_route_removes_history_referenced_files(tmp_path):
    tts_dir = tmp_path / "voice_tts"
    wake_dir = tmp_path / "wake-recordings"
    tts_dir.mkdir()
    wake_dir.mkdir()
    for suffix in (".json", ".raw.wav", ".48k.wav"):
        (tts_dir / f"tts-endpoint{suffix}").write_bytes(b"delete")
    (wake_dir / "wake-endpoint.wav").write_bytes(b"wake")
    (wake_dir / "wake-endpoint.json").write_text('{"recording_id":"wake-endpoint"}\n', encoding="utf-8")
    (tmp_path / "voice_session_history.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-05-09T19:00:00+00:00",
                "sessions": [
                    {
                        "session_id": "voice-session-1",
                        "endpoint_id": "esp-pe-1",
                        "tts": {"stream_id": "tts-endpoint"},
                        "wake_recording": {"recording_id": "wake-endpoint"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                voice_wake_recordings_enabled=True,
                voice_wake_recording_dir=wake_dir,
            )
        )
    )

    response = client.delete("/api/voice/artifacts/endpoints/esp-pe-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_count"] == 1
    assert payload["tts_deleted_count"] == 3
    assert payload["wake_deleted_count"] == 2
    assert not list(tts_dir.glob("tts-endpoint*"))
    assert not list(wake_dir.glob("wake-endpoint*"))


def test_speaker_id_enrollment_captures_list_recent_endpoint_wake_recordings(tmp_path):
    history_path = tmp_path / "voice_session_history.json"
    history_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-08-24T12:00:00+00:00",
                "sessions": [
                    {
                        "session_id": "voice-session-new",
                        "endpoint_id": "esp-pe-1",
                        "completed_at": "2026-08-24T12:00:10+00:00",
                        "transcript": {"text": "my voice is my local key", "provider_id": "test-stt"},
                        "wake_recording": {
                            "recording_id": "wake-new",
                            "recorded_at": "2026-08-24T12:00:09+00:00",
                            "duration_ms": 4200,
                            "audio_url": "/api/voice/wake-recordings/wake-new",
                            "byte_count": 134400,
                            "audio_format": {
                                "encoding": "pcm_s16le",
                                "sample_rate_hz": 16000,
                                "channels": 1,
                            },
                            "wav_path": "/private/wake-new.wav",
                        },
                    },
                    {
                        "session_id": "voice-session-old",
                        "endpoint_id": "esp-pe-1",
                        "completed_at": "2026-08-24T11:59:00+00:00",
                        "wake_recording": {
                            "recording_id": "wake-old",
                            "recorded_at": "2026-08-24T11:59:00+00:00",
                            "audio_url": "/api/voice/wake-recordings/wake-old",
                        },
                    },
                    {
                        "session_id": "voice-session-box",
                        "endpoint_id": "esp-box-1",
                        "completed_at": "2026-08-24T12:00:11+00:00",
                        "wake_recording": {
                            "recording_id": "wake-box",
                            "recorded_at": "2026-08-24T12:00:11+00:00",
                            "audio_url": "/api/voice/wake-recordings/wake-box",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                voice_session_history_path=history_path,
            )
        )
    )

    response = client.get(
        "/api/speaker-id/enrollment-captures",
        params={"endpoint_id": "esp-pe-1", "since": "2026-08-24T12:00:00+00:00"},
    )

    assert response.status_code == 200
    captures = response.json()["captures"]
    assert len(captures) == 1
    capture = captures[0]
    assert capture["recording_id"] == "wake-new"
    assert capture["endpoint_id"] == "esp-pe-1"
    assert capture["sample_rate_hz"] == 16000
    assert capture["transcript"]["text"] == "my voice is my local key"
    assert "wav_path" not in capture


def test_speaker_id_enrollment_capture_window_marks_endpoint_active(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
            )
        )
    )

    response = client.post(
        "/api/speaker-id/enrollment-capture-windows",
        json={"endpoint_id": "esp-pe-1", "ttl_seconds": 120},
    )

    assert response.status_code == 200
    window = response.json()["window"]
    assert window["endpoint_id"] == "esp-pe-1"
    assert window["mode"] == "speaker_id_enrollment"
    assert window["ttl_seconds"] == 120
    assert window["active"] is True
    status = client.get("/api/voice/status").json()
    assert status["speaker_enrollment_capture"]["active_windows"][0]["endpoint_id"] == "esp-pe-1"


def test_voice_placement_test_window_marks_endpoint_active(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
            )
        )
    )

    response = client.post(
        "/api/voice/placement-tests",
        json={
            "endpoint_id": "esp-pe-1",
            "room": "kitchen",
            "zone": "north",
            "position_label": "island",
            "expected_phrase": "Hexe turn on the kitchen lights",
            "expected_speaker_public_id": "speaker_dan",
            "ttl_seconds": 120,
        },
    )

    assert response.status_code == 200
    window = response.json()["window"]
    assert window["endpoint_id"] == "esp-pe-1"
    assert window["mode"] == "active"
    assert window["room"] == "kitchen"
    assert window["raw_audio_policy"] == "discard_after_metrics"
    status = client.get("/api/voice/status").json()
    assert status["placement_tests"]["active_windows"][0]["endpoint_id"] == "esp-pe-1"
    assert status["placement_tests"]["active_windows"][0]["expected_phrase"] == "Hexe turn on the kitchen lights"


def test_voice_passive_placement_calibration_api_schedules_records_and_reports(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
            )
        )
    )

    start = client.post(
        "/api/voice/placement-calibrations",
        json={
            "endpoint_id": "esp-pe-1",
            "room": "kitchen",
            "zone": "north",
            "duration_hours": 24,
            "sample_interval_seconds": 600,
        },
    )

    assert start.status_code == 200
    window = start.json()["window"]
    assert window["mode"] == "passive_ambient"
    assert window["raw_audio_policy"] == "discard_after_metrics"
    assert window["metrics_only"] is True

    sample = client.post(
        f"/api/voice/placement-calibrations/{window['calibration_id']}/samples",
        json={
            "metrics": {
                "ambient_rms": 0.02,
                "peak": 0.12,
                "clipping_ratio": 0.0,
                "speech_like_activity": False,
                "snr_db": 24,
                "audio_b64": "ignored",
            }
        },
    )

    assert sample.status_code == 200
    sample_payload = sample.json()["sample"]
    assert sample_payload["privacy"]["metrics_only"] is True
    assert sample_payload["privacy"]["raw_audio"]["persisted"] is False
    assert sample_payload["privacy"]["stt"]["called"] is False
    assert sample_payload["privacy"]["speaker_id"]["called"] is False
    assert "audio_b64" not in sample_payload["metrics"]

    status = client.get("/api/voice/placement-calibrations", params={"endpoint_id": "esp-pe-1"}).json()
    assert status["calibrations"]["active_windows"][0]["calibration_id"] == window["calibration_id"]
    assert status["calibrations"]["sample_count"] == 1

    report = client.get(f"/api/voice/placement-calibrations/{window['calibration_id']}/report")
    assert report.status_code == 200
    assert report.json()["report"]["sample_count"] == 1
    assert report.json()["report"]["privacy"]["raw_audio_persisted"] is False


def test_voice_passive_placement_calibration_cancel_rejects_samples(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
            )
        )
    )
    window = client.post(
        "/api/voice/placement-calibrations",
        json={"endpoint_id": "esp-pe-1", "room": "kitchen"},
    ).json()["window"]

    cancel = client.post(f"/api/voice/placement-calibrations/{window['calibration_id']}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["window"]["status"] == "cancelled"

    sample = client.post(
        f"/api/voice/placement-calibrations/{window['calibration_id']}/samples",
        json={"metrics": {"ambient_rms": 0.02}},
    )
    assert sample.status_code == 400
    assert sample.json()["detail"] == "calibration_not_active"


def test_voice_privacy_mode_status_blocks_sensitive_voice_features(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                voice_privacy_mode_enabled=True,
                voice_speaker_id_enabled=True,
                voice_wake_recordings_enabled=True,
                voice_micro_vad_chunks_enabled=True,
            )
        )
    )

    status = client.get("/api/voice/status")
    capture = client.post(
        "/api/speaker-id/enrollment-capture-windows",
        json={"endpoint_id": "esp-box-1", "ttl_seconds": 300},
    )
    placement = client.post(
        "/api/voice/placement-tests",
        json={
            "endpoint_id": "esp-box-1",
            "room": "kitchen",
            "expected_phrase": "Hexe turn on the kitchen lights",
        },
    )
    passive_calibration = client.post(
        "/api/voice/placement-calibrations",
        json={"endpoint_id": "esp-box-1", "room": "kitchen"},
    )

    assert status.status_code == 200
    payload = status.json()
    assert payload["privacy_mode"]["enabled"] is True
    assert "speaker_id_lookup" in payload["privacy_mode"]["blocked_features"]
    assert payload["wake_recordings"]["enabled"] is False
    assert payload["turn_pipeline"]["speaker_id"]["enabled"] is False
    assert payload["turn_pipeline"]["speaker_id"]["blocked_reason"] == "privacy_mode_enabled"
    assert payload["speaker_enrollment_capture"]["blocked"] is True
    assert payload["placement_tests"]["blocked"] is True
    assert payload["placement_calibrations"]["blocked"] is True
    assert payload["voice_quality_observations"]["blocked"] is True
    assert payload["voice_quality_observations"]["enabled"] is False
    assert capture.status_code == 400
    assert capture.json()["detail"] == "privacy_mode_enabled"
    assert placement.status_code == 400
    assert placement.json()["detail"] == "privacy_mode_enabled"
    assert passive_calibration.status_code == 400
    assert passive_calibration.json()["detail"] == "privacy_mode_enabled"


def test_tts_warmup_voice_selection_prefers_configured_warm_voices():
    settings = Settings(
        voice_tts_provider="piper",
        voice_tts_piper_voice="en_US-kathleen-low",
        piper_tts_warm_voices="en_US-kathleen-low,en_US-hfc_female-medium",
        voice_tts_endpoint_voices="esp-pe-1=en_US-amy-medium",
    )

    assert _tts_warmup_voices(settings) == ["en_US-kathleen-low", "en_US-hfc_female-medium", "en_US-amy-medium"]


def test_tts_warmup_voice_selection_can_use_discovered_piper_voices():
    settings = Settings(voice_tts_provider="piper", voice_tts_endpoint_voices="esp-pe-1=en_US-hfc_female-medium")

    assert _tts_warmup_voices(
        settings,
        discovered_warm_voices=["en_US-kathleen-low", "en_US-hfc_female-medium"],
    ) == ["en_US-kathleen-low", "en_US-hfc_female-medium"]


def test_daily_orphan_cleanup_schedule_targets_next_midnight():
    seconds = _seconds_until_next_local_midnight(datetime(2026, 5, 7, 23, 30, 0).astimezone())

    assert seconds == 1800


def test_voice_status_reports_tts_warmup_background_task(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                voice_tts_provider="deterministic",
            )
        )
    )

    response = client.get("/api/voice/status")

    assert response.status_code == 200
    warmup = response.json()["voice_tts_warmup"]
    assert warmup["name"] == "every_10_minutes"
    assert warmup["interval_seconds"] == 600
    assert warmup["enabled"] is False
    assert warmup["text"] == "hello"
    orphan_cleanup = response.json()["voice_orphan_cleanup"]
    assert orphan_cleanup["name"] == "daily_midnight"
    assert orphan_cleanup["scheduled_time_local"] == "00:00"
    assert orphan_cleanup["min_age_seconds"] == 600


def test_voice_artifact_cleanup_includes_wake_recordings():
    class FakeTtsAudioService:
        def __init__(self):
            self.called = False

        def cleanup_expired(self):
            self.called = True

    class FakeWakeRecorder:
        def __init__(self):
            self.called = False

        def cleanup_expired(self):
            self.called = True
            return {"deleted_count": 2}

    tts = FakeTtsAudioService()
    wake = FakeWakeRecorder()

    result = cleanup_voice_artifacts_once(tts_audio_service=tts, wake_recorder=wake)

    assert tts.called is True
    assert wake.called is True
    assert result["tts"]["expired_cleanup"] == "completed"
    assert result["wake_recordings"]["deleted_count"] == 2


def test_tts_settings_list_models_and_save_runtime_config(tmp_path):
    model_dir = tmp_path / "piper-tts" / "models"
    model_dir.mkdir(parents=True)
    (model_dir / "en_US-jenny-high.onnx").write_bytes(b"model")
    (model_dir / "en_US-jenny-high.onnx.json").write_text(
        json.dumps(
            {
                "dataset": "jenny_dioco",
                "audio": {"sample_rate": 22050, "quality": "high"},
                "language": {"code": "en_US"},
            }
        ),
        encoding="utf-8",
    )
    piper_env_path = tmp_path / "piper-tts.env"
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                voice_tts_provider="piper",
                piper_tts_model_dir=model_dir,
                piper_tts_warm_voices="en_US-jenny-high",
                voice_tts_conversion_sample_rates="48000,16000",
                piper_tts_env_path=piper_env_path,
            )
        )
    )

    response = client.get("/api/tts/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "piper"
    assert payload["warm_voices"] == ["en_US-jenny-high"]
    assert payload["conversion_sample_rates_hz"] == [48000, 16000]
    assert payload["allowed_conversion_sample_rates_hz"] == [48000, 22050, 16000]
    assert payload["conversion_policy"] == "blocking_all"
    assert payload["allowed_conversion_policies"] == ["blocking_all", "endpoint_required_sync"]
    assert payload["default_voice"] is None
    assert payload["models"][0]["model_id"] == "en_US-jenny-high"
    assert payload["models"][0]["display_name"] == "Jenny Dioco"
    assert payload["models"][0]["raw_sample_rate_hz"] == 22050
    assert payload["models"][0]["quality"] == "high"

    update = client.put(
        "/api/tts/settings",
        json={
            "warm_voices": ["en_US-jenny-high", "missing"],
            "default_voice": "en_US-jenny-high",
            "conversion_sample_rates_hz": [48000, 22050],
            "conversion_policy": "endpoint_required_sync",
        },
    )

    assert update.status_code == 200
    updated = update.json()
    assert updated["default_voice"] == "en_US-jenny-high"
    assert updated["warm_voices"] == ["en_US-jenny-high"]
    assert updated["conversion_sample_rates_hz"] == [48000, 22050]
    assert updated["conversion_policy"] == "endpoint_required_sync"
    assert updated["restart_required"] is True
    runtime_config = json.loads((tmp_path / "voice_tts_settings.json").read_text(encoding="utf-8"))
    assert runtime_config["default_voice"] == "en_US-jenny-high"
    assert runtime_config["warm_voices"] == ["en_US-jenny-high"]
    assert runtime_config["conversion_sample_rates_hz"] == [48000, 22050]
    assert runtime_config["conversion_policy"] == "endpoint_required_sync"
    assert "PIPER_TTS_MODEL_PATH=/models/en_US-jenny-high.onnx" in piper_env_path.read_text(encoding="utf-8")
    assert "PIPER_TTS_WARM_VOICES=en_US-jenny-high" in piper_env_path.read_text(encoding="utf-8")


def test_tts_restart_clears_restart_required_flag(tmp_path, monkeypatch):
    runtime_config_path = tmp_path / "voice_tts_settings.json"
    runtime_config_path.write_text(
        json.dumps(
            {
                "warm_voices": ["en_US-jenny-high"],
                "conversion_sample_rates_hz": [48000, 16000],
                "restart_required": True,
            }
        ),
        encoding="utf-8",
    )
    control_script = tmp_path / "piper-tts-control.sh"
    control_script.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    control_script.chmod(0o755)
    fake_docker = tmp_path / "docker"
    fake_docker.write_text("#!/usr/bin/env sh\nexit 1\n", encoding="utf-8")
    fake_docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                voice_tts_provider="piper",
                piper_tts_control_script=control_script,
            )
        )
    )

    before = client.get("/api/tts/settings")
    restart = client.post("/api/services/restart", json={"target": "tts"})
    after = client.get("/api/tts/settings")

    assert before.json()["restart_required"] is True
    assert restart.status_code == 200
    assert restart.json()["accepted"] is True
    assert after.json()["restart_required"] is False
    runtime_config = json.loads(runtime_config_path.read_text(encoding="utf-8"))
    assert runtime_config["restart_required"] is False
    assert runtime_config["restart_applied_at"]


def test_stt_install_service_action_is_exposed_for_supervisor(tmp_path):
    calls_path = tmp_path / "stt-control-calls.txt"
    control_script = tmp_path / "faster-whisper-stt-control.sh"
    control_script.write_text(
        "#!/usr/bin/env sh\n"
        f"echo \"$1\" >> {calls_path}\n"
        "if [ \"$1\" = status ]; then echo active; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    control_script.chmod(0o755)
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                voice_stt_provider="external_faster_whisper",
                voice_stt_control_script=control_script,
            )
        )
    )

    install = client.post("/api/services/install", json={"target": "stt"})

    assert install.status_code == 200
    assert install.json()["accepted"] is True
    assert install.json()["target"] == "faster_whisper_stt"
    assert calls_path.read_text(encoding="utf-8").splitlines() == ["install", "status"]


def test_speaker_id_install_service_action_is_exposed_for_supervisor(tmp_path):
    calls_path = tmp_path / "speaker-id-control-calls.txt"
    control_script = tmp_path / "speaker-id-control.sh"
    control_script.write_text(
        "#!/usr/bin/env sh\n"
        f"echo \"$1\" >> {calls_path}\n"
        "if [ \"$1\" = status ]; then echo active; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    control_script.chmod(0o755)
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                voice_speaker_id_enabled=True,
                voice_speaker_id_control_script=control_script,
            )
        )
    )

    install = client.post("/api/services/install", json={"target": "speaker_id"})

    assert install.status_code == 200
    assert install.json()["accepted"] is True
    assert install.json()["target"] == "speaker_id"
    assert calls_path.read_text(encoding="utf-8").splitlines() == ["install", "status"]


def test_core_normalized_piper_voice_ids_resolve_to_installed_model(tmp_path):
    model_dir = tmp_path / "piper-tts" / "models"
    model_dir.mkdir(parents=True)
    (model_dir / "en_US-lessac-medium.onnx").write_bytes(b"model")

    assert resolve_piper_voice_model_id("en_us-lessac-medium", model_dir) == "en_US-lessac-medium"


def test_assistant_turn_handles_timer_intent_locally(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", node_name="kitchen-voice")))

    response = client.post(
        "/api/assistant/turn",
        json={"endpoint_id": "box-1", "session_id": "session-abc", "text": "Hexa, create a timer for 5 minutes"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "session-abc"
    assert payload["heard_text"] == "create a timer for 5 minutes"
    assert payload["command"] == "timer.create"
    assert payload["handled_locally"] is True
    assert payload["reply_text"] == "Setting timer for 5 minutes."
    assert payload["spoken_text"] == "Setting timer for 5 minutes."
    assert payload["provider_id"] == "registered_intent"
    assert payload["intent_latency_ms"] >= 0


def test_assistant_turn_fallback_reply_uses_session_id_if_provided():
    client = TestClient(create_app(Settings(node_name="lab-voice")))

    response = client.post(
        "/api/assistant/turn",
        json={
            "endpoint_id": "box-9",
            "session_id": "session-abc",
            "text": "turn on the lights",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "session-abc"
    assert payload["handled_locally"] is False
    assert payload["reply_text"] == "I heard turn on the lights"
    assert payload["provider_id"] == "local_echo"


def test_assistant_turn_can_route_to_configured_ai_node():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read()
        return httpx.Response(
            200,
            json={
                "endpoint_id": "box-9",
                "session_id": "session-abc",
                "heard_text": "turn on the lights",
                "reply_text": "AI Node heard turn on the lights.",
                "spoken_text": "AI Node heard turn on the lights.",
                "handled_locally": False,
                "command": None,
                "device_state": "speaking",
                "model": "assistant-model-a",
                "provider_metadata": {"model_provider": "test-provider"},
            },
        )

    adapter = AiNodeAssistantAdapter(
        base_url="https://ai-node.test",
        turn_path="/api/assistant/turn",
        timeout_s=5,
        fallback=LocalEchoAssistantAdapter(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    response = adapter.handle_turn(
        AssistantTurnRequest(endpoint_id="box-9", session_id="session-abc", text="turn on the lights"),
        session_id="session-abc",
    )

    assert captured["url"] == "https://ai-node.test/api/assistant/turn"
    request_json = json.loads(captured["json"])
    assert request_json["text"] == "turn on the lights"
    assert request_json["contract_version"] == "voice.ai_node.turn.v1"
    assert request_json["source_node_type"] == "voice-node"
    assert response.reply_text == "AI Node heard turn on the lights."
    assert response.heard_text == "turn on the lights"
    assert response.provider_id == "ai_node"
    assert response.model == "assistant-model-a"
    assert response.provider_latency_ms is not None
    assert response.provider_metadata["model_provider"] == "test-provider"
    assert response.provider_metadata["ai_node"]["contract_version"] == "voice.ai_node.turn.v1"
    assert adapter.status()["healthy"] is True
    assert adapter.status()["last_latency_ms"] is not None


def test_assistant_ai_node_adapter_falls_back_to_local_echo_when_unconfigured():
    adapter = AiNodeAssistantAdapter(
        base_url=None,
        turn_path="/api/assistant/turn",
        timeout_s=5,
        fallback=LocalEchoAssistantAdapter(),
    )

    response = adapter.handle_turn(
        AssistantTurnRequest(endpoint_id="box-9", session_id="session-abc", text="turn on the lights"),
        session_id="session-abc",
    )

    assert response.reply_text == "I heard turn on the lights"
    assert response.fallback_used is True
    assert response.fallback_reason == "missing_ai_node_base_url"
    assert response.error == "missing_ai_node_base_url"
    assert adapter.status()["healthy"] is False
    assert adapter.status()["last_error"] == "missing_ai_node_base_url"
    assert adapter.status()["last_error_code"] == "missing_ai_node_base_url"


def test_assistant_ai_node_adapter_falls_back_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    adapter = AiNodeAssistantAdapter(
        base_url="https://ai-node.test",
        turn_path="/api/assistant/turn",
        timeout_s=0.01,
        fallback=LocalEchoAssistantAdapter(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    response = adapter.handle_turn(
        AssistantTurnRequest(endpoint_id="box-9", session_id="session-abc", text="turn on the lights"),
        session_id="session-abc",
    )

    assert response.reply_text == "I heard turn on the lights"
    assert response.provider_id == "local_echo"
    assert response.fallback_used is True
    assert response.fallback_reason == "ai_node_timeout"
    assert response.provider_metadata["primary_provider"] == "ai_node"
    assert response.provider_metadata["fallback_provider"] == "local_echo"
    assert adapter.status()["healthy"] is False
    assert adapter.status()["last_error_code"] == "ai_node_timeout"


def test_assistant_turn_service_keeps_rolling_context(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_conversation_context_turns=2)
    service = AssistantTurnService(settings=settings, runtime_service=NodeRuntimeService(settings=settings))

    service.handle_turn(AssistantTurnRequest(endpoint_id="box-9", session_id="session-1", text="first"))
    service.handle_turn(AssistantTurnRequest(endpoint_id="box-9", session_id="session-1", text="second"))
    service.handle_turn(AssistantTurnRequest(endpoint_id="box-9", session_id="session-1", text="third"))

    endpoint_context = service.context_for_endpoint("box-9")
    session_context = service.context_for_session("session-1")

    assert [turn.heard_text for turn in endpoint_context] == ["second", "third"]
    assert [turn.reply_text for turn in session_context] == [
        "I heard second",
        "I heard third",
    ]
    assert service.status()["context_turn_limit"] == 2
    assert service.status()["endpoint_contexts"]["box-9"] == 2


def test_assistant_ai_node_adapter_receives_context():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        return httpx.Response(200, json={"reply_text": "ok"})

    adapter = AiNodeAssistantAdapter(
        base_url="https://ai-node.test",
        turn_path="/api/assistant/turn",
        timeout_s=5,
        fallback=LocalEchoAssistantAdapter(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    adapter.handle_turn(
        AssistantTurnRequest(endpoint_id="box-9", session_id="session-2", text="second"),
        session_id="session-2",
        context=[
            ConversationTurn(
                endpoint_id="box-9",
                session_id="session-1",
                heard_text="first",
                reply_text="I heard first",
            )
        ],
    )

    body = captured["json"].replace(b" ", b"")
    assert b'"context":[{' in body
    assert b'"heard_text":"first"' in body


def test_status_endpoint_reads_persisted_onboarding_state(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "onboarding_session": {
                    "session_id": "session-123",
                    "session_state": "approved",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "resume": {
                    "current_step_id": "provider_setup",
                },
            }
        )
    )

    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    response = client.get("/api/node/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["node_id"] == "node-voice-123"
    assert payload["trust_state"] == "trusted"
    assert payload["current_step_id"] == "provider_setup"
    assert payload["lifecycle_state"] == "capability_setup_pending"
    assert payload["capability_status"] == "missing"


def test_local_setup_endpoints_persist_node_identity_and_core_connection(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    identity_response = client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "1.0",
            "node_nonce": "voice-node-nonce",
            "hostname": "kitchen-voice.local",
            "api_base_url": "http://10.0.0.22:9000",
        },
    )
    assert identity_response.status_code == 200
    assert identity_response.json()["configured"] is True

    connection_response = client.put(
        "/api/onboarding/local-setup/core-connection",
        json={"core_base_url": "http://10.0.0.100:9001"},
    )
    assert connection_response.status_code == 200
    assert connection_response.json()["configured"] is True

    setup_state = client.get("/api/onboarding/local-setup")
    assert setup_state.status_code == 200
    assert setup_state.json()["node_identity"]["node_name"] == "kitchen-voice"
    assert setup_state.json()["core_connection"]["core_base_url"] == "http://10.0.0.100:9001/"

    status_response = client.get("/api/node/status")
    assert status_response.status_code == 200
    assert status_response.json()["node_name"] == "kitchen-voice"
    assert status_response.json()["current_step_id"] == "core_connection"
    assert status_response.json()["lifecycle_state"] == "bootstrap_connecting"


def test_restart_setup_clears_onboarding_state(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "1.0",
            "node_nonce": "voice-node-nonce",
        },
    )
    client.put(
        "/api/onboarding/local-setup/core-connection",
        json={"core_base_url": "http://10.0.0.100:9001"},
    )

    restart_response = client.post("/api/onboarding/restart")
    assert restart_response.status_code == 200
    assert restart_response.json()["node_identity"]["configured"] is False
    assert restart_response.json()["core_connection"]["configured"] is False

    status_response = client.get("/api/node/status")
    assert status_response.status_code == 200
    assert status_response.json()["current_step_id"] == "node_identity"
    assert status_response.json()["lifecycle_state"] == "unconfigured"


def test_bootstrap_discovery_advertisement_validation_advances_to_registration(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "1.0",
            "node_nonce": "voice-node-nonce",
        },
    )
    client.put(
        "/api/onboarding/local-setup/core-connection",
        json={"core_base_url": "http://10.0.0.100:9001"},
    )

    response = client.put(
        "/api/onboarding/bootstrap-discovery/advertisement",
        json={
            "topic": "hexe/bootstrap/core",
            "api_base": "http://10.0.0.100:9001",
            "mqtt_host": "10.0.0.100",
            "mqtt_port": 1884,
            "onboarding_mode": "api",
            "onboarding_contract": "global-node-v1",
            "onboarding_endpoints": {
                "register_session": "/api/system/nodes/onboarding/sessions",
                "registrations": "/api/system/nodes/registrations",
                "register": "/api/system/nodes/onboarding/sessions",
                "ai_node_register": "/api/system/ai-nodes/onboarding/sessions",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["advertisement_valid"] is True
    assert payload["onboarding_mode"] == "api"
    assert payload["onboarding_contract"] == "global-node-v1"

    status_response = client.get("/api/node/status")
    assert status_response.status_code == 200
    assert status_response.json()["current_step_id"] == "registration"
    assert status_response.json()["lifecycle_state"] == "registration_pending"


def test_onboarding_session_start_persists_core_session_metadata(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "1.0",
            "node_nonce": "voice-node-nonce",
            "hostname": "kitchen-voice.local",
            "api_base_url": "http://10.0.0.22:9000",
        },
    )
    client.put("/api/onboarding/local-setup/core-connection", json={"core_base_url": "http://10.0.0.100:9001"})
    client.put(
        "/api/onboarding/bootstrap-discovery/advertisement",
        json={
            "topic": "hexe/bootstrap/core",
            "api_base": "http://10.0.0.100:9001",
            "mqtt_host": "10.0.0.100",
            "mqtt_port": 1884,
            "onboarding_mode": "api",
            "onboarding_contract": "global-node-v1",
            "onboarding_endpoints": {
                "register_session": "/api/system/nodes/onboarding/sessions",
                "registrations": "/api/system/nodes/registrations",
            },
        },
    )

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "node_name": "kitchen-voice",
                "node_type": "voice-node",
                "node_software_version": "0.1.0",
                "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123&state=abc",
                "session_id": "session-123",
                "expires_at": "2026-04-08T01:00:00+00:00",
                "finalize": "/api/system/nodes/onboarding/sessions/session-123/finalize?node_nonce=voice-node-nonce",
            }

    def fake_post(url, json, timeout):
        assert url == "http://10.0.0.100:9001/api/system/nodes/onboarding/sessions"
        assert json["node_name"] == "kitchen-voice"
        assert json["protocol_version"] == "1.0"
        assert json["node_nonce"] == "voice-node-nonce"
        return DummyResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    response = client.post("/api/onboarding/session/start")
    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "session-123"
    assert payload["approval_url"].startswith("http://10.0.0.100/onboarding/nodes/approve")

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["session_id"] == "session-123"
    assert onboarding_status.json()["approval_url"].startswith("http://10.0.0.100/onboarding/nodes/approve")
    assert onboarding_status.json()["session_state"] == "pending"

    node_status = client.get("/api/node/status")
    assert node_status.status_code == 200
    assert node_status.json()["current_step_id"] == "approval"
    assert node_status.json()["lifecycle_state"] == "pending_approval"


def test_onboarding_session_poll_surfaces_approved_outcome(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "1.0",
            "node_nonce": "voice-node-nonce",
            "hostname": "kitchen-voice.local",
            "api_base_url": "http://10.0.0.22:9000",
        },
    )
    client.put("/api/onboarding/local-setup/core-connection", json={"core_base_url": "http://10.0.0.100:9001"})
    client.put(
        "/api/onboarding/bootstrap-discovery/advertisement",
        json={
            "topic": "hexe/bootstrap/core",
            "api_base": "http://10.0.0.100:9001",
            "mqtt_host": "10.0.0.100",
            "mqtt_port": 1884,
            "onboarding_mode": "api",
            "onboarding_contract": "global-node-v1",
            "onboarding_endpoints": {
                "register_session": "/api/system/nodes/onboarding/sessions",
                "registrations": "/api/system/nodes/registrations",
            },
        },
    )

    class SessionStartResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "node_name": "kitchen-voice",
                "node_type": "voice-node",
                "node_software_version": "0.1.0",
                "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123&state=abc",
                "session_id": "session-123",
                "expires_at": "2026-04-08T01:00:00+00:00",
                "finalize": "/api/system/nodes/onboarding/sessions/session-123/finalize?node_nonce=voice-node-nonce",
            }

    class SessionPollResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "approved",
                "activation": {
                    "node_id": "node-voice-123",
                    "paired_core_id": "core-main",
                    "node_trust_token": "trust-token-123",
                    "baseline_policy_version": "2026.04",
                    "operational_mqtt_identity": "node-voice-123",
                    "operational_mqtt_host": "10.0.0.100",
                    "operational_mqtt_port": 1883,
                    "trust_status": "trusted",
                },
            }

    def fake_post(url, json, timeout):
        return SessionStartResponse()

    def fake_get(url, params, timeout):
        assert url == "http://10.0.0.100:9001/api/system/nodes/onboarding/sessions/session-123/finalize"
        assert params == {"node_nonce": "voice-node-nonce"}
        return SessionPollResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)

    start_response = client.post("/api/onboarding/session/start")
    assert start_response.status_code == 200

    poll_response = client.post("/api/onboarding/session/poll")
    assert poll_response.status_code == 200
    assert poll_response.json()["session_state"] == "approved"
    assert poll_response.json()["activation_received"] is True

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["session_state"] == "approved"
    assert onboarding_status.json()["last_terminal_outcome"] == "approved"
    assert onboarding_status.json()["current_step_id"] == "trust_activation"


def test_trust_activation_finalize_persists_trusted_state(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "onboarding_session": {
                    "session_id": "session-123",
                    "session_state": "approved",
                    "pending_activation": {
                        "node_id": "node-voice-123",
                        "node_type": "voice-node",
                        "paired_core_id": "core-main",
                        "node_trust_token": "trust-token-123",
                        "initial_baseline_policy": {"version": "2026.04"},
                        "baseline_policy_version": "2026.04",
                        "activation_profile": {"voice": {"default_provider": "mock"}},
                        "operational_mqtt_identity": "node-voice-123",
                        "operational_mqtt_token": "mqtt-token-123",
                        "operational_mqtt_host": "10.0.0.100",
                        "operational_mqtt_port": 1883,
                        "issued_at": "2026-04-08T01:00:00+00:00",
                        "source_session_id": "session-123",
                        "trust_status": "trusted",
                    },
                },
                "resume": {
                    "current_step_id": "trust_activation",
                    "last_completed_step_id": "approval",
                },
            }
        )
    )

    response = client.post("/api/onboarding/trust-activation/finalize")
    assert response.status_code == 200
    payload = response.json()
    assert payload["node_id"] == "node-voice-123"
    assert payload["trust_state"] == "trusted"
    assert payload["operational_mqtt_host"] == "10.0.0.100"

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["current_step_id"] == "provider_setup"
    assert onboarding_status.json()["trust_state"] == "trusted"

    node_status = client.get("/api/node/status")
    assert node_status.status_code == 200
    assert node_status.json()["node_id"] == "node-voice-123"
    assert node_status.json()["trust_state"] == "trusted"
    assert node_status.json()["current_step_id"] == "provider_setup"


def test_trust_status_refresh_surfaces_removed_state_and_reonboarding(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "node_trust_token": "trust-token-123",
                    "trust_status": "trusted",
                    "operational_mqtt_token": "mqtt-token-123",
                },
                "resume": {
                    "current_step_id": "ready",
                    "last_completed_step_id": "governance_sync",
                },
            }
        )
    )

    class TrustStatusResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "node_id": "node-voice-123",
                "trust_status": "revoked",
                "supported": False,
                "support_state": "removed",
                "registry_present": False,
                "registry_state": None,
                "revoked_at": "2026-04-08T02:00:00+00:00",
                "revocation_reason": "node_removed_by_admin",
                "revocation_action": "remove",
                "message": "This node was removed by Core and is no longer trusted.",
            }

    def fake_get(url, headers, timeout):
        assert url == "http://10.0.0.100:9001/api/system/nodes/trust-status/node-voice-123"
        assert headers == {"X-Node-Trust-Token": "trust-token-123"}
        return TrustStatusResponse()

    monkeypatch.setattr(httpx, "get", fake_get)

    response = client.post("/api/onboarding/trust-status/refresh")
    assert response.status_code == 200
    assert response.json()["support_state"] == "removed"
    assert response.json()["trust_state"] == "revoked"

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["current_step_id"] == "registration"
    assert onboarding_status.json()["trust_state"] == "revoked"
    assert onboarding_status.json()["support_state"] == "removed"
    assert "no longer trusted" in onboarding_status.json()["trust_message"]

    node_status = client.get("/api/node/status")
    assert node_status.status_code == 200
    assert node_status.json()["current_step_id"] == "registration"
    assert node_status.json()["blocking_reasons"] == ["node_removed_by_core", "re_onboarding_required"]


def test_registration_metadata_refresh_patches_core_node_with_full_metadata(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "core_base_url": "http://10.0.0.100:9001",
                    "hostname": "voice-node.local",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
            }
        )
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=state_path,
                core_admin_token="core-admin-token",
                public_api_base_url="http://10.0.0.22:9004/",
                public_ui_base_url="http://10.0.0.22:8082",
            )
        )
    )

    captured = {}

    class MetadataRefreshResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "registration": {"node_id": "node-voice-123"}}

    def fake_put(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return MetadataRefreshResponse()

    monkeypatch.setattr(httpx, "put", fake_put)

    response = client.post("/api/onboarding/registration-metadata/refresh")

    assert response.status_code == 200
    assert captured["url"] == "http://10.0.0.100:9001/api/system/nodes/registrations/node-voice-123/metadata"
    assert captured["headers"] == {"X-Admin-Token": "core-admin-token"}
    assert captured["json"] == {
        "metadata_schema_version": "1.0",
        "hostname": "voice-node.local",
        "ui_endpoint": "http://10.0.0.22:8082",
        "api_base_url": "http://10.0.0.22:9004",
        "ui_enabled": False,
        "ui_base_url": None,
        "ui_mode": "spa",
        "ui_health_endpoint": None,
    }
    payload = response.json()
    assert payload["ok"] is True
    assert payload["node_id"] == "node-voice-123"
    assert payload["registration"] == {"node_id": "node-voice-123"}


def test_provider_setup_enables_provider_and_advances_to_capability_declaration(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "resume": {
                    "current_step_id": "provider_setup",
                    "last_completed_step_id": "trust_activation",
                },
            }
        )
    )

    response = client.put(
        "/api/providers/setup",
        json={
            "enabled_providers": ["voice"],
            "default_provider": "voice",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["declaration_allowed"] is True
    assert payload["enabled_providers"] == ["voice"]

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["current_step_id"] == "capability_declaration"
    assert onboarding_status.json()["capability_setup"]["provider_selection"]["enabled"] == ["voice"]
    assert (
        onboarding_status.json()["capability_setup"]["task_capability_selection"]["available"]
        == VOICE_NODE_CAPABILITIES
    )
    assert onboarding_status.json()["capability_setup"]["declaration_allowed"] is True

    capability_status = client.get("/api/capabilities")
    assert capability_status.status_code == 200
    assert capability_status.json()["configured"] == ["voice"]
    assert capability_status.json()["available"] == VOICE_NODE_CAPABILITIES
    assert capability_status.json()["selected"] == VOICE_NODE_CAPABILITIES

    selection = client.put(
        "/api/capabilities/selection",
        json={"selected_capabilities": ["voice.inference", "voice.tts.audio_url"]},
    )
    assert selection.status_code == 200
    assert selection.json()["selected"] == ["voice.inference", "voice.tts.audio_url"]
    assert selection.json()["capability_status"] == "selection_pending"


def test_node_ui_provider_setup_updates_one_provider(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=state_path,
                runtime_dir=tmp_path,
                piper_tts_env_path=tmp_path / "piper-tts.env",
            )
        )
    )
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "provider_setup": {
                    "supported_providers": ["voice", "piper", "external_faster_whisper"],
                    "enabled_providers": ["voice"],
                    "default_provider": "voice",
                },
                "resume": {
                    "current_step_id": "provider_setup",
                    "last_completed_step_id": "trust_activation",
                },
            }
        )
    )

    response = client.put(
        "/api/node/ui/providers/piper/setup",
        json={
            "enabled": True,
            "default": True,
            "model": "en_US-jenny-high",
            "warm_models": ["en_US-jenny-high"],
            "default_voice": "en_US-jenny-high",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled_providers"] == ["voice", "piper"]
    assert payload["default_provider"] == "piper"
    assert payload["provider_configs"]["piper"]["default_voice"] == "en_US-jenny-high"
    assert payload["provider_configs"]["piper"]["warm_models"] == ["en_US-jenny-high"]

    response = client.put("/api/node/ui/providers/voice/setup", json={"enabled": False, "default": False})

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled_providers"] == ["piper"]
    assert payload["default_provider"] == "piper"


def test_node_ui_piper_provider_setup_applies_runtime_config(tmp_path, monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def put(self, url, *, json):
            calls.append({"url": url, "json": json, "timeout": self.timeout})

            class Response:
                def raise_for_status(self):
                    return None

            return Response()

    monkeypatch.setattr("hexevoice.main.httpx.AsyncClient", FakeAsyncClient)
    state_path = tmp_path / "onboarding-state.json"
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "en_US-jenny-high.onnx").write_bytes(b"model")
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=state_path,
                runtime_dir=tmp_path,
                voice_tts_provider="piper",
                voice_tts_piper_base_url="http://tts.test:10200",
                voice_tts_timeout_s=9.0,
                piper_tts_model_dir=model_dir,
                piper_tts_env_path=tmp_path / "piper-tts.env",
            )
        )
    )
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "provider_setup": {
                    "supported_providers": ["voice", "piper"],
                    "enabled_providers": ["voice"],
                    "default_provider": "voice",
                },
            }
        )
    )

    response = client.put(
        "/api/node/ui/providers/piper/setup",
        json={
            "enabled": True,
            "default": True,
            "default_voice": "en_US-jenny-high",
            "warm_models": ["en_US-jenny-high"],
        },
    )

    assert response.status_code == 200
    assert calls == [
        {
            "url": "http://tts.test:10200/config",
            "json": {"default_voice": "en_US-jenny-high", "warm_voices": ["en_US-jenny-high"]},
            "timeout": 9.0,
        }
    ]
    settings = client.get("/api/tts/settings").json()
    assert settings["default_voice"] == "en_US-jenny-high"
    assert settings["warm_voices"] == ["en_US-jenny-high"]
    assert settings["restart_required"] is False


def test_node_ui_stt_provider_status_prefers_saved_model(tmp_path, monkeypatch):
    class FailingAsyncClient:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def put(self, url, *, json):
            raise httpx.ConnectError("stt unavailable")

    monkeypatch.setattr("hexevoice.main.httpx.AsyncClient", FailingAsyncClient)
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=state_path,
                voice_stt_provider="external_faster_whisper",
                voice_stt_faster_whisper_model="base.en",
                voice_stt_service_base_url="http://127.0.0.1:9",
            )
        )
    )
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "provider_setup": {
                    "supported_providers": ["voice", "external_faster_whisper"],
                    "enabled_providers": ["voice"],
                    "default_provider": "voice",
                },
                "resume": {
                    "current_step_id": "provider_setup",
                    "last_completed_step_id": "trust_activation",
                },
            }
        )
    )

    response = client.put(
        "/api/node/ui/providers/external_faster_whisper/setup",
        json={
            "enabled": True,
            "default": False,
            "model": "small.en",
            "device": "cuda",
            "compute_type": "float16",
            "warm_model": True,
            "warm_models": ["tiny.en"],
        },
    )
    assert response.status_code == 200
    saved_config = response.json()["provider_configs"]["external_faster_whisper"]
    assert saved_config["model"] == "small.en"
    assert saved_config["device"] == "cuda"
    assert saved_config["compute_type"] == "float16"
    assert saved_config["warm_models"] == ["tiny.en"]

    status = client.get("/api/node/ui/providers/status")
    assert status.status_code == 200
    stt_provider = next(provider for provider in status.json()["providers"] if provider["id"] == "stt")
    facts = {fact["id"]: fact["value"] for fact in stt_provider["facts"]}
    assert facts["model"] == "small.en"
    assert facts["active_model"] == "base.en"
    assert facts["restart_required"] == "yes"


def test_node_ui_stt_provider_setup_applies_external_runtime_config(tmp_path, monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def put(self, url, *, json):
            calls.append({"url": url, "json": json, "timeout": self.timeout})

            class Response:
                def raise_for_status(self):
                    return None

            return Response()

    monkeypatch.setattr("hexevoice.main.httpx.AsyncClient", FakeAsyncClient)
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=state_path,
                voice_stt_provider="external_faster_whisper",
                voice_stt_service_base_url="http://stt.test:10300",
                voice_stt_timeout_s=12.0,
            )
        )
    )
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "provider_setup": {
                    "supported_providers": ["voice", "external_faster_whisper"],
                    "enabled_providers": ["voice"],
                    "default_provider": "voice",
                },
            }
        )
    )

    response = client.put(
        "/api/node/ui/providers/external_faster_whisper/setup",
        json={
            "enabled": True,
            "default": True,
            "model": "small.en",
            "warm_model": True,
            "warm_models": ["tiny.en"],
            "device": "cpu",
            "compute_type": "int8",
        },
    )

    assert response.status_code == 200
    assert calls == [
        {
            "url": "http://stt.test:10300/config",
            "json": {"model": "tiny.en", "device": "cpu", "compute_type": "int8", "warm_model": True},
            "timeout": 12.0,
        },
        {
            "url": "http://stt.test:10300/config",
            "json": {"model": "small.en", "device": "cpu", "compute_type": "int8", "warm_model": True},
            "timeout": 12.0,
        }
    ]


def test_capability_declaration_governance_and_operational_status_flow(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "node_type": "voice-node",
                    "node_trust_token": "trust-token-123",
                    "trust_status": "trusted",
                },
                "provider_setup": {
                    "supported_providers": ["voice"],
                    "enabled_providers": ["voice"],
                    "default_provider": "voice",
                    "declaration_allowed": True,
                    "blocking_reasons": [],
                },
                "resume": {
                    "current_step_id": "capability_declaration",
                    "last_completed_step_id": "provider_setup",
                },
            }
        )
    )

    class CapabilityResponse:
        status_code = 200
        def raise_for_status(self): return None
        def json(self):
            return {
                "acceptance_status": "accepted",
                "node_id": "node-voice-123",
                "manifest_version": "1.0",
                "accepted_at": "2026-04-08T03:00:00+00:00",
                "declared_capabilities": VOICE_NODE_CAPABILITIES,
                "enabled_providers": ["voice"],
                "capability_profile_id": "profile-123",
                "governance_version": "gov-2026.04",
                "governance_issued_at": "2026-04-08T03:00:05+00:00",
            }

    class GovernanceCurrentResponse:
        status_code = 200
        def raise_for_status(self): return None
        def json(self):
            return {
                "node_id": "node-voice-123",
                "capability_profile_id": "profile-123",
                "governance_version": "gov-2026.04",
                "issued_timestamp": "2026-04-08T03:00:05+00:00",
                "refresh_interval_s": 3600,
                "governance_bundle": {"telemetry_requirements": {"interval_s": 60}},
            }

    class GovernanceRefreshResponseObj:
        status_code = 200
        def raise_for_status(self): return None
        def json(self):
            return {
                "updated": False,
                "governance_version": "gov-2026.04",
                "refresh_interval_s": 3600,
            }

    class OperationalStatusResponseObj:
        status_code = 200
        def raise_for_status(self): return None
        def json(self):
            return {
                "node_id": "node-voice-123",
                "lifecycle_state": "operational",
                "trust_status": "trusted",
                "capability_status": "accepted",
                "governance_status": "issued",
                "operational_ready": True,
                "active_governance_version": "gov-2026.04",
                "last_governance_issued_at": "2026-04-08T03:00:05+00:00",
                "last_governance_refresh_request_at": "2026-04-08T03:10:00+00:00",
                "governance_freshness_state": "fresh",
                "governance_freshness_changed_at": "2026-04-08T03:10:00+00:00",
                "governance_stale_for_s": 0,
                "governance_outdated": False,
                "last_telemetry_timestamp": "2026-04-08T03:11:00+00:00",
                "updated_at": "2026-04-08T03:11:00+00:00",
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        if url.endswith("/api/system/nodes/capabilities/declaration"):
            return CapabilityResponse()
        if url.endswith("/api/system/nodes/budgets/declaration"):
            return CapabilityResponse()
        if url.endswith("/api/system/nodes/governance/refresh"):
            return GovernanceRefreshResponseObj()
        raise AssertionError(url)

    def fake_get(url, headers=None, params=None, timeout=None):
        if url.endswith("/api/system/nodes/governance/current"):
            return GovernanceCurrentResponse()
        if url.endswith("/api/system/nodes/operational-status/node-voice-123"):
            return OperationalStatusResponseObj()
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)

    declaration = client.post("/api/capabilities/declaration")
    assert declaration.status_code == 200
    assert declaration.json()["capability_status"] == "accepted"

    governance_current = client.get("/api/governance/current")
    assert governance_current.status_code == 200
    assert governance_current.json()["governance_version"] == "gov-2026.04"

    governance_refresh = client.post("/api/governance/refresh")
    assert governance_refresh.status_code == 200
    assert governance_refresh.json()["updated"] is False

    operational = client.get("/api/node/operational-status")
    assert operational.status_code == 200
    assert operational.json()["operational_ready"] is True

    node_status = client.get("/api/node/status")
    assert node_status.status_code == 200
    assert node_status.json()["current_step_id"] == "ready"
    assert node_status.json()["operational_ready"] is True
    assert node_status.json()["capability_status"] == "accepted"
    assert node_status.json()["governance_sync_status"] == "issued"
