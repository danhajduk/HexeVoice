import httpx

from hexevoice.api.models import CapabilitySelectionRequest
from hexevoice.capabilities.service import CapabilityDeclarationService, VOICE_NODE_CAPABILITIES
from hexevoice.config.settings import Settings
from hexevoice.governance.service import GovernanceService
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def _trusted_phase2_store(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
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
    return store


def test_capability_declaration_persists_accepted_profile(tmp_path, monkeypatch):
    store = _trusted_phase2_store(tmp_path)
    captured = {}

    class DummyResponse:
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

    def fake_post(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return DummyResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    service = CapabilityDeclarationService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )
    response = service.declare()
    persisted = store.load()

    assert response.capability_status == "accepted"
    assert captured["json"]["manifest"]["manifest_version"] == "1.0"
    assert captured["json"]["manifest"]["declared_task_families"] == VOICE_NODE_CAPABILITIES
    assert captured["json"]["manifest"]["declared_capabilities"] == VOICE_NODE_CAPABILITIES
    endpoints = captured["json"]["manifest"]["capability_endpoints"]
    assert endpoints["voice.tts.synthesize"]["method"] == "POST"
    assert endpoints["voice.tts.synthesize"]["path"] == "/api/tts/synthesize"
    assert endpoints["voice.tts.audio_url"]["path"] == "/api/tts/audio/{stream_id}"
    assert captured["json"]["manifest"]["enabled_providers"] == ["voice"]
    assert persisted.capability_declaration.capability_profile_id == "profile-123"
    assert persisted.capability_declaration.capability_status == "accepted"
    assert persisted.resume.current_step_id == "governance_sync"


def test_capability_selection_controls_next_declaration(tmp_path, monkeypatch):
    store = _trusted_phase2_store(tmp_path)
    service = CapabilityDeclarationService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )

    selection = service.save_selection(
        CapabilitySelectionRequest(selected_capabilities=["voice.inference", "voice.tts.synthesize"])
    )

    assert selection.selected == ["voice.inference", "voice.tts.synthesize"]
    assert selection.available == VOICE_NODE_CAPABILITIES
    assert store.load().capability_declaration.capability_status == "selection_pending"

    captured = {}

    class DummyResponse:
        status_code = 200
        def raise_for_status(self): return None
        def json(self):
            return {
                "acceptance_status": "accepted",
                "node_id": "node-voice-123",
                "manifest_version": "1.0",
                "accepted_at": "2026-04-08T03:00:00+00:00",
                "declared_capabilities": ["voice.inference", "voice.tts.synthesize"],
                "enabled_providers": ["voice"],
                "capability_profile_id": "profile-123",
                "governance_version": "gov-2026.04",
                "governance_issued_at": "2026-04-08T03:00:05+00:00",
            }

    def fake_post(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return DummyResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    response = service.declare()

    manifest = captured["json"]["manifest"]
    assert response.declared_capabilities == ["voice.inference", "voice.tts.synthesize"]
    assert manifest["declared_capabilities"] == ["voice.inference", "voice.tts.synthesize"]
    assert sorted(manifest["capability_endpoints"]) == ["voice.tts.synthesize"]


def test_governance_and_operational_status_persist_phase2_readiness(tmp_path, monkeypatch):
    store = _trusted_phase2_store(tmp_path)
    store.save(
        store.load().model_copy(
            update={
                "capability_declaration": store.load().capability_declaration.model_copy(
                    update={
                        "capability_status": "accepted",
                        "capability_profile_id": "profile-123",
                    }
                ),
                "resume": store.load().resume.model_copy(
                    update={"current_step_id": "governance_sync", "last_completed_step_id": "capability_declaration"}
                ),
            }
        )
    )

    class GovernanceResponse:
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

    class OperationalResponse:
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

    def fake_get(url, headers=None, params=None, timeout=None):
        if url.endswith("/api/system/nodes/governance/current"):
            return GovernanceResponse()
        if url.endswith("/api/system/nodes/operational-status/node-voice-123"):
            return OperationalResponse()
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "get", fake_get)

    service = GovernanceService(onboarding_state_store=store)
    current = service.current()
    operational = service.operational_status()
    persisted = store.load()

    assert current.governance_version == "gov-2026.04"
    assert operational.operational_ready is True
    assert persisted.governance_sync.governance_sync_status == "issued"
    assert persisted.operational_status.operational_ready is True
    assert persisted.resume.current_step_id == "ready"
