from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException
import httpx

from hexevoice.api.models import SetupReauthFinalizeResponse, SetupReauthStartResponse
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.onboarding.trust_activation import TrustActivationService
from hexevoice.persistence import OnboardingStateStore


class SetupReauthService:
    def __init__(
        self,
        *,
        onboarding_state_store: OnboardingStateStore,
        core_client: CoreOnboardingClient | None = None,
    ) -> None:
        self._store = onboarding_state_store
        self._core_client = core_client or CoreOnboardingClient()
        self._trust_activation = TrustActivationService(onboarding_state_store=onboarding_state_store)

    def start(self) -> SetupReauthStartResponse:
        state = self._store.load()
        node_id = state.trust_activation.node_id
        core_base_url = state.pre_trust.core_base_url
        if not node_id:
            raise HTTPException(status_code=400, detail="reauth_node_id_missing")
        if not core_base_url:
            raise HTTPException(status_code=400, detail="reauth_core_base_url_missing")

        node_nonce = secrets.token_urlsafe(24)
        try:
            response = self._core_client.start_reauth_session(
                core_base_url=core_base_url,
                payload={"node_id": node_id, "node_nonce": node_nonce, "reason": "migration"},
            )
        except (httpx.HTTPError, ValueError) as exc:
            return SetupReauthStartResponse(
                started=False,
                node_id=node_id,
                status="core_unreachable",
                warnings=[str(exc)],
            )

        session_id = self._session_id(response)
        approval_url = response.get("approval_url")
        finalize_path = self._finalize_path(response)
        updated = state.model_copy(
            update={
                "pre_trust": state.pre_trust.model_copy(update={"node_nonce": node_nonce}),
                "onboarding_session": state.onboarding_session.model_copy(
                    update={
                        "session_id": session_id,
                        "approval_url": approval_url,
                        "finalize_url": finalize_path,
                        "session_state": response.get("reauth_status") or response.get("status") or "pending",
                        "last_error": None,
                    }
                ),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": "trust_activation",
                        "last_completed_step_id": "core_connection",
                    }
                ),
            }
        )
        self._store.save(updated)
        return SetupReauthStartResponse(
            started=True,
            node_id=node_id,
            session_id=session_id,
            approval_url=approval_url,
            finalize_path=finalize_path,
            status=response.get("reauth_status") or response.get("status") or "pending",
        )

    def finalize(self) -> SetupReauthFinalizeResponse:
        state = self._store.load()
        core_base_url = state.pre_trust.core_base_url
        session_id = state.onboarding_session.session_id
        node_nonce = state.pre_trust.node_nonce
        if not core_base_url or not session_id or not node_nonce:
            raise HTTPException(status_code=400, detail="reauth_session_not_started")

        try:
            response = self._core_client.finalize_reauth_session(
                core_base_url=core_base_url,
                session_id=session_id,
                node_nonce=node_nonce,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return SetupReauthFinalizeResponse(status="core_unreachable", warnings=[str(exc)])

        status = str(response.get("status") or response.get("reauth_status") or "pending")
        activation = response.get("activation")
        if status == "approved" and isinstance(activation, dict):
            current = self._store.load()
            self._store.save(
                current.model_copy(
                    update={
                        "onboarding_session": current.onboarding_session.model_copy(
                            update={
                                "pending_activation": activation,
                                "session_state": "approved",
                                "last_terminal_outcome": "approved",
                            }
                        )
                    }
                )
            )
            finalized = self._trust_activation.finalize_activation()
            return SetupReauthFinalizeResponse(
                status="approved",
                approved=True,
                node_id=finalized.node_id,
                trust_state=finalized.trust_state,
            )

        current = self._store.load()
        self._store.save(
            current.model_copy(
                update={
                    "onboarding_session": current.onboarding_session.model_copy(
                        update={
                            "session_state": status,
                            "last_terminal_outcome": status if status in {"rejected", "expired", "consumed", "invalid"} else None,
                        }
                    )
                }
            )
        )
        return SetupReauthFinalizeResponse(
            status=status,
            approved=False,
            node_id=current.trust_activation.node_id,
            trust_state=current.trust_activation.trust_status,
            approval_url=current.onboarding_session.approval_url,
        )

    @staticmethod
    def _session_id(response: dict[str, Any]) -> str | None:
        if response.get("session_id"):
            return str(response["session_id"])
        finalize = response.get("finalize")
        if isinstance(finalize, dict) and finalize.get("path"):
            parts = str(finalize["path"]).strip("/").split("/")
            if "sessions" in parts:
                index = parts.index("sessions")
                if len(parts) > index + 1:
                    return parts[index + 1]
        return None

    @staticmethod
    def _finalize_path(response: dict[str, Any]) -> str | None:
        finalize = response.get("finalize")
        if isinstance(finalize, dict) and finalize.get("path"):
            return str(finalize["path"])
        return None
