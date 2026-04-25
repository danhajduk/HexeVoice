from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
import httpx

from hexevoice.api.models import OnboardingSessionPollResponse
from hexevoice.config.settings import Settings
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.persistence import OnboardingStateStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalPollingService:
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

    def poll_session(self) -> OnboardingSessionPollResponse:
        state = self._store.load()

        if not state.pre_trust.core_base_url:
            raise HTTPException(status_code=400, detail="core_connection_not_configured")
        if not state.pre_trust.node_nonce:
            raise HTTPException(status_code=400, detail="node_identity_not_configured")
        if not state.onboarding_session.session_id:
            raise HTTPException(status_code=400, detail="onboarding_session_not_started")

        try:
            response = self._core_client.finalize_onboarding_session(
                core_base_url=state.pre_trust.core_base_url,
                session_id=state.onboarding_session.session_id,
                node_nonce=state.pre_trust.node_nonce,
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
        except httpx.TimeoutException as exc:
            message = "core_finalize_timeout"
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
            raise HTTPException(status_code=504, detail=message) from exc
        except httpx.HTTPError as exc:
            message = str(exc) or "core_finalize_failed"
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

        outcome = str(
            response.get("outcome")
            or response.get("status")
            or response.get("onboarding_status")
            or "invalid"
        ).strip().lower()
        last_polled_at = _utc_now()
        activation = response.get("activation")
        activation_received = outcome == "approved" and isinstance(activation, dict) and bool(activation)
        last_terminal_outcome = outcome if outcome != "pending" else None

        current_step_id = "approval"
        last_completed_step_id = state.resume.last_completed_step_id
        if outcome == "approved":
            current_step_id = "trust_activation"
            last_completed_step_id = "approval"

        updated = state.model_copy(
            update={
                "onboarding_session": state.onboarding_session.model_copy(
                    update={
                        "session_state": outcome,
                        "last_polled_at": last_polled_at,
                        "last_terminal_outcome": last_terminal_outcome,
                        "pending_activation": activation if activation_received else None,
                        "last_error": None,
                    }
                ),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": current_step_id,
                        "last_completed_step_id": last_completed_step_id,
                    }
                ),
            }
        )
        self._store.save(updated)

        return OnboardingSessionPollResponse(
            session_id=updated.onboarding_session.session_id or "",
            session_state=outcome,
            last_polled_at=last_polled_at,
            last_terminal_outcome=last_terminal_outcome,
            activation_received=activation_received,
        )
