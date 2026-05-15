import json

from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def test_node_migration_export_redacts_trust_secrets_by_default(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    OnboardingStateStore(path=state_path).save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "protocol_version": "1.0",
                    "node_nonce": "nonce-1",
                    "api_base_url": "http://10.0.0.22:9004",
                    "ui_endpoint": "http://10.0.0.22:8084",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                    "node_trust_token": "trust-token-123",
                    "operational_mqtt_token": "mqtt-token-123",
                },
            }
        )
    )
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

    response = client.post("/api/node/migration/export", json={})

    assert response.status_code == 200
    bundle = response.json()
    onboarding_state = bundle["state_files"]["onboarding_state"]
    assert bundle["contains_trust_secrets"] is False
    assert onboarding_state["trust_activation"]["node_id"] == "node-voice-123"
    assert onboarding_state["trust_activation"]["node_trust_token"] is None
    assert onboarding_state["trust_activation"]["operational_mqtt_token"] is None
    assert "voice_intents" in bundle["state_files"]


def test_node_migration_import_restores_state_and_applies_destination_overrides(tmp_path):
    source_path = tmp_path / "source" / "onboarding-state.json"
    source_settings = Settings(
        onboarding_state_path=source_path,
        endpoint_registry_path=tmp_path / "source" / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "source" / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "source" / "voice-tts-settings.json",
    )
    OnboardingStateStore(path=source_path).save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "protocol_version": "1.0",
                    "node_nonce": "nonce-1",
                    "api_base_url": "http://10.0.0.22:9004",
                    "ui_endpoint": "http://10.0.0.22:8084",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                    "node_trust_token": "trust-token-123",
                    "operational_mqtt_token": "mqtt-token-123",
                },
                "resume": {
                    "current_step_id": "ready",
                    "last_completed_step_id": "governance_sync",
                },
            }
        )
    )
    source_settings.resolved_voice_tts_runtime_config_path().parent.mkdir(parents=True, exist_ok=True)
    source_settings.resolved_voice_tts_runtime_config_path().write_text(
        json.dumps({"default_voice": "en_US-lessac-medium", "restart_required": True}),
        encoding="utf-8",
    )
    source_client = TestClient(create_app(source_settings))
    bundle = source_client.post(
        "/api/node/migration/export",
        json={"include_trust_secrets": True},
    ).json()

    destination_path = tmp_path / "destination" / "onboarding-state.json"
    destination_settings = Settings(
        onboarding_state_path=destination_path,
        endpoint_registry_path=tmp_path / "destination" / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "destination" / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "destination" / "voice-tts-settings.json",
    )
    destination_client = TestClient(create_app(destination_settings))

    response = destination_client.post(
        "/api/node/migration/import",
        json={
            "bundle": bundle,
            "destination_core_base_url": "http://10.0.0.101:9001",
            "destination_api_base_url": "http://10.0.0.55:9004",
            "destination_ui_endpoint": "http://10.0.0.55:8084",
            "destination_hostname": "voice-new-host",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported"] is True
    assert payload["node_id"] == "node-voice-123"
    assert payload["files_imported"] == ["onboarding_state", "endpoint_registry", "voice_intents", "voice_tts_settings"]
    imported_state = OnboardingStateStore(path=destination_path).load()
    assert imported_state.trust_activation.node_trust_token == "trust-token-123"
    assert imported_state.pre_trust.core_base_url == "http://10.0.0.101:9001/"
    assert imported_state.pre_trust.api_base_url == "http://10.0.0.55:9004/"
    assert imported_state.pre_trust.ui_endpoint == "http://10.0.0.55:8084/"
    assert imported_state.pre_trust.hostname == "voice-new-host"
    tts_settings = json.loads(destination_settings.resolved_voice_tts_runtime_config_path().read_text(encoding="utf-8"))
    assert tts_settings["default_voice"] == "en_US-lessac-medium"
