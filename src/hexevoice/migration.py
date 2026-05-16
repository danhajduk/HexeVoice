from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from hexevoice.api.models import NodeMigrationExportRequest, NodeMigrationImportRequest, NodeMigrationImportResponse
from hexevoice.assistant.intent_registry import VoiceIntentState, VoiceIntentStateStore
from hexevoice.config.settings import Settings, parse_tts_conversion_sample_rates
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
TTS_PROVIDERS = {"deterministic", "openai", "piper"}
TTS_PROVIDER_CONFIG_FIELDS = {"enabled", "default", "model", "warm_models", "default_voice"}
WAKE_PROVIDERS = {"deterministic", "openwakeword", "supervised_openwakeword"}
WAKE_PROVIDER_CONFIG_FIELDS = {"enabled", "default_wakeword", "model", "warm_model", "warm_models"}


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

        tts_provider_settings = self._tts_provider_settings_payload(state_files["onboarding_state"])
        if tts_provider_settings:
            state_files["voice_tts_provider_settings"] = tts_provider_settings

        stt_settings = self._stt_settings_payload(state_files["onboarding_state"])
        if stt_settings:
            state_files["voice_stt_settings"] = stt_settings

        wake_settings = self._wake_settings_payload(state_files["onboarding_state"])
        if wake_settings:
            state_files["voice_wake_settings"] = wake_settings

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

        tts_provider_settings_payload = state_files.get("voice_tts_provider_settings")
        if tts_provider_settings_payload is not None:
            tts_provider_settings = self._validate_tts_provider_settings(tts_provider_settings_payload)
            current_state = self._onboarding_store.load()
            saved = self._onboarding_store.save(self._merge_tts_provider_settings(current_state, tts_provider_settings))
            node_id = node_id or saved.trust_activation.node_id
            core_base_url = core_base_url or saved.pre_trust.core_base_url
            api_base_url = api_base_url or saved.pre_trust.api_base_url
            ui_endpoint = ui_endpoint or saved.pre_trust.ui_endpoint
            imported.append("voice_tts_provider_settings")
            warnings.append("Imported TTS provider settings may require Piper voice downloads, warmup, and service restart.")

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

        wake_settings_payload = state_files.get("voice_wake_settings")
        if wake_settings_payload is not None:
            wake_settings = self._validate_wake_settings(wake_settings_payload)
            current_state = self._onboarding_store.load()
            saved = self._onboarding_store.save(self._merge_wake_settings(current_state, wake_settings))
            node_id = node_id or saved.trust_activation.node_id
            core_base_url = core_base_url or saved.pre_trust.core_base_url
            api_base_url = api_base_url or saved.pre_trust.api_base_url
            ui_endpoint = ui_endpoint or saved.pre_trust.ui_endpoint
            imported.append("voice_wake_settings")
            warnings.append("Imported wake settings may require wake model download/copy and wake service restart.")

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

    def _tts_provider_settings_payload(self, onboarding_state: dict[str, Any]) -> dict[str, Any] | None:
        provider = str(self._settings.voice_tts_provider or "").strip()
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
        warm_voices = [*self._settings.resolved_piper_tts_warm_voices()]
        for model in warm_models:
            cleaned = str(model).strip()
            if cleaned and cleaned not in warm_voices:
                warm_voices.append(cleaned)
        default_voice = str(
            provider_config.get("default_voice")
            or self._settings.voice_tts_piper_voice
            or self._settings.voice_tts_voice
            or ""
        ).strip()
        payload: dict[str, Any] = {
            "provider": provider,
            "enabled": provider in (provider_setup.get("enabled_providers", []) if isinstance(provider_setup, dict) else []),
            "default": provider == (provider_setup.get("default_provider") if isinstance(provider_setup, dict) else None),
            "model": str(provider_config.get("model") or self._settings.voice_tts_model).strip(),
            "default_voice": default_voice,
            "warm_models": warm_voices,
            "output_sample_rate_hz": self._settings.voice_tts_output_sample_rate_hz,
            "endpoint_voices": self._settings.resolved_voice_tts_endpoint_voices(),
            "endpoint_sample_rates": self._settings.resolved_voice_tts_endpoint_sample_rates(),
            "conversion_sample_rates": parse_tts_conversion_sample_rates(self._settings.voice_tts_conversion_sample_rates),
            "conversion_policy": self._settings.resolved_voice_tts_conversion_policy(),
            "piper": {
                "transport": self._settings.voice_tts_piper_transport,
                "base_url": self._settings.voice_tts_piper_base_url,
                "host": self._settings.voice_tts_piper_service_host,
                "port": self._settings.voice_tts_piper_service_port,
                "socket_path": str(self._settings.voice_tts_piper_socket_path)
                if self._settings.voice_tts_piper_socket_path is not None
                else None,
                "synthesize_path": self._settings.voice_tts_piper_synthesize_path,
                "model_dir": str(self._settings.resolved_piper_tts_model_dir()),
                "service_id": self._settings.piper_tts_service_id,
                "container_name": self._settings.piper_tts_container_name,
                "env_path": str(self._settings.piper_tts_env_path),
                "control_script": str(self._settings.piper_tts_control_script),
            },
        }
        return self._validate_tts_provider_settings(payload)

    @staticmethod
    def _validate_tts_provider_settings(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise NodeMigrationError("voice_tts_provider_settings_must_be_object")
        provider = str(payload.get("provider") or "").strip()
        if provider not in TTS_PROVIDERS:
            raise NodeMigrationError("voice_tts_provider_settings_provider_invalid")

        validated: dict[str, Any] = {"provider": provider}
        for key in ("enabled", "default"):
            if key in payload:
                validated[key] = bool(payload.get(key))
        for key in ("model", "default_voice", "conversion_policy"):
            value = str(payload.get(key) or "").strip()
            if value:
                validated[key] = value
        if "output_sample_rate_hz" in payload:
            try:
                sample_rate = int(payload["output_sample_rate_hz"])
            except (TypeError, ValueError):
                raise NodeMigrationError("voice_tts_provider_settings_output_sample_rate_invalid") from None
            if sample_rate < 0:
                raise NodeMigrationError("voice_tts_provider_settings_output_sample_rate_invalid")
            validated["output_sample_rate_hz"] = sample_rate

        warm_models = payload.get("warm_models", [])
        if not isinstance(warm_models, list):
            raise NodeMigrationError("voice_tts_provider_settings_warm_models_must_be_list")
        validated["warm_models"] = [str(model).strip() for model in warm_models if str(model).strip()]

        for section_name in ("endpoint_voices", "endpoint_sample_rates", "conversion_sample_rates", "piper"):
            section = payload.get(section_name, {})
            if section is None:
                section = {}
            if not isinstance(section, dict):
                raise NodeMigrationError(f"voice_tts_provider_settings_{section_name}_must_be_object")
            validated[section_name] = json.loads(json.dumps(section))
        return validated

    def _wake_settings_payload(self, onboarding_state: dict[str, Any]) -> dict[str, Any] | None:
        provider = str(self._settings.voice_wake_provider or "").strip()
        if not provider:
            return None
        provider_setup = onboarding_state.get("provider_setup", {}) if isinstance(onboarding_state, dict) else {}
        provider_configs = provider_setup.get("provider_configs", {}) if isinstance(provider_setup, dict) else {}
        wake_config = provider_configs.get("wake", {}) if isinstance(provider_configs, dict) else {}
        if not isinstance(wake_config, dict):
            wake_config = {}

        models = self._normalized_wake_models(self._settings.voice_wake_models)
        if provider in {"deterministic", "openwakeword"} and not wake_config and not models:
            return None
        default_wakeword = str(wake_config.get("default_wakeword") or (models[0] if models else "Hexe")).strip()
        default_wakeword = self._normalize_wake_name(default_wakeword)
        warm_models = wake_config.get("warm_models") if isinstance(wake_config.get("warm_models"), list) else []

        payload: dict[str, Any] = {
            "provider": provider,
            "enabled": "wake" in (provider_setup.get("enabled_providers", []) if isinstance(provider_setup, dict) else []),
            "default_wakeword": default_wakeword,
            "models": models or [default_wakeword],
            "warm_model": bool(wake_config.get("warm_model", self._settings.voice_wake_preload)),
            "warm_models": [self._normalize_wake_name(str(model).strip()) for model in warm_models if str(model).strip()],
            "threshold": self._settings.voice_wake_threshold,
            "auto_download_models": self._settings.voice_wake_auto_download_models,
            "preload": self._settings.voice_wake_preload,
            "enable_speex_noise_suppression": self._settings.voice_wake_enable_speex_noise_suppression,
            "vad_threshold": self._settings.voice_wake_vad_threshold,
            "buffer_ms": self._settings.voice_wake_buffer_ms,
            "prediction_frame_ms": self._settings.voice_wake_prediction_frame_ms,
            "service": {
                "host": self._settings.voice_wake_service_host,
                "port": self._settings.voice_wake_service_port,
                "timeout_s": self._settings.voice_wake_service_timeout_s,
                "service_id": self._settings.openwakeword_service_id,
                "container_name": self._settings.openwakeword_container_name,
                "control_script": str(self._settings.openwakeword_control_script),
            },
            "recordings": {
                "enabled": self._settings.voice_wake_recordings_enabled,
                "recording_dir": str(self._settings.voice_wake_recording_dir)
                if self._settings.voice_wake_recording_dir is not None
                else None,
                "retention_days": self._settings.voice_wake_recording_retention_days,
                "preroll_ms": self._settings.voice_wake_recording_preroll_ms,
            },
        }
        return self._validate_wake_settings(payload)

    @classmethod
    def _normalized_wake_models(cls, raw: str | None) -> list[str]:
        models: list[str] = []
        for item in (raw or "").split(","):
            cleaned = cls._normalize_wake_name(item.strip())
            if cleaned and cleaned not in models:
                models.append(cleaned)
        return models

    @staticmethod
    def _normalize_wake_name(value: str) -> str:
        if value.strip().lower() == "hexa":
            return "Hexe"
        return value

    @classmethod
    def _validate_wake_settings(cls, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise NodeMigrationError("voice_wake_settings_must_be_object")
        provider = str(payload.get("provider") or "").strip()
        if provider not in WAKE_PROVIDERS:
            raise NodeMigrationError("voice_wake_settings_provider_invalid")

        validated: dict[str, Any] = {"provider": provider}
        for key in (
            "enabled",
            "warm_model",
            "auto_download_models",
            "preload",
            "enable_speex_noise_suppression",
        ):
            if key in payload:
                validated[key] = bool(payload.get(key))
        for key in ("default_wakeword",):
            value = cls._normalize_wake_name(str(payload.get(key) or "").strip())
            if value:
                validated[key] = value
        for key in ("threshold", "vad_threshold"):
            if payload.get(key) is None:
                continue
            try:
                value = float(payload[key])
            except (TypeError, ValueError):
                raise NodeMigrationError(f"voice_wake_settings_{key}_invalid") from None
            if value < 0 or value > 1:
                raise NodeMigrationError(f"voice_wake_settings_{key}_invalid")
            validated[key] = value
        for key in ("buffer_ms", "prediction_frame_ms"):
            if payload.get(key) is None:
                continue
            try:
                value = int(payload[key])
            except (TypeError, ValueError):
                raise NodeMigrationError(f"voice_wake_settings_{key}_invalid") from None
            if value < 0:
                raise NodeMigrationError(f"voice_wake_settings_{key}_invalid")
            validated[key] = value

        for list_key in ("models", "warm_models"):
            values = payload.get(list_key, [])
            if not isinstance(values, list):
                raise NodeMigrationError(f"voice_wake_settings_{list_key}_must_be_list")
            validated[list_key] = [
                cls._normalize_wake_name(str(model).strip()) for model in values if str(model).strip()
            ]

        for section_name in ("service", "recordings"):
            section = payload.get(section_name, {})
            if section is None:
                section = {}
            if not isinstance(section, dict):
                raise NodeMigrationError(f"voice_wake_settings_{section_name}_must_be_object")
            validated[section_name] = json.loads(json.dumps(section))
        return validated

    @staticmethod
    def _merge_wake_settings(state: PersistedOnboardingState, wake_settings: dict[str, Any]) -> PersistedOnboardingState:
        default_wakeword = str(wake_settings.get("default_wakeword") or "").strip()
        warm_models = wake_settings.get("warm_models") if isinstance(wake_settings.get("warm_models"), list) else []
        if not warm_models:
            warm_models = wake_settings.get("models") if isinstance(wake_settings.get("models"), list) else []
        provider_config = {
            "enabled": bool(wake_settings.get("enabled", True)),
            "provider": wake_settings["provider"],
            "default_wakeword": default_wakeword,
            "model": default_wakeword,
            "warm_model": bool(wake_settings.get("warm_model", wake_settings.get("preload", False))),
            "warm_models": warm_models,
        }
        provider_config = {
            key: value
            for key, value in provider_config.items()
            if key in WAKE_PROVIDER_CONFIG_FIELDS or key == "provider"
            if value not in (None, "", [])
        }

        provider_configs = dict(state.provider_setup.provider_configs)
        provider_configs["wake"] = {**provider_configs.get("wake", {}), **provider_config}

        supported = list(state.provider_setup.supported_providers)
        if "wake" not in supported:
            supported.append("wake")
        enabled = list(state.provider_setup.enabled_providers)
        if wake_settings.get("enabled", True) and "wake" not in enabled:
            enabled.append("wake")
        if wake_settings.get("enabled") is False:
            enabled = [item for item in enabled if item != "wake"]

        return state.model_copy(
            update={
                "provider_setup": state.provider_setup.model_copy(
                    update={
                        "supported_providers": supported,
                        "enabled_providers": enabled,
                        "provider_configs": provider_configs,
                    }
                )
            }
        )

    @staticmethod
    def _merge_tts_provider_settings(
        state: PersistedOnboardingState,
        tts_settings: dict[str, Any],
    ) -> PersistedOnboardingState:
        provider = str(tts_settings["provider"])
        provider_config = {
            key: tts_settings[key]
            for key in TTS_PROVIDER_CONFIG_FIELDS
            if key in tts_settings and tts_settings[key] not in (None, "", [])
        }
        provider_configs = dict(state.provider_setup.provider_configs)
        provider_configs[provider] = {**provider_configs.get(provider, {}), **provider_config}

        supported = list(state.provider_setup.supported_providers)
        if provider not in supported:
            supported.append(provider)
        enabled = list(state.provider_setup.enabled_providers)
        if tts_settings.get("enabled") and provider not in enabled:
            enabled.append(provider)
        if tts_settings.get("enabled") is False:
            enabled = [item for item in enabled if item != provider]
        default_provider = provider if tts_settings.get("default") else state.provider_setup.default_provider

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
