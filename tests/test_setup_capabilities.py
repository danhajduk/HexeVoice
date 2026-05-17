import httpx
from fastapi.testclient import TestClient

from hexevoice.capabilities.service import VOICE_NODE_CAPABILITIES
from hexevoice.capabilities.schema import CapabilityManifestValidationError, validate_capability_declaration
from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def trusted_capability_settings(tmp_path) -> Settings:
    state_path = tmp_path / "onboarding-state.json"
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
                "resume": {
                    "current_step_id": "capability_declaration",
                    "last_completed_step_id": "provider_setup",
                },
            }
        )
    )
    return Settings(
        onboarding_state_path=state_path,
        endpoint_registry_path=tmp_path / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
    )


def test_setup_capabilities_status_blocks_until_declaration_and_governance(tmp_path):
    client = TestClient(create_app(trusted_capability_settings(tmp_path)))

    response = client.get("/api/setup/capabilities/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["capabilities"]["selected"] == VOICE_NODE_CAPABILITIES
    assert payload["capability_current"] is False
    assert payload["governance_current"] is False
    assert payload["continue_blocked"] is True
    assert "capability_declaration_not_current" in payload["blockers"]
    assert "governance_not_current" in payload["blockers"]


def test_setup_capabilities_status_includes_manifest_preview(tmp_path):
    settings = trusted_capability_settings(tmp_path).model_copy(
        update={
            "public_api_base_url": "http://voice.local:8084",
            "voice_stt_provider": "external_faster_whisper",
            "voice_tts_provider": "piper",
            "voice_wake_provider": "openwakeword",
            "voice_tts_piper_voice": "en_US-kathleen-low",
        }
    )
    store = OnboardingStateStore(path=settings.onboarding_state_path)
    state = store.load()
    store.save(
        state.model_copy(
            update={
                "provider_setup": state.provider_setup.model_copy(
                    update={
                        "supported_providers": ["voice", "external_faster_whisper", "piper", "openwakeword"],
                        "enabled_providers": ["voice", "external_faster_whisper", "piper", "openwakeword"],
                        "default_provider": "voice",
                        "provider_configs": {
                            "external_faster_whisper": {
                                "model": "small.en",
                                "profile": "cuda_fast_intent",
                                "device": "cuda",
                                "cuda_mode": "cuda",
                                "compute_type": "float16",
                                "language": "en",
                            },
                            "piper": {"default_voice": "en_US-kathleen-low"},
                            "openwakeword": {"default_wakeword": "Hexe", "threshold": 0.5},
                        },
                    }
                )
            }
        )
    )
    client = TestClient(create_app(settings))

    payload = client.get("/api/setup/capabilities/status").json()
    preview = payload["manifest_preview"]

    assert preview["node_identity"]["node_id"] == "node-voice-123"
    assert preview["runtime"]["api_base_url"] == "http://voice.local:8084"
    assert preview["declaration_payload"]["manifest"]["capability_endpoints"]["voice.tts.synthesize"]["url"] == "http://voice.local:8084/api/tts/synthesize"
    assert preview["providers"]["enabled"] == ["external_faster_whisper", "openwakeword", "piper", "voice"]
    models = {item["provider_id"]: item for item in preview["providers"]["models"]}
    assert models["external_faster_whisper"]["model"] == "small.en"
    assert models["piper"]["model"] == "en_US-kathleen-low"
    assert models["openwakeword"]["model"] == "Hexe"
    assert preview["budget_declaration"]["node_id"] == "node-voice-123"
    summary = preview["core_visible_summary"]
    services = {item["service_id"]: item for item in summary["provided_services"]}
    assert services["stt"]["provider_id"] == "external_faster_whisper"
    assert services["stt"]["models"] == ["small.en"]
    assert services["tts"]["provider_id"] == "piper"
    assert services["wake"]["provider_id"] == "openwakeword"
    assert "voice.inference" in summary["enabled_capabilities"]
    assert summary["disabled_capabilities"] == []
    assert {"provider_id": "external_faster_whisper", "role": "stt", "model_id": "small.en", "enabled": True} in summary["available_models"]
    assert payload["recovery_actions"]["core_governance_url"] == "http://core.test:9001/system/governance"
    assert payload["recovery_actions"]["core_node_governance_url"] == "http://core.test:9001/system/nodes/node-voice-123/governance"


def test_setup_capabilities_status_summarizes_governance_bundle(tmp_path):
    settings = trusted_capability_settings(tmp_path)
    store = OnboardingStateStore(path=settings.onboarding_state_path)
    state = store.load()
    store.save(
        state.model_copy(
            update={
                "governance_sync": state.governance_sync.model_copy(
                    update={
                        "governance_sync_status": "issued",
                        "governance_version": "gov-1",
                        "governance_bundle": {
                            "status": "pending",
                            "accepted_changes": [{"id": "telemetry"}],
                            "denied_requirements": ["cloud-audio"],
                            "pending_requirements": ["budget-review"],
                            "local_required_changes": [{"id": "enable-audit-log"}],
                        },
                    }
                )
            }
        )
    )
    client = TestClient(create_app(settings))

    summary = client.get("/api/setup/capabilities/status").json()["governance_summary"]

    assert summary["status"] == "pending"
    assert summary["accepted"] == [{"id": "telemetry"}]
    assert summary["denied"] == ["cloud-audio"]
    assert summary["pending"] == ["budget-review"]
    assert summary["local_required_changes"] == [{"id": "enable-audit-log"}]


def test_setup_capabilities_status_blocks_invalid_manifest(tmp_path):
    settings = trusted_capability_settings(tmp_path)
    store = OnboardingStateStore(path=settings.onboarding_state_path)
    state = store.load()
    store.save(
        state.model_copy(
            update={
                "trust_activation": state.trust_activation.model_copy(update={"node_id": ""}),
            }
        )
    )
    client = TestClient(create_app(settings))

    payload = client.get("/api/setup/capabilities/status").json()

    assert payload["manifest_validation"]["valid"] is False
    assert "node_id_missing" in payload["manifest_validation"]["errors"]
    assert "invalid_manifest:node_id_missing" in payload["blockers"]


def test_local_capability_manifest_schema_rejects_core_incompatible_payload():
    payload = {
        "manifest_version": "1.0",
        "node": {
            "node_id": "node-voice-123",
            "node_type": "voice-node",
            "node_name": "kitchen",
            "node_software_version": "0.1.0",
        },
        "declared_task_families": ["voice.inference"],
        "declared_capabilities": ["voice.tts.synthesize"],
        "capability_endpoints": {},
        "supported_providers": ["voice"],
        "enabled_providers": ["external_faster_whisper"],
        "node_features": {"telemetry": True},
        "environment_hints": {"network_tier": "lan"},
    }

    try:
        validate_capability_declaration(payload)
    except CapabilityManifestValidationError as exc:
        assert "declared_capabilities_must_match_declared_task_families" in str(exc)
    else:
        raise AssertionError("Expected local manifest schema validation to reject incompatible payload.")


def test_setup_capabilities_declare_rejects_invalid_manifest_before_core_call(tmp_path, monkeypatch):
    settings = trusted_capability_settings(tmp_path)
    store = OnboardingStateStore(path=settings.onboarding_state_path)
    state = store.load()
    store.save(
        state.model_copy(
            update={
                "trust_activation": state.trust_activation.model_copy(update={"node_id": "voice-123"}),
            }
        )
    )
    client = TestClient(create_app(settings))

    def fake_post(url, headers=None, json=None, timeout=None):
        raise AssertionError("Core should not be called for an invalid local manifest.")

    monkeypatch.setattr(httpx, "post", fake_post)

    response = client.post("/api/setup/capabilities/declare")

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is False
    assert payload["status_code"] == 400
    assert "capability_manifest_invalid" in payload["error"]


def test_setup_capabilities_declare_and_sync_governance(tmp_path, monkeypatch):
    client = TestClient(create_app(trusted_capability_settings(tmp_path)))

    class CapabilityResponse:
        status_code = 200

        def raise_for_status(self):
            return None

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

    class GovernanceRefreshResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "updated": True,
                "governance_version": "gov-2026.04",
                "issued_timestamp": "2026-04-08T03:00:05+00:00",
                "refresh_interval_s": 3600,
                "governance_bundle": {"telemetry_requirements": {"interval_s": 60}},
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        if url.endswith("/api/system/nodes/capabilities/declaration"):
            return CapabilityResponse()
        if url.endswith("/api/system/nodes/budgets/declaration"):
            return CapabilityResponse()
        if url.endswith("/api/system/nodes/governance/refresh"):
            return GovernanceRefreshResponse()
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "post", fake_post)

    declaration = client.post("/api/setup/capabilities/declare")
    assert declaration.status_code == 200
    assert declaration.json()["accepted"] is True
    assert declaration.json()["status"]["capability_current"] is True
    assert declaration.json()["status"]["governance_current"] is False

    governance = client.post("/api/setup/capabilities/sync-governance")
    assert governance.status_code == 200
    payload = governance.json()
    assert payload["accepted"] is True
    assert payload["status"]["capability_current"] is True
    assert payload["status"]["governance_current"] is True
    assert payload["status"]["continue_blocked"] is False


def test_setup_capabilities_declare_core_offline_is_retryable(tmp_path, monkeypatch):
    client = TestClient(create_app(trusted_capability_settings(tmp_path)))

    def fake_post(url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError("core offline")

    monkeypatch.setattr(httpx, "post", fake_post)

    response = client.post("/api/setup/capabilities/declare")

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is False
    assert payload["action"] == "declare"
    assert payload["status_code"] == 502
    assert "capability_declaration_request_failed" in payload["error"]
    assert payload["status"]["continue_blocked"] is True
    assert "core_declaration_rejected" in payload["status"]["blockers"]
    assert "core_unavailable" in payload["status"]["blockers"]
