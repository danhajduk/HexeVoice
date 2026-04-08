from fastapi.testclient import TestClient

from hexevoice.main import create_app
from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def test_status_endpoint():
    client = TestClient(create_app())
    response = client.get("/api/node/status")
    assert response.status_code == 200
    assert response.json()["trust_state"] == "untrusted"
    assert response.json()["lifecycle_state"] == "unconfigured"
    assert response.json()["current_step_id"] == "node_identity"


def test_standard_route_groups_exist():
    client = TestClient(create_app())

    onboarding = client.get("/api/onboarding/status")
    assert onboarding.status_code == 200
    assert onboarding.json()["current_step_id"] == "node_identity"
    assert len(onboarding.json()["steps"]) == 10
    assert client.get("/api/onboarding/local-setup").status_code == 200
    assert client.get("/api/capabilities").status_code == 200
    assert client.get("/api/governance/readiness").status_code == 200
    assert client.get("/api/services/status").status_code == 200
    assert client.get("/api/providers/voice/status").status_code == 200


def test_status_endpoint_reads_persisted_onboarding_state(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "onboarding_session": {
                    "session_id": "session-123",
                    "session_state": "approved",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "resume": {
                    "current_step_id": "provider_setup",
                },
            }
        )
    )

    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    response = client.get("/api/node/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["node_id"] == "node-voice-123"
    assert payload["trust_state"] == "trusted"
    assert payload["current_step_id"] == "provider_setup"
    assert payload["lifecycle_state"] == "capability_setup_pending"


def test_local_setup_endpoints_persist_node_identity_and_core_connection(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    identity_response = client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "global-node-v1",
            "node_nonce": "voice-node-nonce",
            "hostname": "kitchen-voice.local",
            "api_base_url": "http://10.0.0.22:9000",
        },
    )
    assert identity_response.status_code == 200
    assert identity_response.json()["configured"] is True

    connection_response = client.put(
        "/api/onboarding/local-setup/core-connection",
        json={"core_base_url": "http://10.0.0.100:9001"},
    )
    assert connection_response.status_code == 200
    assert connection_response.json()["configured"] is True

    setup_state = client.get("/api/onboarding/local-setup")
    assert setup_state.status_code == 200
    assert setup_state.json()["node_identity"]["node_name"] == "kitchen-voice"
    assert setup_state.json()["core_connection"]["core_base_url"] == "http://10.0.0.100:9001/"

    status_response = client.get("/api/node/status")
    assert status_response.status_code == 200
    assert status_response.json()["node_name"] == "kitchen-voice"
    assert status_response.json()["current_step_id"] == "bootstrap_discovery"
    assert status_response.json()["lifecycle_state"] == "core_discovered"
