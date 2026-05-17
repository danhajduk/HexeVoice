from __future__ import annotations

import httpx
from fastapi import HTTPException

from hexevoice.api.models import CapabilityDeclarationResponse, CapabilitySelectionRequest, CapabilitySummaryResponse
from hexevoice.capabilities.schema import CapabilityManifestValidationError, validate_capability_declaration
from hexevoice.config.settings import Settings
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.persistence import OnboardingStateStore
from hexevoice.providers.setup import voice_provider_ids


VOICE_NODE_CAPABILITIES = [
    "voice.inference",
    "voice.tts.synthesize",
    "voice.tts.audio_url",
    "voice.intent.register",
    "voice.intent.list",
    "voice.intent.dispatch",
]


class CapabilityDeclarationService:
    def __init__(
        self,
        *,
        settings: Settings,
        onboarding_state_store: OnboardingStateStore,
        core_onboarding_client: CoreOnboardingClient | None = None,
    ) -> None:
        self._settings = settings
        self._store = onboarding_state_store
        self._core_client = core_onboarding_client or CoreOnboardingClient()

    def declare(self) -> CapabilityDeclarationResponse:
        state = self._store.load()
        if state.trust_activation.trust_status != "trusted":
            raise HTTPException(status_code=400, detail="trust_not_configured")
        if not state.trust_activation.node_id or not state.trust_activation.node_trust_token:
            raise HTTPException(status_code=400, detail="trusted_identity_missing")
        if not state.pre_trust.core_base_url:
            raise HTTPException(status_code=400, detail="core_connection_not_configured")
        if not state.provider_setup.declaration_allowed or not state.provider_setup.enabled_providers:
            raise HTTPException(status_code=400, detail="provider_setup_incomplete")

        declared_task_families = self._selected_capabilities(state)
        enabled_providers = self._enabled_providers(state)
        payload = self.capability_declaration_payload(state)
        try:
            payload["manifest"] = validate_capability_declaration(payload["manifest"])
        except CapabilityManifestValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            response = self._core_client.submit_capability_declaration(
                core_base_url=state.pre_trust.core_base_url,
                node_trust_token=state.trust_activation.node_trust_token,
                payload=payload,
            )
        except httpx.HTTPStatusError as exc:
            detail = "capability_declaration_rejected"
            try:
                payload = exc.response.json()
                if isinstance(payload, dict):
                    raw_detail = payload.get("detail")
                    if isinstance(raw_detail, str):
                        detail = raw_detail
                    elif isinstance(raw_detail, dict):
                        detail = raw_detail.get("message") or raw_detail.get("error") or detail
            except ValueError:
                pass
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="core_capability_declaration_timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"capability_declaration_request_failed: {exc}") from exc

        try:
            self._core_client.submit_budget_declaration(
                core_base_url=state.pre_trust.core_base_url,
                node_trust_token=state.trust_activation.node_trust_token,
                payload=self._budget_declaration_payload(
                    node_id=state.trust_activation.node_id,
                    providers=enabled_providers,
                ),
            )
        except httpx.HTTPStatusError as exc:
            detail = "budget_declaration_rejected"
            try:
                payload = exc.response.json()
                if isinstance(payload, dict):
                    raw_detail = payload.get("detail")
                    if isinstance(raw_detail, str):
                        detail = raw_detail
                    elif isinstance(raw_detail, dict):
                        detail = raw_detail.get("message") or raw_detail.get("error") or detail
            except ValueError:
                pass
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="core_budget_declaration_timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"budget_declaration_request_failed: {exc}") from exc

        updated = state.model_copy(
            update={
                "capability_declaration": state.capability_declaration.model_copy(
                    update={
                        "manifest_version": response.get("manifest_version", "1.0"),
                        "capability_status": "accepted" if response.get("acceptance_status") == "accepted" else "declared",
                        "accepted_at": response.get("accepted_at"),
                        "declared_task_families": declared_task_families,
                        "declared_capabilities": response.get("declared_capabilities", declared_task_families),
                        "capability_profile_id": response.get("capability_profile_id"),
                        "governance_version": response.get("governance_version"),
                        "governance_issued_at": response.get("governance_issued_at"),
                        "last_error": None,
                    }
                ),
                "governance_sync": state.governance_sync.model_copy(
                    update={
                        "governance_sync_status": "pending",
                        "governance_version": response.get("governance_version"),
                        "issued_timestamp": response.get("governance_issued_at"),
                        "governance_bundle": None,
                    }
                ),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": "governance_sync",
                        "last_completed_step_id": "capability_declaration",
                    }
                ),
            }
        )
        self._store.save(updated)

        return CapabilityDeclarationResponse(
            capability_status=updated.capability_declaration.capability_status,
            node_id=state.trust_activation.node_id,
            manifest_version=updated.capability_declaration.manifest_version or "1.0",
            accepted_at=updated.capability_declaration.accepted_at,
            declared_capabilities=updated.capability_declaration.declared_capabilities,
            enabled_providers=enabled_providers,
            capability_profile_id=updated.capability_declaration.capability_profile_id,
            governance_version=updated.capability_declaration.governance_version,
            governance_issued_at=updated.capability_declaration.governance_issued_at,
        )

    def manifest_preview(self) -> dict:
        state = self._store.load()
        declaration_payload = self.capability_declaration_payload(state)
        enabled_providers = self._enabled_providers(state)
        node_id = state.trust_activation.node_id or ""
        provider_models = self._provider_model_preview(enabled_providers, state.provider_setup.provider_configs)
        runtime = self._runtime_preview(declaration_payload["manifest"]["capability_endpoints"])
        return {
            "declaration_payload": declaration_payload,
            "budget_declaration": self._budget_declaration_payload(
                node_id=node_id,
                providers=enabled_providers,
            )
            if node_id
            else {},
            "node_identity": declaration_payload["manifest"]["node"],
            "providers": {
                "supported": declaration_payload["manifest"]["supported_providers"],
                "enabled": enabled_providers,
                "default_provider": state.provider_setup.default_provider,
                "configs": dict(state.provider_setup.provider_configs),
                "models": provider_models,
            },
            "capabilities": {
                "selected": declaration_payload["manifest"]["declared_capabilities"],
                "endpoints": declaration_payload["manifest"]["capability_endpoints"],
            },
            "runtime": runtime,
            "governance": {
                "capability_profile_id": state.capability_declaration.capability_profile_id,
                "governance_version": state.governance_sync.governance_version
                or state.capability_declaration.governance_version,
                "governance_sync_status": state.governance_sync.governance_sync_status,
                "governance_freshness_state": state.governance_sync.governance_freshness_state,
                "refresh_interval_s": state.governance_sync.refresh_interval_s,
                "last_refresh_request_at": state.governance_sync.last_refresh_request_at,
            },
            "core_visible_summary": self._core_visible_summary(
                declaration_payload=declaration_payload,
                provider_models=provider_models,
                runtime=runtime,
            ),
        }

    def capability_declaration_payload(self, state=None) -> dict:
        state = state or self._store.load()
        declared_task_families = self._selected_capabilities(state)
        enabled_providers = self._enabled_providers(state)
        return {
            "manifest": {
                "manifest_version": "1.0",
                "node": {
                    "node_id": state.trust_activation.node_id,
                    "node_type": state.trust_activation.node_type or self._settings.node_type,
                    "node_name": state.pre_trust.node_name or self._settings.node_name,
                    "node_software_version": self._settings.node_software_version,
                },
                "declared_task_families": declared_task_families,
                "declared_capabilities": declared_task_families,
                "capability_endpoints": self._capability_endpoints(declared_task_families),
                "supported_providers": self._supported_providers(state),
                "enabled_providers": enabled_providers,
                "provider_intelligence": self._provider_intelligence(enabled_providers),
                "node_features": {
                    "telemetry": True,
                    "governance_refresh": True,
                    "lifecycle_events": True,
                    "provider_failover": False,
                },
                "environment_hints": {
                    "deployment_target": "node",
                    "acceleration": "none",
                    "network_tier": "lan",
                    "region": "local",
                },
            }
        }

    def _budget_declaration_payload(self, *, node_id: str, providers: list[str]) -> dict:
        return {
            "node_id": node_id,
            "currency": "USD",
            "compute_unit": "cost_units",
            "default_period": "monthly",
            "supports_money_budget": True,
            "supports_compute_budget": True,
            "supports_customer_allocations": False,
            "supports_provider_allocations": False,
            "supported_providers": sorted({provider for provider in providers if provider}),
            "setup_requirements": [],
            "suggested_money_limit": None,
            "suggested_compute_limit": None,
        }

    def _supported_providers(self, state) -> list[str]:
        return sorted(
            {
                provider_id.strip()
                for provider_id in [*state.provider_setup.supported_providers, *voice_provider_ids(self._settings)]
                if provider_id and provider_id.strip()
            }
        )

    def _enabled_providers(self, state) -> list[str]:
        return sorted(
            {
                provider_id.strip()
                for provider_id in state.provider_setup.enabled_providers
                if provider_id and provider_id.strip()
            }
        )

    def _api_base_url(self) -> str:
        return (
            self._settings.public_api_base_url
            or f"http://{self._settings.api_host}:{self._settings.api_port}"
        ).rstrip("/")

    def _provider_model_preview(self, enabled_providers: list[str], provider_configs: dict[str, dict[str, object]]) -> list[dict]:
        previews: list[dict] = []
        for provider_id in enabled_providers:
            config = dict(provider_configs.get(provider_id) or {})
            role = self._provider_role(provider_id)
            if provider_id in {"external_faster_whisper", "faster_whisper"}:
                model = config.get("model") or self._settings.voice_stt_faster_whisper_model
            elif provider_id == "piper":
                model = config.get("default_voice") or self._settings.voice_tts_piper_voice or self._settings.voice_tts_model
            elif provider_id in {"openwakeword", "supervised_openwakeword"}:
                model = config.get("default_wakeword") or self._settings.voice_wake_models or "Hexe"
            else:
                model = config.get("model") or provider_id
            previews.append(
                {
                    "provider_id": provider_id,
                    "role": role,
                    "model": str(model or provider_id),
                    "warm_models": config.get("warm_models") or [],
                    "profile": config.get("profile"),
                    "device": config.get("device"),
                    "cuda_mode": config.get("cuda_mode"),
                    "compute_type": config.get("compute_type"),
                    "language": config.get("language"),
                    "threshold": config.get("threshold"),
                }
            )
        return previews

    def _runtime_preview(self, capability_endpoints: dict) -> dict:
        return {
            "api_base_url": self._api_base_url(),
            "capability_endpoints": capability_endpoints,
            "providers": {
                "stt": {
                    "provider": self._settings.voice_stt_provider,
                    "base_url": self._settings.resolved_voice_stt_service_base_url(),
                    "socket_path": str(self._settings.resolved_voice_stt_service_socket_path())
                    if self._settings.resolved_voice_stt_service_socket_path() is not None
                    else None,
                    "port": self._settings.voice_stt_service_port,
                },
                "tts": {
                    "provider": self._settings.voice_tts_provider,
                    "base_url": self._settings.resolved_voice_tts_piper_base_url(),
                    "socket_path": str(self._settings.resolved_voice_tts_piper_socket_path())
                    if self._settings.resolved_voice_tts_piper_socket_path() is not None
                    else None,
                    "port": self._settings.voice_tts_piper_service_port,
                },
                "wake": {
                    "provider": self._settings.voice_wake_provider,
                    "host": self._settings.voice_wake_service_host,
                    "port": self._settings.voice_wake_service_port,
                },
            },
        }

    def _core_visible_summary(self, *, declaration_payload: dict, provider_models: list[dict], runtime: dict) -> dict:
        manifest = declaration_payload["manifest"]
        selected = list(manifest["declared_capabilities"])
        disabled = [capability for capability in VOICE_NODE_CAPABILITIES if capability not in set(selected)]
        models_by_provider = {str(item.get("provider_id")): item for item in provider_models}
        service_specs = [
            {
                "service_id": "stt",
                "label": "STT",
                "role": "stt",
                "provider_id": self._provider_for_role(provider_models, "stt") or self._settings.voice_stt_provider,
                "enabled": "voice.inference" in selected,
                "capabilities": [capability for capability in selected if capability == "voice.inference"],
                "runtime": runtime.get("providers", {}).get("stt") or {},
            },
            {
                "service_id": "tts",
                "label": "TTS",
                "role": "tts",
                "provider_id": self._provider_for_role(provider_models, "tts") or self._settings.voice_tts_provider,
                "enabled": any(capability in selected for capability in {"voice.tts.synthesize", "voice.tts.audio_url"}),
                "capabilities": [capability for capability in selected if capability.startswith("voice.tts.")],
                "runtime": runtime.get("providers", {}).get("tts") or {},
            },
            {
                "service_id": "wake",
                "label": "Wake",
                "role": "wake",
                "provider_id": self._provider_for_role(provider_models, "wake") or self._settings.voice_wake_provider,
                "enabled": bool(self._provider_for_role(provider_models, "wake")),
                "capabilities": [],
                "runtime": runtime.get("providers", {}).get("wake") or {},
            },
        ]
        for service in service_specs:
            model = models_by_provider.get(str(service["provider_id"])) or {}
            service["models"] = [item for item in [model.get("model"), *model.get("warm_models", [])] if item]
        available_models = []
        for model in provider_models:
            model_ids = [model.get("model"), *model.get("warm_models", [])]
            for model_id in model_ids:
                if not model_id:
                    continue
                available_models.append(
                    {
                        "provider_id": model.get("provider_id"),
                        "role": model.get("role"),
                        "model_id": model_id,
                        "enabled": model.get("provider_id") in manifest["enabled_providers"],
                    }
                )
        return {
            "node_id": manifest["node"].get("node_id"),
            "provided_services": service_specs,
            "available_models": available_models,
            "enabled_capabilities": selected,
            "disabled_capabilities": disabled,
            "enabled_providers": manifest["enabled_providers"],
        }

    @staticmethod
    def _provider_role(provider_id: str) -> str:
        if provider_id in {"external_faster_whisper", "faster_whisper"}:
            return "stt"
        if provider_id == "piper":
            return "tts"
        if provider_id in {"openwakeword", "supervised_openwakeword"}:
            return "wake"
        return "pipeline"

    @staticmethod
    def _provider_for_role(provider_models: list[dict], role: str) -> str | None:
        for model in provider_models:
            if model.get("role") == role:
                return str(model.get("provider_id") or "")
        return None

    def _provider_intelligence(self, enabled_providers: list[str]) -> list[dict]:
        providers = {provider.strip().lower() for provider in enabled_providers if provider and provider.strip()}
        intelligence: list[dict] = []
        if "piper" in providers:
            intelligence.append(
                {
                    "provider": "piper",
                    "available_models": self._piper_voice_models(),
                }
            )
        return intelligence

    def _piper_voice_models(self) -> list[dict]:
        model_dir = self._settings.resolved_piper_tts_model_dir()
        models: list[dict] = []
        for model_path in sorted(model_dir.glob("*.onnx")) if model_dir.exists() else []:
            model_id = model_path.stem
            models.append({"model_id": model_id})
        configured_voice = str(self._settings.voice_tts_piper_voice or "").strip()
        if configured_voice and configured_voice not in {str(model.get("model_id")) for model in models}:
            models.append({"model_id": configured_voice})
        return sorted(models, key=lambda item: str(item.get("model_id") or "").lower())

    def save_selection(self, payload: CapabilitySelectionRequest) -> CapabilitySummaryResponse:
        state = self._store.load()
        if state.trust_activation.trust_status != "trusted":
            raise HTTPException(status_code=400, detail="trust_not_ready_for_capability_selection")

        selected = normalize_capability_selection(payload.selected_capabilities)
        if not selected:
            raise HTTPException(status_code=400, detail="capability_selection_required")

        selection_changed = selected != self._selected_capabilities(state)
        capability_status = state.capability_declaration.capability_status
        if selection_changed:
            capability_status = "selection_pending"

        updated = state.model_copy(
            update={
                "capability_declaration": state.capability_declaration.model_copy(
                    update={
                        "declared_task_families": selected,
                        "capability_status": capability_status,
                        "last_error": None,
                    }
                ),
                "governance_sync": state.governance_sync.model_copy(
                    update={
                        "governance_sync_status": "pending_capability"
                        if selection_changed
                        else state.governance_sync.governance_sync_status,
                    }
                ),
            }
        )
        self._store.save(updated)
        return capability_summary(updated)

    def _selected_capabilities(self, state) -> list[str]:
        return normalize_capability_selection(state.capability_declaration.declared_task_families) or VOICE_NODE_CAPABILITIES

    def _capability_endpoints(self, selected_capabilities: list[str]) -> dict:
        base_url = (
            self._settings.public_api_base_url
            or f"http://{self._settings.api_host}:{self._settings.api_port}"
        ).rstrip("/")
        supported_formats = ["wav", "mp3"] if self._settings.voice_tts_provider == "openai" else ["wav"]
        default_format = (
            self._settings.voice_tts_response_format
            if self._settings.voice_tts_provider == "openai"
            else "wav"
        )
        if default_format not in supported_formats:
            default_format = "wav"
        endpoints = {
            "voice.tts.synthesize": {
                "transport": "http",
                "method": "POST",
                "path": "/api/tts/synthesize",
                "url": f"{base_url}/api/tts/synthesize",
                "request_schema": "TtsSynthesizeRequest",
                "response_schema": "TtsSynthesizeResponse",
                "default_format": default_format,
                "supported_formats": supported_formats,
                "ttl_seconds": {
                    "default": 3600,
                    "minimum": 5,
                    "maximum": 3600,
                },
            },
            "voice.tts.audio_url": {
                "transport": "http",
                "method": "GET",
                "path": "/api/tts/audio/{stream_id}",
                "url_template": f"{base_url}/api/tts/audio/{{stream_id}}",
                "response": "short_lived_audio_file",
                "reachable_from": "lan",
            },
            "voice.intent.register": {
                "transport": "http",
                "method": "POST",
                "path": "/api/voice/intents",
                "url": f"{base_url}/api/voice/intents",
                "request_schema": "VoiceIntentRegisterRequest",
                "response_schema": "VoiceIntentStateResponse",
                "lifecycle_paths": {
                    "update": "/api/voice/intents/{intent_id}",
                    "transition": "/api/voice/intents/{intent_id}/lifecycle",
                    "review": "/api/voice/intents/{intent_id}/review",
                },
                "supports_versions": True,
                "supports_lifecycle": True,
                "supported_matcher_modes": ["builtin_timer", "builtin_time_query", "exact_example", "regex"],
            },
            "voice.intent.list": {
                "transport": "http",
                "method": "GET",
                "path": "/api/voice/intents",
                "url": f"{base_url}/api/voice/intents",
                "response_schema": "VoiceIntentStateResponse",
                "lookup_path": "/api/voice/intents/{intent_id}",
            },
            "voice.intent.dispatch": {
                "transport": "http",
                "method": "POST",
                "path": "/api/voice/intents/dispatch",
                "url": f"{base_url}/api/voice/intents/dispatch",
                "request_schema": "VoiceIntentDispatchRequest",
                "response_schema": "VoiceIntentDispatchResponse",
                "side_effects": "dry_run_match_only",
            },
        }
        return {
            capability: endpoint
            for capability, endpoint in endpoints.items()
            if capability in selected_capabilities
        }


def normalize_capability_selection(capabilities: list[str]) -> list[str]:
    requested = {str(capability).strip() for capability in capabilities if str(capability).strip()}
    unsupported = sorted(requested - set(VOICE_NODE_CAPABILITIES))
    if unsupported:
        raise HTTPException(status_code=400, detail=f"unsupported_capabilities: {', '.join(unsupported)}")
    return [capability for capability in VOICE_NODE_CAPABILITIES if capability in requested]


def capability_summary(state) -> CapabilitySummaryResponse:
    return CapabilitySummaryResponse(
        configured=state.provider_setup.enabled_providers,
        available=VOICE_NODE_CAPABILITIES,
        selected=normalize_capability_selection(state.capability_declaration.declared_task_families) or VOICE_NODE_CAPABILITIES,
        declared=state.capability_declaration.declared_capabilities,
        capability_status=state.capability_declaration.capability_status,
        capability_profile_id=state.capability_declaration.capability_profile_id,
        accepted_at=state.capability_declaration.accepted_at,
        governance_version=state.capability_declaration.governance_version,
    )
