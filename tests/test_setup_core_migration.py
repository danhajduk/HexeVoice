from fastapi.testclient import TestClient
import httpx

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
    assert payload["core_base_url"] == "http://127.0.0.1:9"
    assert payload["reachable"] is False
    assert payload["warnings"]
    assert OnboardingStateStore(path=state_path).load().pre_trust.core_base_url == "http://127.0.0.1:9"


def test_setup_core_reports_core_metadata_and_supported_paths(monkeypatch, tmp_path):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            if url.endswith("/api/health"):
                return httpx.Response(200, json={"status": "ok", "version": "1.2.3"})
            if url.endswith("/api/system/platform"):
                return httpx.Response(
                    200,
                    json={
                        "core_id": "core-123",
                        "platform_name": "Hexe",
                        "core_name": "Kitchen Core",
                        "ignored": "value",
                    },
                )
            return await self.request("GET", url)

        async def request(self, method, url):
            if url.endswith("/api/unknown"):
                return httpx.Response(404)
            return httpx.Response(401)

    monkeypatch.setattr("hexevoice.main.httpx.AsyncClient", FakeAsyncClient)
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

    response = client.put("/api/setup/core", json={"core_base_url": "http://10.0.0.100"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["core_base_url"] == "http://10.0.0.100:9001"
    assert payload["reachable"] is True
    assert payload["core_version"] == "1.2.3"
    assert payload["core_identity"]["core_id"] == "core-123"
    assert payload["core_identity"]["core_name"] == "Kitchen Core"
    assert payload["registration_supported"] is True
    assert payload["reauth_supported"] is True
    assert payload["supervisor_enrollment_supported"] is True
    assert payload["capability_governance_supported"] is True
    assert payload["metadata"]["probes"]["registration"]["status_code"] == 401


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
