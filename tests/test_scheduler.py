from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService


def test_starter_runtime_surfaces_onboarding_blocker(tmp_path):
    service = NodeRuntimeService(settings=Settings(onboarding_state_path=tmp_path / "state.json"))
    assert "node_identity_not_configured" in service.status_payload().blocking_reasons
