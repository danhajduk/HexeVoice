import json
from pathlib import Path

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
    assert onboarding_state["trust_activation"]["trust_status"] == "reauth_required"
    assert onboarding_state["trust_activation"]["node_trust_token"] is None
    assert onboarding_state["trust_activation"]["operational_mqtt_token"] is None
    assert "voice_intents" in bundle["state_files"]


def test_node_migration_export_rejects_trust_secret_request(tmp_path):
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

    response = client.post("/api/node/migration/export", json={"include_trust_secrets": True})

    assert response.status_code == 400
    assert response.json()["detail"] == "migration_trust_secret_export_not_supported"


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
    bundle = source_client.post("/api/node/migration/export", json={}).json()

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
    assert "re-authorize with Core" in " ".join(payload["warnings"])
    imported_state = OnboardingStateStore(path=destination_path).load()
    assert imported_state.trust_activation.node_trust_token is None
    assert imported_state.trust_activation.operational_mqtt_token is None
    assert imported_state.trust_activation.trust_status == "reauth_required"
    assert imported_state.pre_trust.core_base_url == "http://10.0.0.101:9001/"
    assert imported_state.pre_trust.api_base_url == "http://10.0.0.55:9004/"
    assert imported_state.pre_trust.ui_endpoint == "http://10.0.0.55:8084/"
    assert imported_state.pre_trust.hostname == "voice-new-host"
    tts_settings = json.loads(destination_settings.resolved_voice_tts_runtime_config_path().read_text(encoding="utf-8"))
    assert tts_settings["default_voice"] == "en_US-lessac-medium"


def test_node_migration_import_rejects_trust_secrets_before_writing(tmp_path):
    destination_path = tmp_path / "destination" / "onboarding-state.json"
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=destination_path,
                endpoint_registry_path=tmp_path / "destination" / "endpoint-registry.json",
                voice_intent_registry_path=tmp_path / "destination" / "voice-intents.json",
                voice_tts_runtime_config_path=tmp_path / "destination" / "voice-tts-settings.json",
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
                    "trust_status": "trusted",
                    "node_trust_token": "trust-token-123",
                }
            }
        },
    }

    response = client.post("/api/node/migration/import", json={"bundle": bundle})

    assert response.status_code == 400
    assert response.json()["detail"] == "migration_bundle_contains_trust_secrets"
    assert not destination_path.exists()


def test_node_migration_dry_run_rejects_nested_trust_secrets(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "destination" / "onboarding-state.json",
                endpoint_registry_path=tmp_path / "destination" / "endpoint-registry.json",
                voice_intent_registry_path=tmp_path / "destination" / "voice-intents.json",
                voice_tts_runtime_config_path=tmp_path / "destination" / "voice-tts-settings.json",
            )
        )
    )
    bundle = {
        "schema_version": 1,
        "state_files": {
            "onboarding_state": {
                "onboarding_session": {
                    "pending_activation": {
                        "operational_mqtt_token": "mqtt-token-123",
                    }
                }
            }
        },
    }

    response = client.post("/api/node/migration/import", json={"bundle": bundle, "dry_run": True})

    assert response.status_code == 400
    assert response.json()["detail"] == "migration_bundle_contains_trust_secrets"


