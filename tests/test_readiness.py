from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService


def test_initial_state_not_ready():
    service = NodeRuntimeService(settings=Settings())
    assert service.status_payload().operational_ready is False
