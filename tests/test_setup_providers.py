from fastapi.testclient import TestClient

from hexevoice.api.models import ServiceStatusResponse
from hexevoice.config.settings import Settings
from hexevoice.main import create_app, setup_provider_action_sequence
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState
from hexevoice.runtime.service import NodeRuntimeService


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


def test_setup_provider_status_blocks_missing_selected_assets(tmp_path):
    settings = trusted_settings(tmp_path)
    settings = settings.model_copy(update={"voice_tts_provider": "piper", "piper_tts_model_dir": tmp_path / "piper-models"})
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/setup/providers/config",
        json={
            "enabled_providers": ["voice", "piper"],
            "default_provider": "voice",
            "provider_configs": {
                "piper": {
                    "default_voice": "en_US-kathleen-low",
                    "warm_models": ["en_US-kathleen-low"],
                }
            },
        },
    )
    assert response.status_code == 200

    status = client.get("/api/setup/providers/status").json()

    assert status["continue_blocked"] is True
    assert any(blocker.startswith("selected_asset_") and ":piper:en_US-kathleen-low" in blocker for blocker in status["blockers"])


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


def test_setup_provider_status_reports_docker_permission_blocker(tmp_path, monkeypatch):
    settings = trusted_settings(tmp_path).model_copy(
        update={"voice_tts_provider": "piper", "piper_tts_model_dir": tmp_path / "piper-models"}
    )

    def fake_service_status_payload(self):
        return ServiceStatusResponse(
            backend="running",
            frontend="defined",
            scheduler="not_started",
            piper_tts="not_created",
            components=[
                {
                    "component_id": "tts",
                    "label": "TTS Engine",
                    "status": "not_created",
                    "healthy": False,
                    "provider": "piper",
                    "service_id": "piper_tts",
                    "model": "en_US-kathleen-low",
                    "restart_target": "piper_tts",
                    "resource_usage": {
                        "process": {
                            "error": (
                                "permission denied while trying to connect to the Docker daemon socket "
                                "at unix:///var/run/docker.sock"
                            )
                        }
                    },
                }
            ],
        )

    monkeypatch.setattr(NodeRuntimeService, "service_status_payload", fake_service_status_payload)
    client = TestClient(create_app(settings))
    response = client.post(
        "/api/setup/providers/config",
        json={
            "enabled_providers": ["voice", "piper"],
            "default_provider": "voice",
            "provider_configs": {"piper": {"default_voice": "en_US-kathleen-low"}},
        },
    )
    assert response.status_code == 200

    status = client.get("/api/setup/providers/status").json()

    assert status["provider_states"][1]["state"] == "blocked"
    assert "piper_docker_permission_denied" in status["blockers"]
    assert "selected_asset_missing:piper:en_US-kathleen-low" in status["blockers"]
    assert "selected_asset_failed:piper:en_US-kathleen-low" not in status["blockers"]


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
    assert payload["declaration_allowed"] is False
    assert "wake_provider_required" in payload["blocking_reasons"]


def test_setup_provider_config_requires_selected_stt_tts_wake_when_runtime_enabled(tmp_path):
    settings = trusted_settings(tmp_path).model_copy(
        update={
            "voice_stt_provider": "external_faster_whisper",
            "voice_tts_provider": "piper",
            "voice_wake_provider": "supervised_openwakeword",
        }
    )
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/setup/providers/config",
        json={"enabled_providers": ["voice"], "default_provider": "voice"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["declaration_allowed"] is False
    assert payload["blocking_reasons"] == ["stt_provider_required", "tts_provider_required", "wake_provider_required"]


def test_setup_provider_config_normalizes_openwakeword_alias(tmp_path):
    settings = trusted_settings(tmp_path).model_copy(update={"voice_wake_provider": "supervised_openwakeword"})
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/setup/providers/config",
        json={
            "enabled_providers": ["voice", "openwakeword"],
            "default_provider": "voice",
            "provider_configs": {"openwakeword": {"default_wakeword": "Hexe", "threshold": 0.65}},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "supervised_openwakeword" in payload["enabled_providers"]
    assert "wake_provider_required" not in payload["blocking_reasons"]
    assert payload["provider_configs"]["supervised_openwakeword"]["threshold"] == 0.65


def test_setup_provider_apply_supports_targeted_action(tmp_path):
    client = TestClient(create_app(trusted_settings(tmp_path)))

    response = client.post("/api/setup/providers/apply", json={"target": "not-real", "action": "start"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["actions"][0]["accepted"] is False
    assert payload["actions"][0]["status"] == "unsupported_service"
    assert "status" in payload


def test_setup_provider_action_sequence_supports_recovery_actions():
    assert setup_provider_action_sequence("download-models") == ("download-models",)
    assert setup_provider_action_sequence("preload") == ("preload",)
    assert setup_provider_action_sequence("restart") == ("restart",)
    assert setup_provider_action_sequence("recreate") == ("restart",)
    assert setup_provider_action_sequence("rebuild-env") == ("install", "start")