def test_node_migration_import_dry_run_does_not_write_state(tmp_path):
    bundle = {
        "schema_version": 1,
        "state_files": {
            "onboarding_state": {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "api_base_url": "http://10.0.0.22:9004",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                    "node_trust_token": None,
                },
            }
        },
    }
    destination_path = tmp_path / "destination" / "onboarding-state.json"
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=destination_path,
                endpoint_registry_path=tmp_path / "destination" / "endpoint-registry.json",
                voice_intent_registry_path=tmp_path / "destination" / "voice-intents.json",
                voice_tts_runtime_config_path=tmp_path / "destination" / "voice-tts-settings.json",
            )
        )
    )

    response = client.post("/api/node/migration/import", json={"bundle": bundle, "dry_run": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported"] is False
    assert payload["files_imported"] == ["onboarding_state"]
    assert "Dry-run only" in " ".join(payload["warnings"])
    assert not destination_path.exists()


def test_node_migration_preflight_reports_missing_docker_and_planned_writes(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    bundle = {
        "schema_version": 1,
        "state_files": {
            "voice_stt_settings": {
                "provider": "external_faster_whisper",
                "model": "small.en",
                "device": "cpu",
                "compute_type": "int8",
            }
        },
    }
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

    response = client.post("/api/node/migration/preflight", json={"bundle": bundle})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["planned_writes"] == ["voice_stt_settings"]
    docker_check = next(check for check in payload["checks"] if check["id"] == "docker")
    assert docker_check["status"] == "fail"
    assert "docker" in " ".join(payload["errors"]).lower()


def test_node_migration_preflight_rejects_invalid_bundle(tmp_path):
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

    response = client.post("/api/node/migration/preflight", json={"bundle": {"schema_version": 999, "state_files": {}}})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "unsupported_migration_schema_version" in " ".join(payload["errors"])


def test_node_migration_backup_creates_manifest_and_redacts_trust_by_default(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    OnboardingStateStore(path=state_path).save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                    "node_trust_token": "secret-token",
                }
            }
        )
    )
    client = TestClient(
        create_app(
            Settings(
                runtime_dir=tmp_path / "runtime",
                onboarding_state_path=state_path,
                endpoint_registry_path=tmp_path / "endpoint-registry.json",
                voice_intent_registry_path=tmp_path / "voice-intents.json",
                voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
            )
        )
    )

    response = client.post("/api/node/migration/backup", json={"label": "before-move"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["contains_trust_secrets"] is False
    manifest = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
    bundle = json.loads(Path(payload["bundle_path"]).read_text(encoding="utf-8"))
    assert manifest["backup_id"] == payload["backup_id"]
    assert manifest["contains_trust_secrets"] is False
    assert bundle["state_files"]["onboarding_state"]["trust_activation"]["node_trust_token"] is None
    assert "manifest.json" in payload["files"]


def test_node_migration_restore_validates_backup_before_writing(tmp_path):
    source_path = tmp_path / "source" / "onboarding-state.json"
    OnboardingStateStore(path=source_path).save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                    "node_trust_token": "secret-token",
                }
            }
        )
    )
    backup_settings = Settings(
        runtime_dir=tmp_path / "source-runtime",
        onboarding_state_path=source_path,
        endpoint_registry_path=tmp_path / "source" / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "source" / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "source" / "voice-tts-settings.json",
    )
    backup = TestClient(create_app(backup_settings)).post(
        "/api/node/migration/backup",
        json={"label": "rollback"},
    ).json()

    destination_path = tmp_path / "destination" / "onboarding-state.json"
    restore_client = TestClient(
        create_app(
            Settings(
                runtime_dir=tmp_path / "source-runtime",
                onboarding_state_path=destination_path,
                endpoint_registry_path=tmp_path / "destination" / "endpoint-registry.json",
                voice_intent_registry_path=tmp_path / "destination" / "voice-intents.json",
                voice_tts_runtime_config_path=tmp_path / "destination" / "voice-tts-settings.json",
            )
        )
    )

    dry_run = restore_client.post("/api/node/migration/restore", json={"backup_id": backup["backup_id"], "dry_run": True})

    assert dry_run.status_code == 200
    assert dry_run.json()["imported"] is False
    assert not destination_path.exists()

    restored = restore_client.post("/api/node/migration/restore", json={"backup_id": backup["backup_id"]})

    assert restored.status_code == 200
    assert restored.json()["imported"] is True
    restored_state = OnboardingStateStore(path=destination_path).load()
    assert restored_state.trust_activation.node_trust_token is None
    assert restored_state.trust_activation.trust_status == "reauth_required"


