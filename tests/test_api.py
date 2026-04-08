from fastapi.testclient import TestClient
import httpx

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
    assert client.get("/api/onboarding/bootstrap-discovery").status_code == 200
    assert client.post("/api/onboarding/session/start").status_code == 400
    assert client.post("/api/onboarding/session/poll").status_code == 400
    assert client.post("/api/onboarding/trust-activation/finalize").status_code == 400
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


def test_bootstrap_discovery_advertisement_validation_advances_to_registration(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "global-node-v1",
            "node_nonce": "voice-node-nonce",
        },
    )
    client.put(
        "/api/onboarding/local-setup/core-connection",
        json={"core_base_url": "http://10.0.0.100:9001"},
    )

    response = client.put(
        "/api/onboarding/bootstrap-discovery/advertisement",
        json={
            "topic": "hexe/bootstrap/core",
            "api_base": "http://10.0.0.100:9001",
            "mqtt_host": "10.0.0.100",
            "mqtt_port": 1884,
            "onboarding_mode": "api",
            "onboarding_contract": "global-node-v1",
            "onboarding_endpoints": {
                "register_session": "/api/system/nodes/onboarding/sessions",
                "registrations": "/api/system/nodes/registrations",
                "register": "/api/system/nodes/onboarding/sessions",
                "ai_node_register": "/api/system/ai-nodes/onboarding/sessions",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["advertisement_valid"] is True
    assert payload["onboarding_mode"] == "api"
    assert payload["onboarding_contract"] == "global-node-v1"

    status_response = client.get("/api/node/status")
    assert status_response.status_code == 200
    assert status_response.json()["current_step_id"] == "registration"
    assert status_response.json()["lifecycle_state"] == "registration_pending"


def test_onboarding_session_start_persists_core_session_metadata(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "global-node-v1",
            "node_nonce": "voice-node-nonce",
            "hostname": "kitchen-voice.local",
            "api_base_url": "http://10.0.0.22:9000",
        },
    )
    client.put("/api/onboarding/local-setup/core-connection", json={"core_base_url": "http://10.0.0.100:9001"})
    client.put(
        "/api/onboarding/bootstrap-discovery/advertisement",
        json={
            "topic": "hexe/bootstrap/core",
            "api_base": "http://10.0.0.100:9001",
            "mqtt_host": "10.0.0.100",
            "mqtt_port": 1884,
            "onboarding_mode": "api",
            "onboarding_contract": "global-node-v1",
            "onboarding_endpoints": {
                "register_session": "/api/system/nodes/onboarding/sessions",
                "registrations": "/api/system/nodes/registrations",
            },
        },
    )

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "node_name": "kitchen-voice",
                "node_type": "voice-node",
                "node_software_version": "0.1.0",
                "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123&state=abc",
                "session_id": "session-123",
                "expires_at": "2026-04-08T01:00:00+00:00",
                "finalize": "/api/system/nodes/onboarding/sessions/session-123/finalize?node_nonce=voice-node-nonce",
            }

    def fake_post(url, json, timeout):
        assert url == "http://10.0.0.100:9001/api/system/nodes/onboarding/sessions"
        assert json["node_name"] == "kitchen-voice"
        assert json["protocol_version"] == "global-node-v1"
        assert json["node_nonce"] == "voice-node-nonce"
        return DummyResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    response = client.post("/api/onboarding/session/start")
    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "session-123"
    assert payload["approval_url"].startswith("http://10.0.0.100/onboarding/nodes/approve")

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["session_id"] == "session-123"
    assert onboarding_status.json()["approval_url"].startswith("http://10.0.0.100/onboarding/nodes/approve")
    assert onboarding_status.json()["session_state"] == "pending"

    node_status = client.get("/api/node/status")
    assert node_status.status_code == 200
    assert node_status.json()["current_step_id"] == "approval"
    assert node_status.json()["lifecycle_state"] == "pending_approval"


def test_onboarding_session_poll_surfaces_approved_outcome(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "global-node-v1",
            "node_nonce": "voice-node-nonce",
            "hostname": "kitchen-voice.local",
            "api_base_url": "http://10.0.0.22:9000",
        },
    )
    client.put("/api/onboarding/local-setup/core-connection", json={"core_base_url": "http://10.0.0.100:9001"})
    client.put(
        "/api/onboarding/bootstrap-discovery/advertisement",
        json={
            "topic": "hexe/bootstrap/core",
            "api_base": "http://10.0.0.100:9001",
            "mqtt_host": "10.0.0.100",
            "mqtt_port": 1884,
            "onboarding_mode": "api",
            "onboarding_contract": "global-node-v1",
            "onboarding_endpoints": {
                "register_session": "/api/system/nodes/onboarding/sessions",
                "registrations": "/api/system/nodes/registrations",
            },
        },
    )

    class SessionStartResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "node_name": "kitchen-voice",
                "node_type": "voice-node",
                "node_software_version": "0.1.0",
                "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123&state=abc",
                "session_id": "session-123",
                "expires_at": "2026-04-08T01:00:00+00:00",
                "finalize": "/api/system/nodes/onboarding/sessions/session-123/finalize?node_nonce=voice-node-nonce",
            }

    class SessionPollResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "approved",
                "activation": {
                    "node_id": "node-voice-123",
                    "paired_core_id": "core-main",
                    "node_trust_token": "trust-token-123",
                    "baseline_policy_version": "2026.04",
                    "operational_mqtt_identity": "node-voice-123",
                    "operational_mqtt_host": "10.0.0.100",
                    "operational_mqtt_port": 1883,
                    "trust_status": "trusted",
                },
            }

    def fake_post(url, json, timeout):
        return SessionStartResponse()

    def fake_get(url, params, timeout):
        assert url == "http://10.0.0.100:9001/api/system/nodes/onboarding/sessions/session-123/finalize"
        assert params == {"node_nonce": "voice-node-nonce"}
        return SessionPollResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)

    start_response = client.post("/api/onboarding/session/start")
    assert start_response.status_code == 200

    poll_response = client.post("/api/onboarding/session/poll")
    assert poll_response.status_code == 200
    assert poll_response.json()["session_state"] == "approved"
    assert poll_response.json()["activation_received"] is True

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["session_state"] == "approved"
    assert onboarding_status.json()["last_terminal_outcome"] == "approved"
    assert onboarding_status.json()["current_step_id"] == "trust_activation"


