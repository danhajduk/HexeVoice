from hexevoice.api.models import (
    ApiHealthResponse,
    CapabilitySummaryResponse,
    GovernanceReadinessResponse,
    NodeStatusResponse,
    OnboardingStatusResponse,
    ProviderStatusResponse,
    ServiceStatusResponse,
)
from hexevoice.config.settings import Settings


class NodeRuntimeService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    def api_health_payload(self) -> ApiHealthResponse:
        return ApiHealthResponse(status="ok", version=self._settings.node_software_version)

    def status_payload(self) -> NodeStatusResponse:
        return NodeStatusResponse(
            node_name=self._settings.node_name,
            node_type=self._settings.node_type,
            node_id=None,
            lifecycle_state="bootstrap_required",
            trust_state="untrusted",
            operational_ready=False,
            blocking_reasons=[
                "onboarding_not_started",
                "provider_not_configured",
                "governance_not_synced",
            ],
        )

    def onboarding_payload(self) -> OnboardingStatusResponse:
        return OnboardingStatusResponse(
            onboarding_state="pending_start",
            trust_state="untrusted",
            next_action="start_local_onboarding",
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
