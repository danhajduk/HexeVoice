from hexevoice.config.settings import Settings
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
