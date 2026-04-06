from fastapi.testclient import TestClient

from hexevoice.main import create_app


def test_status_endpoint():
    client = TestClient(create_app())
    response = client.get("/api/node/status")
    assert response.status_code == 200
    assert response.json()["trust_state"] == "untrusted"


def test_standard_route_groups_exist():
    client = TestClient(create_app())

    assert client.get("/api/onboarding/status").status_code == 200
    assert client.get("/api/capabilities").status_code == 200
    assert client.get("/api/governance/readiness").status_code == 200
    assert client.get("/api/services/status").status_code == 200
    assert client.get("/api/providers/voice/status").status_code == 200
