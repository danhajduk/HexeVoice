from __future__ import annotations

import socket
from typing import Any

from fastapi import HTTPException
import httpx

from hexevoice.config.settings import Settings
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def registration_hostname(state: PersistedOnboardingState) -> str | None:
    if state.pre_trust.hostname:
        return state.pre_trust.hostname
    return socket.gethostname() or None


def registration_ui_endpoint(settings: Settings, state: PersistedOnboardingState) -> str | None:
    return state.pre_trust.ui_endpoint or settings.public_ui_base_url


def registration_api_base_url(settings: Settings, state: PersistedOnboardingState) -> str | None:
    if state.pre_trust.api_base_url:
        return state.pre_trust.api_base_url
    if settings.public_api_base_url:
        return settings.public_api_base_url.rstrip("/")
    if settings.api_host and settings.api_host not in {"0.0.0.0", "::"}:
        return f"http://{settings.api_host}:{settings.api_port}"
    return None


def onboarding_start_metadata(settings: Settings, state: PersistedOnboardingState) -> dict[str, Any]:
    return {
        "node_id": state.pre_trust.requested_node_id,
        "hostname": registration_hostname(state),
        "ui_endpoint": registration_ui_endpoint(settings, state),
        "api_base_url": registration_api_base_url(settings, state),
    }


def full_onboarding_metadata(settings: Settings, state: PersistedOnboardingState) -> dict[str, Any]:
    ui_endpoint = registration_ui_endpoint(settings, state)
    return {
        "metadata_schema_version": "1.0",
        "hostname": registration_hostname(state),
        "ui_endpoint": ui_endpoint,
        "api_base_url": registration_api_base_url(settings, state),
        "ui_enabled": False,
        "ui_base_url": None,
        "ui_mode": "spa",
        "ui_health_endpoint": None,
    }


class RegistrationMetadataRefreshService:
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

    def refresh(self) -> dict[str, Any]:
        state = self._store.load()
        if not state.pre_trust.core_base_url:
            raise HTTPException(status_code=400, detail="core_connection_not_configured")
        if not state.trust_activation.node_id:
            raise HTTPException(status_code=400, detail="node_registration_not_configured")
        if not self._settings.core_admin_token:
            raise HTTPException(status_code=400, detail="core_admin_token_not_configured")

        payload = full_onboarding_metadata(self._settings, state)
        if not payload.get("api_base_url"):
            raise HTTPException(status_code=400, detail="api_base_url_not_configured")

        try:
            response = self._core_client.update_registration_metadata(
                core_base_url=state.pre_trust.core_base_url,
                node_id=state.trust_activation.node_id,
                admin_token=self._settings.core_admin_token,
                payload=payload,
            )
        except httpx.HTTPStatusError as exc:
            message = exc.response.text.strip() or f"http_{exc.response.status_code}"
            raise HTTPException(status_code=exc.response.status_code, detail=message) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc) or "core_connection_failed") from exc

        return {
            "ok": True,
            "node_id": state.trust_activation.node_id,
            "metadata": payload,
            "registration": response.get("registration") if isinstance(response, dict) else None,
        }
