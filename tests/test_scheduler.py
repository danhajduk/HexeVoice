from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService


def test_starter_runtime_surfaces_onboarding_blocker():
    service = NodeRuntimeService(settings=Settings())
    assert "onboarding_not_started" in service.status_payload().blocking_reasons
