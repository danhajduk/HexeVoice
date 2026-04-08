from fastapi import HTTPException

from hexevoice.onboarding.trust_activation import TrustActivationService
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def test_trust_activation_finalize_consumes_pending_activation_once(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
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

    service = TrustActivationService(onboarding_state_store=store)
    response = service.finalize_activation()
    persisted = store.load()

    assert response.node_id == "node-voice-123"
    assert response.trust_state == "trusted"
    assert persisted.trust_activation.node_id == "node-voice-123"
    assert persisted.trust_activation.node_trust_token == "trust-token-123"
    assert persisted.trust_activation.operational_mqtt_token == "mqtt-token-123"
    assert persisted.trust_activation.initial_baseline_policy == {"version": "2026.04"}
    assert persisted.trust_activation.activation_profile == {"voice": {"default_provider": "mock"}}
    assert persisted.trust_activation.source_session_id == "session-123"
    assert persisted.onboarding_session.pending_activation is None
    assert persisted.resume.current_step_id == "provider_setup"
    assert persisted.resume.last_completed_step_id == "trust_activation"


def test_trust_activation_finalize_rejects_missing_pending_activation(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "onboarding_session": {
                    "session_id": "session-123",
                    "session_state": "approved",
                },
                "resume": {
                    "current_step_id": "trust_activation",
                    "last_completed_step_id": "approval",
                },
            }
        )
    )

    service = TrustActivationService(onboarding_state_store=store)

    try:
        service.finalize_activation()
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "trust_activation_not_pending"
    else:
        raise AssertionError("Expected trust activation finalize to fail without pending activation")
