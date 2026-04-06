from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService


def test_initial_state_is_unconfigured():
    service = NodeRuntimeService(settings=Settings())
    assert service.status_payload().lifecycle_state == "bootstrap_required"
