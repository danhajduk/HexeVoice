from __future__ import annotations

import httpx
from fastapi import HTTPException

from hexevoice.api.models import OnboardingSessionStartResponse
from hexevoice.config.settings import Settings
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.persistence import OnboardingStateStore


class OnboardingSessionStartService:
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

    def start_session(self) -> OnboardingSessionStartResponse:
        state = self._store.load()

        if not state.pre_trust.core_base_url:
            raise HTTPException(status_code=400, detail="core_connection_not_configured")
        if not state.pre_trust.protocol_version or not state.pre_trust.node_nonce:
            raise HTTPException(status_code=400, detail="node_identity_not_configured")
        if not state.bootstrap_discovery.advertisement_valid:
            raise HTTPException(status_code=400, detail="bootstrap_discovery_not_completed")

        payload = {
            "node_name": state.pre_trust.node_name or self._settings.node_name,
            "node_type": self._settings.node_type,
            "node_software_version": self._settings.node_software_version,
            "protocol_version": state.pre_trust.protocol_version,
            "node_nonce": state.pre_trust.node_nonce,
            "hostname": state.pre_trust.hostname,
            "ui_endpoint": state.pre_trust.ui_endpoint,
            "api_base_url": state.pre_trust.api_base_url,
        }

        try:
            response = self._core_client.start_onboarding_session(
                core_base_url=state.pre_trust.core_base_url,
                payload=payload,
            )
        except httpx.HTTPStatusError as exc:
            message = exc.response.text.strip() or f"http_{exc.response.status_code}"
            updated = state.model_copy(
                update={
                    "onboarding_session": state.onboarding_session.model_copy(
                        update={
                            "last_error": message,
                        }
                    )
                }
            )
            self._store.save(updated)
            raise

        updated = state.model_copy(
            update={
                "onboarding_session": state.onboarding_session.model_copy(
                    update={
                        "session_id": response.get("session_id"),
                        "approval_url": response.get("approval_url"),
                        "expires_at": response.get("expires_at"),
                        "finalize_url": response.get("finalize"),
                        "session_state": "pending",
                        "last_error": None,
                    }
                ),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": "approval",
                        "last_completed_step_id": "registration",
                    }
                ),
            }
        )
        self._store.save(updated)

        return OnboardingSessionStartResponse(
            session_id=response["session_id"],
            approval_url=response["approval_url"],
            expires_at=response.get("expires_at"),
            finalize_url=response.get("finalize"),
            node_name=response.get("node_name"),
            node_type=response.get("node_type"),
            node_software_version=response.get("node_software_version"),
        )
