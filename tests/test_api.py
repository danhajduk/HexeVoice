from fastapi.testclient import TestClient

from hexevoice.main import create_app


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
    assert client.get("/api/capabilities").status_code == 200
    assert client.get("/api/governance/readiness").status_code == 200
    assert client.get("/api/services/status").status_code == 200
    assert client.get("/api/providers/voice/status").status_code == 200
