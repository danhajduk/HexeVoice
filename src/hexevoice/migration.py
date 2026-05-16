from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from hexevoice.api.models import NodeMigrationExportRequest, NodeMigrationImportRequest, NodeMigrationImportResponse
from hexevoice.assistant.intent_registry import VoiceIntentState, VoiceIntentStateStore
from hexevoice.config.settings import Settings
from hexevoice.persistence import (
    EndpointRegistryStore,
    OnboardingStateStore,
    PersistedEndpointRegistry,
    PersistedOnboardingState,
)


MIGRATION_SCHEMA_VERSION = 1
TRUST_SECRET_FIELDS = {"node_trust_token", "operational_mqtt_token"}
STT_PROVIDERS = {"deterministic", "openai", "faster_whisper", "external_faster_whisper"}
STT_PROVIDER_CONFIG_FIELDS = {"enabled", "default", "model", "device", "compute_type", "warm_model", "warm_models"}


class NodeMigrationError(ValueError):
    pass


class NodeMigrationService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._onboarding_store = OnboardingStateStore(path=settings.resolved_onboarding_state_path())
        self._endpoint_registry_store = EndpointRegistryStore(path=settings.resolved_endpoint_registry_path())
        self._voice_intent_store = VoiceIntentStateStore(path=settings.resolved_voice_intent_registry_path())
        self._tts_runtime_settings_path = settings.resolved_voice_tts_runtime_config_path()

    def export_bundle(self, payload: NodeMigrationExportRequest) -> dict[str, Any]:
        state_files: dict[str, Any] = {
            "onboarding_state": self._onboarding_store.load().model_dump(mode="json"),
            "endpoint_registry": self._endpoint_registry_store.load().model_dump(mode="json"),
        }

        if self._voice_intent_store.path.exists():
            intent_payload = json.loads(self._voice_intent_store.path.read_text(encoding="utf-8"))
            state_files["voice_intents"] = VoiceIntentState.model_validate(intent_payload).model_dump(mode="json")

        if self._tts_runtime_settings_path.exists():
            state_files["voice_tts_settings"] = self._read_object_file(self._tts_runtime_settings_path)

        stt_settings = self._stt_settings_payload(state_files["onboarding_state"])
        if stt_settings:
            state_files["voice_stt_settings"] = stt_settings

        warnings = [
            "Bundle can contain trust tokens and should be handled like a secret.",
            "Large model, media, firmware, log, and audio-history folders are not included.",
        ]
        if not payload.include_trust_secrets:
            state_files["onboarding_state"] = self._redact_trust_secrets(state_files["onboarding_state"])
            warnings.append("Trust secrets were redacted; imported node will need trust reactivation.")

        onboarding_state = state_files["onboarding_state"]
        trust_activation = onboarding_state.get("trust_activation", {}) if isinstance(onboarding_state, dict) else {}

        return {
            "schema_version": MIGRATION_SCHEMA_VERSION,
            "exported_at": datetime.now(UTC).isoformat(),
            "source": {
                "node_name": self._settings.node_name,
                "node_type": self._settings.node_type,
                "node_id": trust_activation.get("node_id"),
                "api_base_url": self._settings.public_api_base_url,
                "ui_endpoint": self._settings.public_ui_base_url,
            },
            "contains_trust_secrets": payload.include_trust_secrets,
            "state_files": state_files,
            "warnings": warnings,
        }

    def import_bundle(self, payload: NodeMigrationImportRequest) -> NodeMigrationImportResponse:
        bundle = payload.bundle
        if not isinstance(bundle, dict):
            raise NodeMigrationError("migration_bundle_must_be_object")
        if bundle.get("schema_version") != MIGRATION_SCHEMA_VERSION:
            raise NodeMigrationError("unsupported_migration_schema_version")

        state_files = bundle.get("state_files")
        if not isinstance(state_files, dict):
            raise NodeMigrationError("migration_state_files_missing")

        imported: list[str] = []
        warnings: list[str] = []
        node_id: str | None = None
        core_base_url: str | None = None
        api_base_url: str | None = None
        ui_endpoint: str | None = None

        onboarding_payload = state_files.get("onboarding_state")
        if onboarding_payload is not None:
            onboarding_state = PersistedOnboardingState.model_validate(onboarding_payload)
            onboarding_state = self._apply_destination_overrides(onboarding_state, payload)
            saved = self._onboarding_store.save(onboarding_state)
            node_id = saved.trust_activation.node_id
            core_base_url = saved.pre_trust.core_base_url
            api_base_url = saved.pre_trust.api_base_url
            ui_endpoint = saved.pre_trust.ui_endpoint
            imported.append("onboarding_state")

            if saved.trust_activation.trust_status == "trusted" and not saved.trust_activation.node_trust_token:
                warnings.append("Imported state is trusted but missing node_trust_token; run trust activation again.")

        endpoint_registry_payload = state_files.get("endpoint_registry")
        if endpoint_registry_payload is not None:
            registry = PersistedEndpointRegistry.model_validate(endpoint_registry_payload)
            self._endpoint_registry_store.save(registry)
            imported.append("endpoint_registry")

        voice_intents_payload = state_files.get("voice_intents")
        if voice_intents_payload is not None:
            intents = VoiceIntentState.model_validate(voice_intents_payload)
            self._voice_intent_store.save(intents)
            imported.append("voice_intents")

        tts_settings_payload = state_files.get("voice_tts_settings")
        if tts_settings_payload is not None:
            if not isinstance(tts_settings_payload, dict):
                raise NodeMigrationError("voice_tts_settings_must_be_object")
            self._write_json_file(self._tts_runtime_settings_path, tts_settings_payload)
            imported.append("voice_tts_settings")

        stt_settings_payload = state_files.get("voice_stt_settings")
        if stt_settings_payload is not None:
            stt_settings = self._validate_stt_settings(stt_settings_payload)
            current_state = self._onboarding_store.load()
            saved = self._onboarding_store.save(self._merge_stt_settings(current_state, stt_settings))
            node_id = node_id or saved.trust_activation.node_id
            core_base_url = core_base_url or saved.pre_trust.core_base_url
            api_base_url = api_base_url or saved.pre_trust.api_base_url
            ui_endpoint = ui_endpoint or saved.pre_trust.ui_endpoint
            imported.append("voice_stt_settings")
            warnings.append("Imported STT settings may require model downloads and an STT service restart on this host.")
            if stt_settings.get("device") == "cuda":
                warnings.append("Imported STT settings request CUDA; verify GPU support before starting the STT service.")

        if not imported:
            raise NodeMigrationError("migration_bundle_contains_no_supported_state_files")

        warnings.append("Copy model, firmware, endpoint media, and service env files separately if this node uses them.")
        return NodeMigrationImportResponse(
            imported=True,
            files_imported=imported,
            node_id=node_id,
            core_base_url=core_base_url,
            api_base_url=api_base_url,
            ui_endpoint=ui_endpoint,
            warnings=warnings,
        )

    def _apply_destination_overrides(
        self,
        state: PersistedOnboardingState,
        payload: NodeMigrationImportRequest,
    ) -> PersistedOnboardingState:
        updates: dict[str, Any] = {}
        if payload.destination_core_base_url is not None:
            updates["core_base_url"] = str(payload.destination_core_base_url)
        if payload.destination_api_base_url is not None:
            updates["api_base_url"] = str(payload.destination_api_base_url)
        if payload.destination_ui_endpoint is not None:
            updates["ui_endpoint"] = str(payload.destination_ui_endpoint)
        if payload.destination_hostname:
            updates["hostname"] = payload.destination_hostname
        if not updates:
            return state
        return state.model_copy(update={"pre_trust": state.pre_trust.model_copy(update=updates)})

    @staticmethod
    def _redact_trust_secrets(onboarding_state: dict[str, Any]) -> dict[str, Any]:
        redacted = json.loads(json.dumps(onboarding_state))
        trust_activation = redacted.get("trust_activation")
        if isinstance(trust_activation, dict):
            for field in TRUST_SECRET_FIELDS:
                if field in trust_activation:
                    trust_activation[field] = None

        onboarding_session = redacted.get("onboarding_session")
        pending_activation = onboarding_session.get("pending_activation") if isinstance(onboarding_session, dict) else None
        if isinstance(pending_activation, dict):
            for field in TRUST_SECRET_FIELDS:
                if field in pending_activation:
                    pending_activation[field] = None
        return redacted

    def _stt_settings_payload(self, onboarding_state: dict[str, Any]) -> dict[str, Any] | None:
        provider = str(self._settings.voice_stt_provider or "").strip()
        if not provider:
            return None
        provider_setup = onboarding_state.get("provider_setup", {}) if isinstance(onboarding_state, dict) else {}
        provider_configs = provider_setup.get("provider_configs", {}) if isinstance(provider_setup, dict) else {}
        provider_config = provider_configs.get(provider, {}) if isinstance(provider_configs, dict) else {}
        if not isinstance(provider_config, dict):
            provider_config = {}
        if provider == "deterministic" and not provider_config:
            return None

        warm_models = provider_config.get("warm_models") if isinstance(provider_config.get("warm_models"), list) else []
        payload: dict[str, Any] = {
            "provider": provider,
            "enabled": provider in (provider_setup.get("enabled_providers", []) if isinstance(provider_setup, dict) else []),
            "default": provider == (provider_setup.get("default_provider") if isinstance(provider_setup, dict) else None),
            "model": str(provider_config.get("model") or self._settings.voice_stt_faster_whisper_model).strip(),
            "device": str(provider_config.get("device") or self._settings.voice_stt_faster_whisper_device).strip(),
            "compute_type": str(
                provider_config.get("compute_type") or self._settings.voice_stt_faster_whisper_compute_type
            ).strip(),
            "warm_model": bool(provider_config.get("warm_model", self._settings.voice_stt_preload)),
            "warm_models": [str(model).strip() for model in warm_models if str(model).strip()],
            "preload": self._settings.voice_stt_preload,
            "service": {
                "transport": self._settings.voice_stt_service_transport,
                "base_url": self._settings.voice_stt_service_base_url,
                "host": self._settings.voice_stt_service_host,
                "port": self._settings.voice_stt_service_port,
                "socket_path": str(self._settings.voice_stt_service_socket_path)
                if self._settings.voice_stt_service_socket_path is not None
                else None,
                "service_id": self._settings.voice_stt_service_id,
                "container_name": self._settings.voice_stt_container_name,
                "control_script": str(self._settings.voice_stt_control_script),
            },
            "faster_whisper": {
                "language": self._settings.voice_stt_faster_whisper_language,
                "beam_size": self._settings.voice_stt_faster_whisper_beam_size,
                "best_of": self._settings.voice_stt_faster_whisper_best_of,
                "without_timestamps": self._settings.voice_stt_faster_whisper_without_timestamps,
                "word_timestamps": self._settings.voice_stt_faster_whisper_word_timestamps,
                "max_initial_timestamp": self._settings.voice_stt_faster_whisper_max_initial_timestamp,
                "temp_dir": str(self._settings.voice_stt_faster_whisper_temp_dir)
                if self._settings.voice_stt_faster_whisper_temp_dir is not None
                else None,
            },
        }
        return self._validate_stt_settings(payload)

    @staticmethod
    def _validate_stt_settings(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise NodeMigrationError("voice_stt_settings_must_be_object")
        provider = str(payload.get("provider") or "").strip()
        if provider not in STT_PROVIDERS:
            raise NodeMigrationError("voice_stt_settings_provider_invalid")

        validated: dict[str, Any] = {"provider": provider}
        for key in ("enabled", "default", "warm_model", "preload"):
            if key in payload:
                validated[key] = bool(payload.get(key))
        for key in ("model", "device", "compute_type"):
            value = str(payload.get(key) or "").strip()
            if value:
                validated[key] = value
        warm_models = payload.get("warm_models", [])
        if not isinstance(warm_models, list):
            raise NodeMigrationError("voice_stt_settings_warm_models_must_be_list")
        validated["warm_models"] = [str(model).strip() for model in warm_models if str(model).strip()]

        for section_name in ("service", "faster_whisper"):
            section = payload.get(section_name, {})
            if section is None:
                section = {}
            if not isinstance(section, dict):
                raise NodeMigrationError(f"voice_stt_settings_{section_name}_must_be_object")
            validated[section_name] = json.loads(json.dumps(section))
        return validated

    @staticmethod
    def _merge_stt_settings(state: PersistedOnboardingState, stt_settings: dict[str, Any]) -> PersistedOnboardingState:
        provider = str(stt_settings["provider"])
        provider_config = {
            key: stt_settings[key]
            for key in STT_PROVIDER_CONFIG_FIELDS
            if key in stt_settings and stt_settings[key] not in (None, "", [])
        }
        provider_configs = dict(state.provider_setup.provider_configs)
        provider_configs[provider] = {**provider_configs.get(provider, {}), **provider_config}

        supported = list(state.provider_setup.supported_providers)
        if provider not in supported:
            supported.append(provider)
        enabled = list(state.provider_setup.enabled_providers)
        if stt_settings.get("enabled") and provider not in enabled:
            enabled.append(provider)
        if stt_settings.get("enabled") is False:
            enabled = [item for item in enabled if item != provider]
        default_provider = provider if stt_settings.get("default") else state.provider_setup.default_provider

        return state.model_copy(
            update={
                "provider_setup": state.provider_setup.model_copy(
                    update={
                        "supported_providers": supported,
                        "enabled_providers": enabled,
                        "default_provider": default_provider,
                        "provider_configs": provider_configs,
                    }
                )
            }
        )

    @staticmethod
    def _read_object_file(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise NodeMigrationError(f"{path.name}_must_be_object")
        return payload

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)
