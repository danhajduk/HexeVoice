import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from hexevoice.capabilities.service import VOICE_NODE_CAPABILITIES
from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def _prepare_runtime(runtime_dir):
    payload = json.loads((Path(__file__).resolve().parents[1] / "config" / "runtime-dirs.json").read_text(encoding="utf-8"))
    for rel in payload["runtime_dirs"]:
        (runtime_dir / rel).mkdir(parents=True, exist_ok=True)
    firmware_dir = runtime_dir / "firmware"
    (firmware_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (firmware_dir / "hexe_firmware.bin").write_bytes(b"firmware")


def ready_settings(tmp_path) -> Settings:
    state_path = tmp_path / "onboarding-state.json"
    runtime_dir = tmp_path / "runtime"
    _prepare_runtime(runtime_dir)
    OnboardingStateStore(path=state_path).save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "core_base_url": "http://core.test:9001",
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
                "capability_declaration": {
                    "capability_status": "accepted",
                    "declared_task_families": VOICE_NODE_CAPABILITIES,
                    "declared_capabilities": VOICE_NODE_CAPABILITIES,
                    "capability_profile_id": "profile-123",
                    "governance_version": "gov-2026.04",
                },
                "governance_sync": {
                    "governance_sync_status": "issued",
                    "governance_version": "gov-2026.04",
                    "issued_timestamp": "2026-04-08T03:00:05+00:00",
                    "refresh_interval_s": 3600,
                    "governance_bundle": {"telemetry_requirements": {"interval_s": 60}},
                    "governance_freshness_state": "fresh",
                },
                "resume": {
                    "current_step_id": "governance_sync",
                    "last_completed_step_id": "capability_declaration",
                },
            }
        )
    )
    return Settings(
        onboarding_state_path=state_path,
        runtime_dir=runtime_dir,
        public_ui_base_url="http://ui.test",
        endpoint_registry_path=tmp_path / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
    )


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("failed", request=None, response=self)

    def json(self):
        return self._payload


def test_setup_ready_status_blocks_before_smoke_test(tmp_path):
    client = TestClient(create_app(ready_settings(tmp_path)))

    response = client.get("/api/setup/ready/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["completed"] is False
    assert payload["continue_blocked"] is True
    assert payload["setup_root_redirect_active"] is True


def test_setup_ready_smoke_test_and_complete(tmp_path, monkeypatch):
    settings = ready_settings(tmp_path)
    client = TestClient(create_app(settings))

    def fake_get(url, headers=None, params=None, timeout=None, follow_redirects=None):
        if str(url).rstrip("/") == "http://ui.test":
            return FakeResponse(status_code=200)
        if str(url).endswith("/api/system/nodes/operational-status/node-voice-123"):
            return FakeResponse(
                {
                    "node_id": "node-voice-123",
                    "lifecycle_state": "operational",
                    "trust_status": "trusted",
                    "capability_status": "accepted",
                    "governance_status": "issued",
                    "operational_ready": True,
                    "active_governance_version": "gov-2026.04",
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "get", fake_get)

    smoke = client.post("/api/setup/ready/run-smoke-test")
    assert smoke.status_code == 200
    smoke_payload = smoke.json()
    assert smoke_payload["smoke"]["ok"] is True
    assert smoke_payload["status"]["continue_blocked"] is False
    checks = {check["id"]: check for check in smoke_payload["smoke"]["checks"]}
    assert checks["stt_provider_response"]["status"] == "pass"
    assert checks["tts_provider_response"]["status"] == "pass"
    assert checks["wake_provider_response"]["status"] == "warn"
    assert checks["backend_provider_calls"]["status"] == "pass"
    assert checks["core_trust_visibility"]["status"] == "pass"
    assert checks["core_capability_visibility"]["status"] == "pass"
    assert checks["governance_currency"]["status"] == "pass"
    assert checks["supervisor_registration"]["status"] == "warn"

    complete = client.post("/api/setup/ready/complete")
    assert complete.status_code == 200
    complete_payload = complete.json()
    assert complete_payload["accepted"] is True
    assert complete_payload["status"]["completed"] is True
    assert complete_payload["status"]["setup_root_redirect_active"] is False
    persisted = OnboardingStateStore(path=settings.resolved_onboarding_state_path()).load()
    assert persisted.resume.current_step_id == "ready"
    assert persisted.operational_status.operational_ready is True

    export = client.post("/api/setup/ready/export")
    assert export.status_code == 200
    export_payload = export.json()
    assert export_payload["setup_summary"]["node_id"] == "node-voice-123"
    assert export_payload["recovery_bundle"]["migration_bundle"]["contains_trust_secrets"] is False
    trust_payload = export_payload["recovery_bundle"]["migration_bundle"]["state_files"]["onboarding_state"]["trust_activation"]
    assert trust_payload.get("node_trust_token") is None
    assert export_payload["migration_import_receipt"]["receipt"] is None
    assert export_payload["download_url"] == "/api/setup/ready/export/download"

    download = client.get("/api/setup/ready/export/download")
    assert download.status_code == 200
    assert download.json()["setup_summary"]["node_id"] == "node-voice-123"


def test_setup_ready_smoke_test_blocks_when_core_is_not_visible(tmp_path, monkeypatch):
    client = TestClient(create_app(ready_settings(tmp_path)))

    def fake_get(url, headers=None, params=None, timeout=None, follow_redirects=None):
        if str(url).rstrip("/") == "http://ui.test":
            return FakeResponse(status_code=200)
        if str(url).endswith("/api/system/nodes/operational-status/node-voice-123"):
            raise httpx.ConnectError("core offline")
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "get", fake_get)

    smoke = client.post("/api/setup/ready/run-smoke-test")
    complete = client.post("/api/setup/ready/complete")

    assert smoke.status_code == 200
    assert smoke.json()["smoke"]["ok"] is False
    assert "core_node_visibility" in [
        check["id"] for check in smoke.json()["smoke"]["checks"] if check["status"] == "fail"
    ]
    assert complete.json()["accepted"] is False
