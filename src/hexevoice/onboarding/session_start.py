from __future__ import annotations

import httpx
from fastapi import HTTPException
import socket

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
            "node_id": state.pre_trust.requested_node_id,
            "hostname": self._registration_hostname(state.pre_trust.hostname),
            "ui_endpoint": self._registration_ui_endpoint(state.pre_trust.ui_endpoint),
            "api_base_url": self._registration_api_base_url(state.pre_trust.api_base_url),
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
            raise HTTPException(status_code=exc.response.status_code, detail=message) from exc
        except httpx.HTTPError as exc:
            message = str(exc) or "core_connection_failed"
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
            raise HTTPException(status_code=502, detail=message) from exc

        session_payload = self._extract_session_payload(response)

        finalize_url = self._normalize_finalize_url(session_payload.get("finalize"))

        updated = state.model_copy(
            update={
                "onboarding_session": state.onboarding_session.model_copy(
                    update={
                        "session_id": session_payload.get("session_id"),
                        "approval_url": session_payload.get("approval_url"),
                        "expires_at": session_payload.get("expires_at"),
                        "finalize_url": finalize_url,
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
            session_id=session_payload["session_id"],
            approval_url=session_payload["approval_url"],
            expires_at=session_payload.get("expires_at"),
            finalize_url=finalize_url,
            node_name=response.get("node_name") or response.get("requested_node_name") or session_payload.get("node_name"),
            node_type=response.get("node_type") or response.get("requested_node_type") or session_payload.get("node_type"),
            node_software_version=response.get("node_software_version")
            or response.get("requested_node_software_version")
            or session_payload.get("node_software_version"),
        )

    def _extract_session_payload(self, response: dict) -> dict:
        session_payload = response.get("session") if isinstance(response.get("session"), dict) else response
        session_id = session_payload.get("session_id")
        approval_url = session_payload.get("approval_url")
        if not session_id or not approval_url:
            raise HTTPException(status_code=502, detail="core_onboarding_response_invalid")
        return session_payload

    def _normalize_finalize_url(self, finalize_value: object) -> str | None:
        if isinstance(finalize_value, str):
            return finalize_value
        if isinstance(finalize_value, dict):
            path = finalize_value.get("path")
            if isinstance(path, str) and path.strip():
                return path
        return None

    def _registration_hostname(self, configured_hostname: str | None) -> str | None:
        if configured_hostname:
            return configured_hostname
        return socket.gethostname() or None

    def _registration_ui_endpoint(self, configured_ui_endpoint: str | None) -> str | None:
        return configured_ui_endpoint or self._settings.public_ui_base_url

    def _registration_api_base_url(self, configured_api_base_url: str | None) -> str | None:
        if configured_api_base_url:
            return configured_api_base_url
        if self._settings.public_api_base_url:
            return self._settings.public_api_base_url.rstrip("/")
        if self._settings.api_host and self._settings.api_host not in {"0.0.0.0", "::"}:
            return f"http://{self._settings.api_host}:{self._settings.api_port}"
        return None
