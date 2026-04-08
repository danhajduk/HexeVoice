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


class NodeRuntimeService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    def api_health_payload(self) -> ApiHealthResponse:
        return ApiHealthResponse(status="ok", version=self._settings.node_software_version)

    def _current_step(self):
        return initial_onboarding_step()

    def _step_payloads(self) -> list[OnboardingStepResponse]:
        current_step = self._current_step()
        return [
            OnboardingStepResponse(
                step_id=step.step_id,
                label=step.label,
                lifecycle_state=step.lifecycle_state,
                phase=step.phase,
                current=step.step_id == current_step.step_id,
            )
            for step in CANONICAL_ONBOARDING_STEPS
        ]

    def status_payload(self) -> NodeStatusResponse:
        current_step = self._current_step()
        return NodeStatusResponse(
            node_name=self._settings.node_name,
            node_type=self._settings.node_type,
            node_id=None,
            lifecycle_state=current_step.lifecycle_state,
            current_step_id=current_step.step_id,
            current_step_label=current_step.label,
            trust_state="untrusted",
            operational_ready=False,
            blocking_reasons=[
                "node_identity_not_configured",
                "core_connection_not_configured",
                "bootstrap_discovery_not_started",
                "onboarding_session_not_started",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
        )

    def onboarding_payload(self) -> OnboardingStatusResponse:
        current_step = self._current_step()
        return OnboardingStatusResponse(
            onboarding_state="waiting_for_local_setup",
            lifecycle_state=current_step.lifecycle_state,
            trust_state="untrusted",
            current_step_id=current_step.step_id,
            current_step_label=current_step.label,
            next_action="configure_node_identity",
            steps=self._step_payloads(),
        )

    def capabilities_payload(self) -> CapabilitySummaryResponse:
        return CapabilitySummaryResponse(configured=[], declared=[])

    def readiness_payload(self) -> GovernanceReadinessResponse:
        status = self.status_payload()
        return GovernanceReadinessResponse(
            operational_ready=status.operational_ready,
            degraded=False,
            blocking_reasons=status.blocking_reasons,
        )

    def service_status_payload(self) -> ServiceStatusResponse:
        return ServiceStatusResponse(
            backend="defined",
            frontend="defined",
            scheduler="not_started",
        )

    def provider_status_payload(self, *, provider_id: str) -> ProviderStatusResponse:
        status = "pending_configuration"
        if provider_id != self._settings.provider_id:
            status = "unknown_provider"

        return ProviderStatusResponse(
            provider_id=provider_id,
            configured=False,
            healthy=False,
            status=status,
        )
