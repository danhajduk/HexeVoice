from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService


def test_starter_runtime_surfaces_onboarding_blocker():
    service = NodeRuntimeService(settings=Settings())
    assert "node_identity_not_configured" in service.status_payload().blocking_reasons
