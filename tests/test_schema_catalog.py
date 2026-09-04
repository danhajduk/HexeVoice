from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app


def test_voice_schema_catalog_exposes_intent_contracts(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    families = client.get("/api/schemas")
    assert families.status_code == 200
    assert families.json()["families"] == [
        {
            "schema_family": "voice",
            "versions": [
                {
                    "version": "v1",
                    "api_path": "/api/schemas/voice/v1",
                }
            ],
        }
    ]

    response = client.get("/api/schemas/voice/v1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_family"] == "voice"
    assert payload["version"] == "v1"

    catalog_items = {item["name"]: item for item in payload["schemas"]}
    assert catalog_items["schema-catalog.v1.response.schema.json"]["category"] == "catalog"
    assert catalog_items["intent-registration.schema.json"]["api_path"] == (
        "/api/schemas/voice/v1/intent-registration.schema.json"
    )
    assert catalog_items["intent-registration.schema.json"]["schema_id"] == (
        "https://hexe.local/schemas/intents/intent-registration.schema.json"
    )
    assert catalog_items["voice-event-envelope.schema.json"]["category"] == "voice-event"


def test_voice_schema_catalog_serves_individual_schema(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    response = client.get("/api/schemas/voice/v1/intent-registration.schema.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/schema+json")
    schema = response.json()
    assert schema["title"] == "Voice Intent Registration"
    assert schema["required"] == ["intent_id", "definition"]


def test_voice_schema_catalog_rejects_unknown_schema_names(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    assert client.get("/api/schemas/voice/v1/unknown.schema.json").status_code == 404
    assert client.get("/api/schemas/voice/v1/../pyproject.toml").status_code == 404
