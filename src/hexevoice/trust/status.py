from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from hexevoice.api.models import TrustStatusRefreshResponse
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.persistence import OnboardingStateStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrustStatusService:
    def __init__(
        self,
        *,
        onboarding_state_store: OnboardingStateStore,
        core_onboarding_client: CoreOnboardingClient | None = None,
    ) -> None:
        self._store = onboarding_state_store
        self._core_client = core_onboarding_client or CoreOnboardingClient()

    def refresh_status(self) -> TrustStatusRefreshResponse:
        state = self._store.load()

        if not state.pre_trust.core_base_url:
            raise HTTPException(status_code=400, detail="core_connection_not_configured")
        if not state.trust_activation.node_id or not state.trust_activation.node_trust_token:
            raise HTTPException(status_code=400, detail="trust_not_configured")

        response = self._core_client.get_trust_status(
            core_base_url=state.pre_trust.core_base_url,
            node_id=state.trust_activation.node_id,
            node_trust_token=state.trust_activation.node_trust_token,
        )

        checked_at = _utc_now()
        support_state = response.get("support_state")
        supported = response.get("supported")
        trust_state = response.get("trust_status") or state.trust_activation.trust_status
        current_step_id = state.resume.current_step_id
        last_completed_step_id = state.resume.last_completed_step_id
        onboarding_session_update = {}
        trust_update = {
            "trust_status": trust_state,
            "supported": supported,
            "support_state": support_state,
            "registry_present": response.get("registry_present"),
            "registry_state": response.get("registry_state"),
            "revoked_at": response.get("revoked_at"),
            "revocation_reason": response.get("revocation_reason"),
            "revocation_action": response.get("revocation_action"),
            "support_message": response.get("message"),
            "trust_last_checked_at": checked_at,
        }

        if supported is True and trust_state == "trusted":
            if current_step_id in {"node_identity", "core_connection", "bootstrap_discovery", "registration", "approval", "trust_activation"}:
                current_step_id = "provider_setup"
                last_completed_step_id = "trust_activation"
        elif support_state in {"revoked", "removed"} or trust_state == "revoked":
            current_step_id = "registration"
            last_completed_step_id = "bootstrap_discovery"
            onboarding_session_update = {
                "session_id": None,
                "approval_url": None,
                "expires_at": None,
                "finalize_url": None,
                "session_state": None,
                "last_polled_at": None,
                "last_terminal_outcome": None,
                "pending_activation": None,
            }
            trust_update.update(
                {
                    "trust_status": "revoked",
                    "operational_mqtt_token": None,
                }
            )

        updated = state.model_copy(
            update={
                "trust_activation": state.trust_activation.model_copy(update=trust_update),
                "onboarding_session": state.onboarding_session.model_copy(update=onboarding_session_update),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": current_step_id,
                        "last_completed_step_id": last_completed_step_id,
                    }
                ),
            }
        )
        self._store.save(updated)

        return TrustStatusRefreshResponse(
            node_id=updated.trust_activation.node_id or "",
            trust_state=updated.trust_activation.trust_status,
            supported=updated.trust_activation.supported,
            support_state=updated.trust_activation.support_state,
            registry_present=updated.trust_activation.registry_present,
            registry_state=updated.trust_activation.registry_state,
            revoked_at=updated.trust_activation.revoked_at,
            revocation_reason=updated.trust_activation.revocation_reason,
            revocation_action=updated.trust_activation.revocation_action,
            trust_message=updated.trust_activation.support_message,
            trust_last_checked_at=updated.trust_activation.trust_last_checked_at,
        )
