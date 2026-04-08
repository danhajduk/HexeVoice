from hexevoice.api.models import (
    ApiHealthResponse,
    CapabilitySummaryResponse,
    GovernanceReadinessResponse,
    NodeStatusResponse,
    OnboardingStepResponse,
    OnboardingStatusResponse,
    ProviderStatusResponse,
    ServiceStatusResponse,
)
from hexevoice.config.settings import Settings
from hexevoice.onboarding import CANONICAL_ONBOARDING_STEPS, initial_onboarding_step
from hexevoice.persistence import OnboardingStateStore


class NodeRuntimeService:
    def __init__(self, *, settings: Settings, onboarding_state_store: OnboardingStateStore | None = None) -> None:
        self._settings = settings
        self._onboarding_state_store = onboarding_state_store or OnboardingStateStore(
            path=settings.resolved_onboarding_state_path()
        )

    def api_health_payload(self) -> ApiHealthResponse:
        return ApiHealthResponse(status="ok", version=self._settings.node_software_version)

    def _state(self):
        return self._onboarding_state_store.load()

    def _current_step(self, state=None):
        onboarding_state = state or self._state()
        current_step_id = onboarding_state.normalized_current_step_id()
        for step in CANONICAL_ONBOARDING_STEPS:
            if step.step_id == current_step_id:
                return step
        return initial_onboarding_step()

    def _trust_state(self, state=None) -> str:
        onboarding_state = state or self._state()
        return onboarding_state.trust_activation.trust_status or "untrusted"

    def _node_id(self, state=None) -> str | None:
        onboarding_state = state or self._state()
        return onboarding_state.trust_activation.node_id

    def _node_name(self, state=None) -> str:
        onboarding_state = state or self._state()
        return onboarding_state.pre_trust.node_name or self._settings.node_name

    def _blocking_reasons(self, current_step_id: str) -> list[str]:
        onboarding_state = self._state()
        support_state = onboarding_state.trust_activation.support_state
        trust_state = onboarding_state.trust_activation.trust_status
        if trust_state == "revoked":
            if support_state == "removed":
                return ["node_removed_by_core", "re_onboarding_required"]
            return ["trust_revoked_by_core", "re_onboarding_required"]

        blockers_by_step = {
            "node_identity": [
                "node_identity_not_configured",
                "core_connection_not_configured",
                "bootstrap_discovery_not_started",
                "onboarding_session_not_started",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "core_connection": [
                "core_connection_not_configured",
                "bootstrap_discovery_not_started",
                "onboarding_session_not_started",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "bootstrap_discovery": [
                "bootstrap_discovery_not_started",
                "onboarding_session_not_started",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "registration": [
                "onboarding_session_not_started",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "approval": [
                "approval_pending",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "trust_activation": [
                "trust_activation_pending",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "provider_setup": [
                *(
                    onboarding_state.provider_setup.blocking_reasons
                    or ["provider_selection_required"]
                ),
                "capability_declaration_not_started",
                "governance_sync_not_started",
            ],
            "capability_declaration": [
                "capability_declaration_not_started",
                "governance_sync_not_started",
            ],
            "governance_sync": [
                "governance_sync_not_started",
            ],
            "ready": [],
        }
        return blockers_by_step.get(current_step_id, blockers_by_step[initial_onboarding_step().step_id])

    def _onboarding_state_label(self, current_step_id: str) -> tuple[str, str]:
        labels = {
            "node_identity": ("waiting_for_local_setup", "configure_node_identity"),
            "core_connection": ("waiting_for_local_setup", "configure_core_connection"),
            "bootstrap_discovery": ("bootstrap_pending", "run_bootstrap_discovery"),
            "registration": ("ready_to_register", "start_onboarding_session"),
            "approval": ("pending_approval", "review_approval_in_core"),
            "trust_activation": ("approval_granted", "finalize_trust_activation"),
            "provider_setup": ("trust_activated", "configure_provider_setup"),
            "capability_declaration": ("capability_setup_pending", "declare_node_capabilities"),
            "governance_sync": ("governance_pending", "refresh_governance"),
            "ready": ("operational", "monitor_operational_state"),
        }
        return labels.get(current_step_id, labels[initial_onboarding_step().step_id])

    def _step_payloads(self) -> list[OnboardingStepResponse]:
        onboarding_state = self._state()
        current_step = self._current_step(onboarding_state)
        step_ids = [step.step_id for step in CANONICAL_ONBOARDING_STEPS]
        current_index = step_ids.index(current_step.step_id)
        return [
            OnboardingStepResponse(
                step_id=step.step_id,
                label=step.label,
                lifecycle_state=step.lifecycle_state,
                phase=step.phase,
                complete=step_ids.index(step.step_id) < current_index,
                current=step.step_id == current_step.step_id,
            )
            for step in CANONICAL_ONBOARDING_STEPS
        ]

    def status_payload(self) -> NodeStatusResponse:
        onboarding_state = self._state()
        current_step = self._current_step(onboarding_state)
        trust_state = self._trust_state(onboarding_state)
        blockers = self._blocking_reasons(current_step.step_id)
        return NodeStatusResponse(
            node_name=self._node_name(onboarding_state),
            node_type=self._settings.node_type,
            node_id=self._node_id(onboarding_state),
            lifecycle_state=current_step.lifecycle_state,
            current_step_id=current_step.step_id,
            current_step_label=current_step.label,
            trust_state=trust_state,
            operational_ready=onboarding_state.operational_status.operational_ready,
            blocking_reasons=blockers,
        )

    def onboarding_payload(self) -> OnboardingStatusResponse:
        persisted_state = self._state()
        current_step = self._current_step(persisted_state)
        onboarding_state_label, next_action = self._onboarding_state_label(current_step.step_id)
        return OnboardingStatusResponse(
            onboarding_state=onboarding_state_label,
            lifecycle_state=current_step.lifecycle_state,
            trust_state=self._trust_state(persisted_state),
            current_step_id=current_step.step_id,
            current_step_label=current_step.label,
            next_action=next_action,
            session_id=persisted_state.onboarding_session.session_id,
            approval_url=persisted_state.onboarding_session.approval_url,
            expires_at=persisted_state.onboarding_session.expires_at,
            finalize_url=persisted_state.onboarding_session.finalize_url,
            session_state=persisted_state.onboarding_session.session_state,
            last_polled_at=persisted_state.onboarding_session.last_polled_at,
            last_terminal_outcome=persisted_state.onboarding_session.last_terminal_outcome,
            support_state=persisted_state.trust_activation.support_state,
            trust_last_checked_at=persisted_state.trust_activation.trust_last_checked_at,
            trust_message=persisted_state.trust_activation.support_message,
            last_error=persisted_state.onboarding_session.last_error,
            steps=self._step_payloads(),
        )

    def capabilities_payload(self) -> CapabilitySummaryResponse:
        state = self._state()
        return CapabilitySummaryResponse(
            configured=state.provider_setup.enabled_providers,
            declared=state.capability_declaration.declared_capabilities,
            capability_status=state.capability_declaration.capability_status,
            capability_profile_id=state.capability_declaration.capability_profile_id,
            accepted_at=state.capability_declaration.accepted_at,
            governance_version=state.capability_declaration.governance_version,
        )

    def readiness_payload(self) -> GovernanceReadinessResponse:
        return GovernanceReadinessResponse(
            operational_ready=self._state().operational_status.operational_ready,
            degraded=bool(self._state().operational_status.governance_outdated),
            blocking_reasons=self._blocking_reasons(self._current_step(self._state()).step_id),
        )

    def service_status_payload(self) -> ServiceStatusResponse:
        return ServiceStatusResponse(
            backend="defined",
            frontend="defined",
            scheduler="not_started",
        )

    def provider_status_payload(self, *, provider_id: str) -> ProviderStatusResponse:
        state = self._state()
        supported_providers = state.provider_setup.supported_providers or [self._settings.provider_id]
        status = "pending_configuration"
        configured = provider_id in state.provider_setup.enabled_providers
        healthy = configured and state.trust_activation.trust_status == "trusted"

        if provider_id not in supported_providers:
            status = "unknown_provider"
            configured = False
            healthy = False
        elif state.trust_activation.trust_status != "trusted":
            status = "blocked_by_trust"
        elif configured:
            status = "ready_for_capability_declaration"

        return ProviderStatusResponse(
            provider_id=provider_id,
            configured=configured,
            healthy=healthy,
            status=status,
        )
