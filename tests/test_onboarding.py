from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState
from hexevoice.runtime.service import NodeRuntimeService


def test_initial_state_is_unconfigured():
    service = NodeRuntimeService(settings=Settings())
    assert service.status_payload().lifecycle_state == "unconfigured"


def test_onboarding_payload_uses_canonical_first_step():
    service = NodeRuntimeService(settings=Settings())

    payload = service.onboarding_payload()

    assert payload.current_step_id == "node_identity"
    assert payload.current_step_label == "Node Identity"
    assert payload.lifecycle_state == "unconfigured"
    assert payload.next_action == "configure_node_identity"
    assert len(payload.steps) == 10
    assert payload.steps[0].current is True
    assert payload.steps[0].step_id == "node_identity"
    assert payload.steps[-1].step_id == "ready"


def test_runtime_service_resumes_from_persisted_onboarding_state(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
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

    service = NodeRuntimeService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )

    status = service.status_payload()
    onboarding = service.onboarding_payload()

    assert status.node_id == "node-voice-123"
    assert status.trust_state == "trusted"
    assert status.current_step_id == "provider_setup"
    assert status.lifecycle_state == "capability_setup_pending"
    assert onboarding.onboarding_state == "trust_activated"
    assert onboarding.next_action == "configure_provider_setup"
