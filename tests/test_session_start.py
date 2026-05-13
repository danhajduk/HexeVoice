import httpx

from hexevoice.config.settings import Settings
from hexevoice.onboarding.session_start import OnboardingSessionStartService
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def test_session_start_writes_pending_session_state(tmp_path, monkeypatch):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "protocol_version": "1.0",
                    "node_nonce": "voice-node-nonce",
                    "hostname": "kitchen-voice.local",
                    "api_base_url": "http://10.0.0.22:9000",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "bootstrap_discovery": {
                    "advertisement_valid": True,
                },
                "resume": {
                    "current_step_id": "registration",
                    "last_completed_step_id": "bootstrap_discovery",
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
                "node_name": "kitchen-voice",
                "node_type": "voice-node",
                "node_software_version": "0.1.0",
                "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123&state=abc",
                "session_id": "session-123",
                "expires_at": "2026-04-08T01:00:00+00:00",
                "finalize": "/api/system/nodes/onboarding/sessions/session-123/finalize?node_nonce=voice-node-nonce",
            }

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: DummyResponse())

    service = OnboardingSessionStartService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )
    response = service.start_session()
    persisted = store.load()

    assert response.session_id == "session-123"
    assert persisted.onboarding_session.session_id == "session-123"
    assert persisted.onboarding_session.session_state == "pending"
    assert persisted.resume.current_step_id == "approval"


def test_session_start_accepts_wrapped_core_session_response(tmp_path, monkeypatch):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "protocol_version": "1.0",
                    "node_nonce": "voice-node-nonce",
                    "hostname": "kitchen-voice.local",
                    "api_base_url": "http://10.0.0.22:9000",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "bootstrap_discovery": {
                    "advertisement_valid": True,
                },
                "resume": {
                    "current_step_id": "registration",
                    "last_completed_step_id": "bootstrap_discovery",
                },
            }
        )
    )

    class WrappedResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "session": {
                    "session_id": "sx_wrapped",
                    "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=sx_wrapped&state=abc",
                    "expires_at": "2026-04-08T01:00:00+00:00",
                    "finalize": {
                        "method": "GET",
                        "path": "/api/system/nodes/onboarding/sessions/sx_wrapped/finalize",
                    },
                },
            }

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: WrappedResponse())

    service = OnboardingSessionStartService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )
    response = service.start_session()
    persisted = store.load()

    assert response.session_id == "sx_wrapped"
    assert response.approval_url.startswith("http://10.0.0.100/onboarding/nodes/approve")
    assert persisted.onboarding_session.session_id == "sx_wrapped"
    assert persisted.onboarding_session.session_state == "pending"


def test_session_start_defaults_core_registration_metadata_from_runtime_settings(tmp_path, monkeypatch):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "requested_node_id": "node-kitchen-voice",
                    "protocol_version": "1.0",
                    "node_nonce": "voice-node-nonce",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "bootstrap_discovery": {
                    "advertisement_valid": True,
                },
                "resume": {
                    "current_step_id": "registration",
                    "last_completed_step_id": "bootstrap_discovery",
                },
            }
        )
    )
    captured = {}

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
            }

    def fake_post(*args, **kwargs):
        captured["url"] = args[0]
        captured["json"] = kwargs["json"]
        return DummyResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    service = OnboardingSessionStartService(
        settings=Settings(
            onboarding_state_path=tmp_path / "onboarding-state.json",
            public_api_base_url="http://10.0.0.100:9004/",
            public_ui_base_url="http://10.0.0.100:8082",
        ),
        onboarding_state_store=store,
    )
    response = service.start_session()

    assert response.session_id == "session-123"
    assert captured["url"] == "http://10.0.0.100:9001/api/system/nodes/onboarding/sessions"
    assert captured["json"]["node_id"] == "node-kitchen-voice"
    assert captured["json"]["api_base_url"] == "http://10.0.0.100:9004"
    assert captured["json"]["ui_endpoint"] == "http://10.0.0.100:8082"
    assert captured["json"]["hostname"]
