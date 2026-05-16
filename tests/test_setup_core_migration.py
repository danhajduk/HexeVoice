from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.persistence import OnboardingStateStore


def test_setup_core_saves_url_when_core_is_unreachable(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=state_path,
                endpoint_registry_path=tmp_path / "endpoint-registry.json",
                voice_intent_registry_path=tmp_path / "voice-intents.json",
                voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
            )
        )
    )

    response = client.put("/api/setup/core", json={"core_base_url": "http://127.0.0.1:9"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["core_base_url"] == "http://127.0.0.1:9/"
    assert payload["reachable"] is False
    assert payload["warnings"]
    assert OnboardingStateStore(path=state_path).load().pre_trust.core_base_url == "http://127.0.0.1:9/"


def test_setup_migration_preflight_uses_node_migration_validation(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "onboarding-state.json",
                endpoint_registry_path=tmp_path / "endpoint-registry.json",
                voice_intent_registry_path=tmp_path / "voice-intents.json",
                voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
            )
        )
    )

    response = client.post(
        "/api/setup/migration/preflight",
        json={"bundle": {"schema_version": 999, "state_files": {}}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "unsupported_migration_schema_version" in " ".join(payload["errors"])


def test_setup_migration_import_rejects_trust_secret_bundle(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "onboarding-state.json",
                endpoint_registry_path=tmp_path / "endpoint-registry.json",
                voice_intent_registry_path=tmp_path / "voice-intents.json",
                voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
            )
        )
    )
    bundle = {
        "schema_version": 1,
        "contains_trust_secrets": True,
        "state_files": {
            "onboarding_state": {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "node_trust_token": "secret-token",
                }
            }
        },
    }

    response = client.post("/api/setup/migration/import", json={"bundle": bundle})

    assert response.status_code == 400
    assert response.json()["detail"] == "migration_bundle_contains_trust_secrets"
