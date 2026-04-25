import httpx

from hexevoice.config.settings import Settings
from hexevoice.onboarding.approval import ApprovalPollingService
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def test_approval_polling_marks_pending_without_advancing(tmp_path, monkeypatch):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "protocol_version": "1.0",
                    "node_nonce": "voice-node-nonce",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "bootstrap_discovery": {
                    "advertisement_valid": True,
                },
                "onboarding_session": {
                    "session_id": "session-123",
                    "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123&state=abc",
                    "session_state": "pending",
                },
                "resume": {
                    "current_step_id": "approval",
                    "last_completed_step_id": "registration",
                },
            }
        )
    )

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "pending"}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: DummyResponse())

    service = ApprovalPollingService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )
    response = service.poll_session()
    persisted = store.load()

    assert response.session_state == "pending"
    assert response.activation_received is False
    assert persisted.onboarding_session.session_state == "pending"
    assert persisted.onboarding_session.last_terminal_outcome is None
    assert persisted.resume.current_step_id == "approval"


def test_approval_polling_stashes_activation_and_advances_to_trust_activation(tmp_path, monkeypatch):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "protocol_version": "1.0",
                    "node_nonce": "voice-node-nonce",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "bootstrap_discovery": {
                    "advertisement_valid": True,
                },
                "onboarding_session": {
                    "session_id": "session-123",
                    "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123&state=abc",
                    "session_state": "pending",
                },
                "resume": {
                    "current_step_id": "approval",
                    "last_completed_step_id": "registration",
                },
            }
        )
    )

    class DummyResponse:
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

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: DummyResponse())

    service = ApprovalPollingService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )
    response = service.poll_session()
    persisted = store.load()

    assert response.session_state == "approved"
    assert response.activation_received is True
    assert persisted.onboarding_session.session_state == "approved"
    assert persisted.onboarding_session.last_terminal_outcome == "approved"
    assert persisted.onboarding_session.pending_activation is not None
    assert persisted.onboarding_session.pending_activation["node_id"] == "node-voice-123"
    assert persisted.resume.current_step_id == "trust_activation"
    assert persisted.resume.last_completed_step_id == "approval"


def test_approval_polling_accepts_onboarding_status_field(tmp_path, monkeypatch):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "protocol_version": "1.0",
                    "node_nonce": "voice-node-nonce",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "bootstrap_discovery": {
                    "advertisement_valid": True,
                },
                "onboarding_session": {
                    "session_id": "session-123",
                    "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123&state=abc",
                    "session_state": "pending",
                },
                "resume": {
                    "current_step_id": "approval",
                    "last_completed_step_id": "registration",
                },
            }
        )
    )

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "onboarding_status": "approved",
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

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: DummyResponse())

    service = ApprovalPollingService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )
    response = service.poll_session()
    persisted = store.load()

    assert response.session_state == "approved"
    assert response.activation_received is True
    assert persisted.resume.current_step_id == "trust_activation"
