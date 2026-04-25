from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService


def test_initial_state_not_ready(tmp_path):
    service = NodeRuntimeService(settings=Settings(onboarding_state_path=tmp_path / "state.json"))
    assert service.status_payload().operational_ready is False
