import httpx

from hexevoice.trust.status import TrustStatusService
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def test_trust_status_refresh_keeps_provider_setup_when_supported(tmp_path, monkeypatch):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
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
                },
                "resume": {
                    "current_step_id": "provider_setup",
                    "last_completed_step_id": "trust_activation",
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
                "node_id": "node-voice-123",
                "trust_status": "trusted",
                "supported": True,
                "support_state": "supported",
                "registry_present": True,
                "registry_state": "trusted",
                "message": "Node trust remains active.",
            }

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: DummyResponse())

    service = TrustStatusService(onboarding_state_store=store)
    response = service.refresh_status()
    persisted = store.load()

    assert response.trust_state == "trusted"
    assert response.support_state == "supported"
    assert persisted.resume.current_step_id == "provider_setup"
    assert persisted.trust_activation.trust_status == "trusted"
    assert persisted.trust_activation.supported is True


def test_trust_status_refresh_moves_revoked_node_back_to_registration(tmp_path, monkeypatch):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "onboarding_session": {
                    "session_id": "session-123",
                    "session_state": "approved",
                    "pending_activation": {
                        "node_id": "node-voice-123",
                        "trust_status": "trusted",
                    },
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

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "node_id": "node-voice-123",
                "trust_status": "revoked",
                "supported": False,
                "support_state": "revoked",
                "registry_present": True,
                "registry_state": "revoked",
                "revoked_at": "2026-04-08T02:00:00+00:00",
                "revocation_reason": "revoked_by_admin",
                "revocation_action": "revoke",
                "message": "Trust was revoked by Core.",
            }

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: DummyResponse())

    service = TrustStatusService(onboarding_state_store=store)
    response = service.refresh_status()
    persisted = store.load()

    assert response.trust_state == "revoked"
    assert response.support_state == "revoked"
    assert persisted.resume.current_step_id == "registration"
    assert persisted.resume.last_completed_step_id == "bootstrap_discovery"
    assert persisted.onboarding_session.session_id is None
    assert persisted.onboarding_session.pending_activation is None
    assert persisted.trust_activation.operational_mqtt_token is None
    assert persisted.trust_activation.revocation_reason == "revoked_by_admin"