def test_trust_activation_finalize_persists_trusted_state(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "onboarding_session": {
                    "session_id": "session-123",
                    "session_state": "approved",
                    "pending_activation": {
                        "node_id": "node-voice-123",
                        "node_type": "voice-node",
                        "paired_core_id": "core-main",
                        "node_trust_token": "trust-token-123",
                        "initial_baseline_policy": {"version": "2026.04"},
                        "baseline_policy_version": "2026.04",
                        "activation_profile": {"voice": {"default_provider": "mock"}},
                        "operational_mqtt_identity": "node-voice-123",
                        "operational_mqtt_token": "mqtt-token-123",
                        "operational_mqtt_host": "10.0.0.100",
                        "operational_mqtt_port": 1883,
                        "issued_at": "2026-04-08T01:00:00+00:00",
                        "source_session_id": "session-123",
                        "trust_status": "trusted",
                    },
                },
                "resume": {
                    "current_step_id": "trust_activation",
                    "last_completed_step_id": "approval",
                },
            }
        )
    )

    response = client.post("/api/onboarding/trust-activation/finalize")
    assert response.status_code == 200
    payload = response.json()
    assert payload["node_id"] == "node-voice-123"
    assert payload["trust_state"] == "trusted"
    assert payload["operational_mqtt_host"] == "10.0.0.100"

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["current_step_id"] == "provider_setup"
    assert onboarding_status.json()["trust_state"] == "trusted"

    node_status = client.get("/api/node/status")
    assert node_status.status_code == 200
    assert node_status.json()["node_id"] == "node-voice-123"
    assert node_status.json()["trust_state"] == "trusted"
    assert node_status.json()["current_step_id"] == "provider_setup"
