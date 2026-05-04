from __future__ import annotations

import httpx
from fastapi import HTTPException

from hexevoice.api.models import CapabilityDeclarationResponse
from hexevoice.config.settings import Settings
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.persistence import OnboardingStateStore


VOICE_NODE_CAPABILITIES = [
    "voice.inference",
    "voice.tts.synthesize",
    "voice.tts.audio_url",
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

        declared_task_families = VOICE_NODE_CAPABILITIES
        supported_providers = sorted({provider_id.strip() for provider_id in (state.provider_setup.supported_providers or [self._settings.provider_id]) if provider_id and provider_id.strip()})
        enabled_providers = sorted({provider_id.strip() for provider_id in state.provider_setup.enabled_providers if provider_id and provider_id.strip()})
        capability_endpoints = self._capability_endpoints()

        payload = {
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
                "capability_endpoints": capability_endpoints,
                "supported_providers": supported_providers,
                "enabled_providers": enabled_providers,
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

    def _capability_endpoints(self) -> dict:
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
        return {
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
                    "default": 60,
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
        }