def test_node_migration_backup_rejects_trust_secret_request(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                runtime_dir=tmp_path / "runtime",
                onboarding_state_path=tmp_path / "onboarding-state.json",
                endpoint_registry_path=tmp_path / "endpoint-registry.json",
                voice_intent_registry_path=tmp_path / "voice-intents.json",
                voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
            )
        )
    )

    response = client.post("/api/node/migration/backup", json={"include_trust_secrets": True})

    assert response.status_code == 400
    assert response.json()["detail"] == "migration_trust_secret_backup_not_supported"


def test_node_migration_export_imports_stt_provider_settings(tmp_path):
    source_path = tmp_path / "source" / "onboarding-state.json"
    source_settings = Settings(
        onboarding_state_path=source_path,
        endpoint_registry_path=tmp_path / "source" / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "source" / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "source" / "voice-tts-settings.json",
        voice_stt_provider="external_faster_whisper",
        voice_stt_service_transport="unix",
        voice_stt_service_socket_path=tmp_path / "source" / "sockets" / "stt.sock",
        voice_stt_preload=True,
        voice_stt_faster_whisper_model="base.en",
        voice_stt_faster_whisper_device="cpu",
        voice_stt_faster_whisper_compute_type="int8",
        voice_stt_faster_whisper_temp_dir=tmp_path / "source" / "stt-cache",
    )
    OnboardingStateStore(path=source_path).save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "provider_setup": {
                    "supported_providers": ["voice", "external_faster_whisper"],
                    "enabled_providers": ["voice", "external_faster_whisper"],
                    "default_provider": "voice",
                    "provider_configs": {
                        "external_faster_whisper": {
                            "enabled": True,
                            "profile": "cuda_fast_intent",
                            "fallback_profile": "cuda_accurate_fallback",
                            "model": "small.en",
                            "device": "cuda",
                            "compute_type": "float16",
                            "warm_model": True,
                            "warm_models": ["tiny.en"],
                        }
                    },
                },
            }
        )
    )
    source_client = TestClient(create_app(source_settings))

    bundle = source_client.post("/api/node/migration/export", json={}).json()

    stt_settings = bundle["state_files"]["voice_stt_settings"]
    assert stt_settings["provider"] == "external_faster_whisper"
    assert stt_settings["profile"] == "cuda_fast_intent"
    assert stt_settings["fallback_profile"] == "cuda_accurate_fallback"
    assert stt_settings["model"] == "small.en"
    assert stt_settings["device"] == "cuda"
    assert stt_settings["compute_type"] == "float16"
    assert stt_settings["warm_models"] == ["tiny.en"]
    assert stt_settings["preload"] is True
    assert stt_settings["service"]["transport"] == "unix"
    assert stt_settings["faster_whisper"]["temp_dir"] == str(tmp_path / "source" / "stt-cache")

    destination_path = tmp_path / "destination" / "onboarding-state.json"
    destination_settings = Settings(
        onboarding_state_path=destination_path,
        endpoint_registry_path=tmp_path / "destination" / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "destination" / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "destination" / "voice-tts-settings.json",
    )
    response = TestClient(create_app(destination_settings)).post(
        "/api/node/migration/import",
        json={"bundle": {"schema_version": 1, "state_files": {"voice_stt_settings": stt_settings}}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["files_imported"] == ["voice_stt_settings"]
    assert "model downloads" in " ".join(payload["warnings"])
    assert "CUDA" in " ".join(payload["warnings"])
    imported = OnboardingStateStore(path=destination_path).load()
    assert imported.provider_setup.supported_providers == ["external_faster_whisper"]
    assert imported.provider_setup.enabled_providers == ["external_faster_whisper"]
    assert imported.provider_setup.provider_configs["external_faster_whisper"]["model"] == "small.en"
    assert imported.provider_setup.provider_configs["external_faster_whisper"]["profile"] == "cuda_fast_intent"
    assert imported.provider_setup.provider_configs["external_faster_whisper"]["fallback_profile"] == "cuda_accurate_fallback"
    assert imported.provider_setup.provider_configs["external_faster_whisper"]["device"] == "cuda"
    assert imported.provider_setup.provider_configs["external_faster_whisper"]["compute_type"] == "float16"
    assert imported.provider_setup.provider_configs["external_faster_whisper"]["warm_models"] == ["tiny.en"]


def test_node_migration_rejects_malformed_stt_settings(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "onboarding-state.json",
        endpoint_registry_path=tmp_path / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
    )
    response = TestClient(create_app(settings)).post(
        "/api/node/migration/import",
        json={
            "bundle": {
                "schema_version": 1,
                "state_files": {
                    "voice_stt_settings": {
                        "provider": "external_faster_whisper",
                        "warm_models": "tiny.en",
                    }
                },
            }
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "voice_stt_settings_warm_models_must_be_list"


def test_node_migration_export_omits_custom_legacy_stt_profile(tmp_path):
    source_path = tmp_path / "source" / "onboarding-state.json"
    source_settings = Settings(
        onboarding_state_path=source_path,
        endpoint_registry_path=tmp_path / "source" / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "source" / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "source" / "voice-tts-settings.json",
        voice_stt_provider="external_faster_whisper",
        voice_stt_faster_whisper_model="small.en",
        voice_stt_faster_whisper_device="cpu",
        voice_stt_faster_whisper_compute_type="int8",
    )
    OnboardingStateStore(path=source_path).save(
        PersistedOnboardingState.model_validate(
            {
                "provider_setup": {
                    "supported_providers": ["voice", "external_faster_whisper"],
                    "enabled_providers": ["voice", "external_faster_whisper"],
                    "provider_configs": {
                        "external_faster_whisper": {
                            "enabled": True,
                            "model": "small.en",
                            "device": "cpu",
                            "compute_type": "int8",
                            "warm_model": True,
                        }
                    },
                },
            }
        )
    )

    response = TestClient(create_app(source_settings)).post("/api/node/migration/export", json={})

    assert response.status_code == 200
    stt_settings = response.json()["state_files"]["voice_stt_settings"]
    assert "profile" not in stt_settings
    assert stt_settings["model"] == "small.en"
    assert stt_settings["device"] == "cpu"
    assert stt_settings["compute_type"] == "int8"


def test_node_migration_rejects_unknown_stt_profile_without_500(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "onboarding-state.json",
        endpoint_registry_path=tmp_path / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
    )
    response = TestClient(create_app(settings)).post(
        "/api/node/migration/import",
        json={
            "bundle": {
                "schema_version": 1,
                "state_files": {
                    "voice_stt_settings": {
                        "provider": "external_faster_whisper",
                        "profile": "not_a_real_profile",
                    }
                },
            }
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "voice_stt_settings_profile_invalid"


def test_node_migration_export_imports_tts_provider_settings(tmp_path):
    source_path = tmp_path / "source" / "onboarding-state.json"
    source_settings = Settings(
        onboarding_state_path=source_path,
        endpoint_registry_path=tmp_path / "source" / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "source" / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "source" / "voice-tts-settings.json",
        voice_tts_provider="piper",
        voice_tts_piper_transport="unix",
        voice_tts_piper_socket_path=tmp_path / "source" / "sockets" / "tts.sock",
        voice_tts_output_sample_rate_hz=48000,
        voice_tts_endpoint_voices="esp-pe-1=en_US-lessac-high",
        voice_tts_endpoint_sample_rates="esp-pe-1=48000,esp-box-1=16000",
        voice_tts_conversion_sample_rates="48000,22050,16000",
        voice_tts_conversion_policy="endpoint_required_sync",
        piper_tts_model_dir=tmp_path / "source" / "piper-models",
        piper_tts_warm_voices="en_US-lessac-high,en_GB-jenny_dioco-medium",
    )
    OnboardingStateStore(path=source_path).save(
        PersistedOnboardingState.model_validate(
            {
                "provider_setup": {
                    "supported_providers": ["voice", "piper"],
                    "enabled_providers": ["voice", "piper"],
                    "default_provider": "piper",
                    "provider_configs": {
                        "piper": {
                            "enabled": True,
                            "default": True,
                            "model": "en_GB-jenny_dioco-medium",
                            "default_voice": "en_GB-jenny_dioco-medium",
                            "warm_models": ["en_US-lessac-high"],
                        }
                    },
                },
            }
        )
    )
    source_settings.resolved_voice_tts_runtime_config_path().parent.mkdir(parents=True, exist_ok=True)
    source_settings.resolved_voice_tts_runtime_config_path().write_text(
        json.dumps({"default_voice": "en_GB-jenny_dioco-medium", "restart_required": True}),
        encoding="utf-8",
    )

    bundle = TestClient(create_app(source_settings)).post("/api/node/migration/export", json={}).json()

    tts_provider_settings = bundle["state_files"]["voice_tts_provider_settings"]
    assert bundle["state_files"]["voice_tts_settings"]["restart_required"] is True
    assert tts_provider_settings["provider"] == "piper"
    assert tts_provider_settings["default"] is True
    assert tts_provider_settings["model"] == "en_GB-jenny_dioco-medium"
    assert tts_provider_settings["default_voice"] == "en_GB-jenny_dioco-medium"
    assert tts_provider_settings["warm_models"] == ["en_US-lessac-high", "en_GB-jenny_dioco-medium"]
    assert tts_provider_settings["endpoint_voices"] == {"esp-pe-1": "en_US-lessac-high"}
    assert tts_provider_settings["endpoint_sample_rates"] == {"esp-pe-1": 48000, "esp-box-1": 16000}
    assert tts_provider_settings["conversion_sample_rates"] == {"48k": 48000, "22050": 22050, "16k": 16000}
    assert tts_provider_settings["conversion_policy"] == "endpoint_required_sync"
    assert tts_provider_settings["piper"]["model_dir"] == str(tmp_path / "source" / "piper-models")

    destination_path = tmp_path / "destination" / "onboarding-state.json"
    destination_settings = Settings(
        onboarding_state_path=destination_path,
        endpoint_registry_path=tmp_path / "destination" / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "destination" / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "destination" / "voice-tts-settings.json",
    )
    response = TestClient(create_app(destination_settings)).post(
        "/api/node/migration/import",
        json={
            "bundle": {
                "schema_version": 1,
                "state_files": {"voice_tts_provider_settings": tts_provider_settings},
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["files_imported"] == ["voice_tts_provider_settings"]
    assert "Piper voice downloads" in " ".join(payload["warnings"])
    imported = OnboardingStateStore(path=destination_path).load()
    assert imported.provider_setup.supported_providers == ["piper"]
    assert imported.provider_setup.enabled_providers == ["piper"]
    assert imported.provider_setup.default_provider == "piper"
    assert imported.provider_setup.provider_configs["piper"]["default_voice"] == "en_GB-jenny_dioco-medium"
    assert imported.provider_setup.provider_configs["piper"]["warm_models"] == [
        "en_US-lessac-high",
        "en_GB-jenny_dioco-medium",
    ]


def test_node_migration_rejects_malformed_tts_provider_settings(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "onboarding-state.json",
        endpoint_registry_path=tmp_path / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
    )
    response = TestClient(create_app(settings)).post(
        "/api/node/migration/import",
        json={
            "bundle": {
                "schema_version": 1,
                "state_files": {
                    "voice_tts_provider_settings": {
                        "provider": "piper",
                        "warm_models": "en_US-lessac-high",
                    }
                },
            }
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "voice_tts_provider_settings_warm_models_must_be_list"


def test_node_migration_export_imports_wake_settings_with_hexe_normalization(tmp_path):
    source_path = tmp_path / "source" / "onboarding-state.json"
    source_settings = Settings(
        onboarding_state_path=source_path,
        endpoint_registry_path=tmp_path / "source" / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "source" / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "source" / "voice-tts-settings.json",
        voice_wake_provider="supervised_openwakeword",
        voice_wake_models="Hexa,Nora",
        voice_wake_threshold=0.7,
        voice_wake_auto_download_models=True,
        voice_wake_preload=True,
        voice_wake_service_host="10.0.0.38",
        voice_wake_service_port=10400,
        voice_wake_service_timeout_s=0.25,
        voice_wake_recordings_enabled=True,
        voice_wake_recording_dir=tmp_path / "source" / "wake-recordings",
    )
    OnboardingStateStore(path=source_path).save(
        PersistedOnboardingState.model_validate(
            {
                "provider_setup": {
                    "supported_providers": ["voice", "wake"],
                    "enabled_providers": ["voice", "wake"],
                    "default_provider": "voice",
                    "provider_configs": {
                        "wake": {
                            "enabled": True,
                            "default_wakeword": "Hexa",
                            "warm_model": True,
                            "warm_models": ["Hexa", "Nora"],
                        }
                    },
                },
            }
        )
    )

    bundle = TestClient(create_app(source_settings)).post("/api/node/migration/export", json={}).json()

    wake_settings = bundle["state_files"]["voice_wake_settings"]
    assert wake_settings["provider"] == "supervised_openwakeword"
    assert wake_settings["default_wakeword"] == "Hexe"
    assert wake_settings["models"] == ["Hexe", "Nora"]
    assert wake_settings["warm_models"] == ["Hexe", "Nora"]
    assert wake_settings["threshold"] == 0.7
    assert wake_settings["auto_download_models"] is True
    assert wake_settings["preload"] is True
    assert wake_settings["service"]["host"] == "10.0.0.38"
    assert wake_settings["recordings"]["enabled"] is True

    destination_path = tmp_path / "destination" / "onboarding-state.json"
    destination_settings = Settings(
        onboarding_state_path=destination_path,
        endpoint_registry_path=tmp_path / "destination" / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "destination" / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "destination" / "voice-tts-settings.json",
    )
    response = TestClient(create_app(destination_settings)).post(
        "/api/node/migration/import",
        json={
            "bundle": {
                "schema_version": 1,
                "state_files": {"voice_wake_settings": wake_settings},
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["files_imported"] == ["voice_wake_settings"]
    assert "wake model download/copy" in " ".join(payload["warnings"])
    imported = OnboardingStateStore(path=destination_path).load()
    assert imported.provider_setup.supported_providers == ["wake"]
    assert imported.provider_setup.enabled_providers == ["wake"]
    assert imported.provider_setup.provider_configs["wake"]["provider"] == "supervised_openwakeword"
    assert imported.provider_setup.provider_configs["wake"]["default_wakeword"] == "Hexe"
    assert imported.provider_setup.provider_configs["wake"]["model"] == "Hexe"
    assert imported.provider_setup.provider_configs["wake"]["warm_models"] == ["Hexe", "Nora"]


def test_node_migration_rejects_malformed_wake_settings(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "onboarding-state.json",
        endpoint_registry_path=tmp_path / "endpoint-registry.json",
        voice_intent_registry_path=tmp_path / "voice-intents.json",
        voice_tts_runtime_config_path=tmp_path / "voice-tts-settings.json",
    )
    response = TestClient(create_app(settings)).post(
        "/api/node/migration/import",
        json={
            "bundle": {
                "schema_version": 1,
                "state_files": {
                    "voice_wake_settings": {
                        "provider": "supervised_openwakeword",
                        "models": "Hexe",
                    }
                },
            }
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "voice_wake_settings_models_must_be_list"
