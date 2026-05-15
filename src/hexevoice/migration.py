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
