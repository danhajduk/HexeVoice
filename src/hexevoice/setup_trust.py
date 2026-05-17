from __future__ import annotations

import httpx

from hexevoice.api.models import SetupTrustRecoveryActionResponse
from hexevoice.config.settings import Settings
from hexevoice.onboarding.approval import ApprovalPollingService
from hexevoice.onboarding.trust_activation import TrustActivationService
from hexevoice.persistence import OnboardingSessionState, OnboardingStateStore, PersistedOnboardingState, TrustActivationState
from hexevoice.setup_reauth import SetupReauthService


TERMINAL_SESSION_STATES = {"rejected", "expired", "invalid", "consumed"}


class SetupTrustRecoveryService:
    def __init__(
        self,
        *,
        settings: Settings,
        onboarding_state_store: OnboardingStateStore,
        approval_service: ApprovalPollingService | None = None,
        trust_activation_service: TrustActivationService | None = None,
        reauth_service: SetupReauthService | None = None,
    ) -> None:
        self._settings = settings
        self._store = onboarding_state_store
        self._approval_service = approval_service or ApprovalPollingService(
            settings=settings,
            onboarding_state_store=onboarding_state_store,
        )
        self._trust_activation_service = trust_activation_service or TrustActivationService(
            onboarding_state_store=onboarding_state_store,
        )
        self._reauth_service = reauth_service or SetupReauthService(onboarding_state_store=onboarding_state_store)

    def run_action(self, action: str) -> SetupTrustRecoveryActionResponse:
        normalized = action.strip().lower()
        if normalized == "restart-onboarding":
            return self._restart_onboarding()
        if normalized == "reopen-core-approval":
            return self._reopen_core_approval()
        if normalized == "repoll-approval":
            return self._repoll_approval()
        if normalized == "retry-trust-finalize":
            return self._retry_trust_finalize()
        if normalized == "clear-expired-sessions":
            return self._clear_expired_sessions()
        if normalized == "recheck-core-trust-support":
            return self._recheck_core_trust_support()
        return SetupTrustRecoveryActionResponse(
            accepted=False,
            action=normalized or action,
            message="unsupported_setup_trust_action",
            retryable=False,
        )

    def _restart_onboarding(self) -> SetupTrustRecoveryActionResponse:
        state = self._store.load()
        trust_activation = state.trust_activation
        if trust_activation.trust_status != "trusted":
            trust_activation = TrustActivationState()
        updated = self._store.save(
            state.model_copy(
                update={
                    "onboarding_session": OnboardingSessionState(),
                    "trust_activation": trust_activation,
                    "resume": state.resume.model_copy(
                        update={
                            "current_step_id": self._pretrust_resume_step(state),
                            "last_completed_step_id": self._pretrust_last_completed_step(state),
                        }
                    ),
                }
            )
        )
        return self._response(
            action="restart-onboarding",
            message="onboarding_restarted",
            state=updated,
        )

    def _reopen_core_approval(self) -> SetupTrustRecoveryActionResponse:
        state = self._store.load()
        approval_url = state.onboarding_session.approval_url
        if not approval_url:
            return self._response(
                action="reopen-core-approval",
                message="approval_url_missing",
                state=state,
                accepted=False,
            )
        return self._response(
            action="reopen-core-approval",
            message="approval_url_available",
            state=state,
            approval_url=approval_url,
        )

    def _repoll_approval(self) -> SetupTrustRecoveryActionResponse:
        state = self._store.load()
        if state.trust_activation.trust_status == "reauth_required":
            finalized = self._reauth_service.finalize()
            return SetupTrustRecoveryActionResponse(
                accepted=True,
                action="repoll-approval",
                message="reauth_status_polled",
                session_state=finalized.status,
                node_id=finalized.node_id,
                trust_state=finalized.trust_state,
                approval_url=finalized.approval_url,
                warnings=finalized.warnings,
            )

        polled = self._approval_service.poll_session()
        refreshed = self._store.load()
        return self._response(
            action="repoll-approval",
            message=f"approval_polled:{polled.session_state}",
            state=refreshed,
        )

    def _retry_trust_finalize(self) -> SetupTrustRecoveryActionResponse:
        state = self._store.load()
        if state.trust_activation.trust_status == "reauth_required":
            finalized = self._reauth_service.finalize()
            return SetupTrustRecoveryActionResponse(
                accepted=True,
                action="retry-trust-finalize",
                message="reauth_finalize_retried",
                session_state=finalized.status,
                node_id=finalized.node_id,
                trust_state=finalized.trust_state,
                approval_url=finalized.approval_url,
                warnings=finalized.warnings,
            )

        finalized = self._trust_activation_service.finalize_activation()
        refreshed = self._store.load()
        return self._response(
            action="retry-trust-finalize",
            message=f"trust_finalized:{finalized.node_id}",
            state=refreshed,
        )

    def _clear_expired_sessions(self) -> SetupTrustRecoveryActionResponse:
        state = self._store.load()
        session_state = state.onboarding_session.session_state
        terminal_outcome = state.onboarding_session.last_terminal_outcome
        if session_state not in TERMINAL_SESSION_STATES and terminal_outcome not in TERMINAL_SESSION_STATES:
            return self._response(
                action="clear-expired-sessions",
                message="no_expired_session_to_clear",
                state=state,
                accepted=False,
            )

        updated = self._store.save(
            state.model_copy(
                update={
                    "onboarding_session": OnboardingSessionState(),
                    "resume": state.resume.model_copy(
                        update={
                            "current_step_id": "registration" if state.trust_activation.trust_status != "reauth_required" else "trust_activation",
                            "last_completed_step_id": "bootstrap_discovery",
                        }
                    ),
                }
            )
        )
        return self._response(
            action="clear-expired-sessions",
            message="expired_session_cleared",
            state=updated,
        )

    def _recheck_core_trust_support(self) -> SetupTrustRecoveryActionResponse:
        state = self._store.load()
        core_base_url = state.pre_trust.core_base_url
        if not core_base_url:
            return self._response(
                action="recheck-core-trust-support",
                message="core_base_url_missing",
                state=state,
                accepted=False,
            )

        core = core_base_url.rstrip("/")
        support: dict[str, object] = {}
        warnings: list[str] = []
        try:
            with httpx.Client(timeout=2.0) as client:
                for key, method, path in (
                    ("health", "GET", "/api/health"),
                    ("onboarding", "HEAD", "/api/system/nodes/onboarding/sessions"),
                    ("reauth", "HEAD", "/api/system/nodes/reauth/sessions"),
                ):
                    try:
                        response = client.request(method, f"{core}{path}")
                    except httpx.HTTPError as exc:
                        support[key] = {"supported": False, "error": str(exc)}
                        continue
                    support[key] = {
                        "supported": response.status_code not in {404, 501},
                        "status_code": response.status_code,
                        "path": path,
                    }
        except httpx.HTTPError as exc:
            warnings.append(str(exc))

        return self._response(
            action="recheck-core-trust-support",
            message="core_trust_support_checked",
            state=state,
            core_support=support,
            warnings=warnings,
        )

    def _pretrust_resume_step(self, state: PersistedOnboardingState) -> str:
        if state.pre_trust.node_name and state.pre_trust.protocol_version and state.pre_trust.node_nonce:
            if state.pre_trust.core_base_url and state.bootstrap_discovery.advertisement_valid:
                return "registration"
            if state.pre_trust.core_base_url:
                return "bootstrap_discovery"
            return "core_connection"
        return "node_identity"

    def _pretrust_last_completed_step(self, state: PersistedOnboardingState) -> str | None:
        step = self._pretrust_resume_step(state)
        if step == "registration":
            return "bootstrap_discovery"
        if step == "bootstrap_discovery":
            return "core_connection"
        if step == "core_connection":
            return "node_identity"
        return None

    def _response(
        self,
        *,
        action: str,
        message: str,
        state: PersistedOnboardingState,
        accepted: bool = True,
        approval_url: str | None = None,
        core_support: dict[str, object] | None = None,
        warnings: list[str] | None = None,
    ) -> SetupTrustRecoveryActionResponse:
        return SetupTrustRecoveryActionResponse(
            accepted=accepted,
            action=action,
            message=message,
            session_state=state.onboarding_session.session_state,
            node_id=state.trust_activation.node_id,
            trust_state=state.trust_activation.trust_status,
            approval_url=approval_url or state.onboarding_session.approval_url,
            core_support=core_support or {},
            warnings=warnings or [],
        )
