from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def trusted_settings(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    OnboardingStateStore(path=state_path).save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                    "node_trust_token": "token",
                },
                "resume": {"current_step_id": "provider_setup"},
            }
        )
    )
    return Settings(
        onboarding_state_path=state_path,
        endpoint_registry_path=tmp_path / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
    )


def test_setup_provider_status_shape(tmp_path):
    client = TestClient(create_app(trusted_settings(tmp_path)))

    response = client.get("/api/setup/providers/status")

    assert response.status_code == 200
    payload = response.json()
    assert "provider_setup" in payload
    assert "services" in payload
    assert "provider_states" in payload
    assert "apply_plan" in payload
    assert "asset_progress" in payload
    assert "cuda_profile" in payload
    assert "supervisor_registration" in payload
    assert "continue_blocked" in payload


def test_setup_provider_config_saves_selection(tmp_path):
    client = TestClient(create_app(trusted_settings(tmp_path)))

    response = client.post(
        "/api/setup/providers/config",
        json={"enabled_providers": ["voice"], "default_provider": "voice"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["enabled_providers"] == ["voice"]


def test_setup_provider_apply_supports_targeted_action(tmp_path):
    client = TestClient(create_app(trusted_settings(tmp_path)))

    response = client.post("/api/setup/providers/apply", json={"target": "not-real", "action": "start"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["actions"][0]["accepted"] is False
    assert payload["actions"][0]["status"] == "unsupported_service"
    assert "status" in payload
