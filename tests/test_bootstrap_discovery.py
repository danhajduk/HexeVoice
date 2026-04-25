from hexevoice.api.models import BootstrapAdvertisementRequest
from hexevoice.config.settings import Settings
from hexevoice.onboarding.bootstrap import BootstrapDiscoveryService
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def test_bootstrap_advertisement_validation_persists_discovery_state(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "protocol_version": "1.0",
                    "node_nonce": "voice-node-nonce",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "resume": {
                    "current_step_id": "bootstrap_discovery",
                    "last_completed_step_id": "core_connection",
                },
            }
        )
    )

    service = BootstrapDiscoveryService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )

    payload = service.validate_advertisement(
        BootstrapAdvertisementRequest(
            topic="hexe/bootstrap/core",
            api_base="http://10.0.0.100:9001",
            mqtt_host="10.0.0.100",
            mqtt_port=1884,
            onboarding_mode="api",
            onboarding_contract="global-node-v1",
            onboarding_endpoints={
                "register_session": "/api/system/nodes/onboarding/sessions",
                "registrations": "/api/system/nodes/registrations",
                "register": "/api/system/nodes/onboarding/sessions",
                "ai_node_register": "/api/system/ai-nodes/onboarding/sessions",
            },
        )
    )

    persisted = store.load()

    assert payload.advertisement_valid is True
    assert persisted.bootstrap_discovery.register_session_endpoint == "/api/system/nodes/onboarding/sessions"
    assert persisted.resume.current_step_id == "registration"
    assert persisted.resume.last_completed_step_id == "bootstrap_discovery"
