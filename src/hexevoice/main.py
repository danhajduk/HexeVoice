from fastapi import FastAPI
import uvicorn

from hexevoice.api.models import (
    ApiHealthResponse,
    BootstrapAdvertisementRequest,
    BootstrapDiscoveryResponse,
    CapabilitySummaryResponse,
    CoreConnectionSetupRequest,
    CoreConnectionSetupResponse,
    GovernanceReadinessResponse,
    LocalSetupStateResponse,
    NodeStatusResponse,
    NodeIdentitySetupRequest,
    NodeIdentitySetupResponse,
    OnboardingSessionPollResponse,
    OnboardingSessionStartResponse,
    OnboardingStatusResponse,
    ProviderStatusResponse,
    ProviderSetupRequest,
    ProviderSetupResponse,
    ServiceStatusResponse,
    TrustActivationFinalizeResponse,
    TrustStatusRefreshResponse,
)
from hexevoice.onboarding.approval import ApprovalPollingService
from hexevoice.config.settings import Settings
from hexevoice.onboarding.bootstrap import BootstrapDiscoveryService
from hexevoice.onboarding.session_start import OnboardingSessionStartService
from hexevoice.onboarding.service import OnboardingStateService
from hexevoice.onboarding.trust_activation import TrustActivationService
from hexevoice.persistence import OnboardingStateStore
from hexevoice.providers.setup import ProviderSetupService
from hexevoice.runtime.service import NodeRuntimeService
from hexevoice.trust.status import TrustStatusService


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    onboarding_state_store = OnboardingStateStore(path=app_settings.resolved_onboarding_state_path())
    onboarding_state_service = OnboardingStateService(onboarding_state_store=onboarding_state_store)
    bootstrap_service = BootstrapDiscoveryService(settings=app_settings, onboarding_state_store=onboarding_state_store)
    session_start_service = OnboardingSessionStartService(
        settings=app_settings,
        onboarding_state_store=onboarding_state_store,
    )
    approval_service = ApprovalPollingService(
        settings=app_settings,
        onboarding_state_store=onboarding_state_store,
    )
    trust_activation_service = TrustActivationService(onboarding_state_store=onboarding_state_store)
    trust_status_service = TrustStatusService(onboarding_state_store=onboarding_state_store)
    provider_setup_service = ProviderSetupService(settings=app_settings, onboarding_state_store=onboarding_state_store)
    service = NodeRuntimeService(settings=app_settings, onboarding_state_store=onboarding_state_store)
    app = FastAPI(title="HexeVoice")

    @app.get("/health/live")
    async def health_live():
        return {"live": True}

    @app.get("/health/ready")
    async def health_ready():
        return {"ready": service.readiness_payload().operational_ready}

    @app.get("/api/health", response_model=ApiHealthResponse)
    async def api_health() -> ApiHealthResponse:
        return service.api_health_payload()

    @app.get("/api/node/status", response_model=NodeStatusResponse)
    async def node_status() -> NodeStatusResponse:
        return service.status_payload()

    @app.get("/api/onboarding/status", response_model=OnboardingStatusResponse)
    async def onboarding_status() -> OnboardingStatusResponse:
        return service.onboarding_payload()

    @app.get("/api/onboarding/local-setup", response_model=LocalSetupStateResponse)
    async def local_setup_state() -> LocalSetupStateResponse:
        return onboarding_state_service.local_setup_payload()

    @app.put("/api/onboarding/local-setup/node-identity", response_model=NodeIdentitySetupResponse)
    async def save_node_identity(payload: NodeIdentitySetupRequest) -> NodeIdentitySetupResponse:
        return onboarding_state_service.save_node_identity(payload)

    @app.put("/api/onboarding/local-setup/core-connection", response_model=CoreConnectionSetupResponse)
    async def save_core_connection(payload: CoreConnectionSetupRequest) -> CoreConnectionSetupResponse:
        return onboarding_state_service.save_core_connection(payload)

    @app.get("/api/onboarding/bootstrap-discovery", response_model=BootstrapDiscoveryResponse)
    async def bootstrap_discovery_status() -> BootstrapDiscoveryResponse:
        return bootstrap_service.status_payload()

    @app.post("/api/onboarding/bootstrap-discovery/test-connection", response_model=BootstrapDiscoveryResponse)
    async def bootstrap_discovery_test_connection() -> BootstrapDiscoveryResponse:
        return bootstrap_service.test_connection()

    @app.put("/api/onboarding/bootstrap-discovery/advertisement", response_model=BootstrapDiscoveryResponse)
    async def bootstrap_discovery_validate_advertisement(
        payload: BootstrapAdvertisementRequest,
    ) -> BootstrapDiscoveryResponse:
        return bootstrap_service.validate_advertisement(payload)

    @app.post("/api/onboarding/session/start", response_model=OnboardingSessionStartResponse)
    async def onboarding_session_start() -> OnboardingSessionStartResponse:
        return session_start_service.start_session()

    @app.post("/api/onboarding/session/poll", response_model=OnboardingSessionPollResponse)
    async def onboarding_session_poll() -> OnboardingSessionPollResponse:
        return approval_service.poll_session()

    @app.post("/api/onboarding/trust-activation/finalize", response_model=TrustActivationFinalizeResponse)
    async def onboarding_trust_activation_finalize() -> TrustActivationFinalizeResponse:
        return trust_activation_service.finalize_activation()

    @app.post("/api/onboarding/trust-status/refresh", response_model=TrustStatusRefreshResponse)
    async def onboarding_trust_status_refresh() -> TrustStatusRefreshResponse:
        return trust_status_service.refresh_status()

    @app.get("/api/providers/setup", response_model=ProviderSetupResponse)
    async def provider_setup_status() -> ProviderSetupResponse:
        return provider_setup_service.status_payload()

    @app.put("/api/providers/setup", response_model=ProviderSetupResponse)
    async def provider_setup_save(payload: ProviderSetupRequest) -> ProviderSetupResponse:
        return provider_setup_service.save_setup(payload)

    @app.get("/api/capabilities", response_model=CapabilitySummaryResponse)
    async def capabilities_status() -> CapabilitySummaryResponse:
        return service.capabilities_payload()

    @app.get("/api/governance/readiness", response_model=GovernanceReadinessResponse)
    async def governance_readiness() -> GovernanceReadinessResponse:
        return service.readiness_payload()

    @app.get("/api/services/status", response_model=ServiceStatusResponse)
    async def services_status() -> ServiceStatusResponse:
        return service.service_status_payload()

    @app.get("/api/providers/{provider_id}/status", response_model=ProviderStatusResponse)
    async def provider_status(provider_id: str) -> ProviderStatusResponse:
        return service.provider_status_payload(provider_id=provider_id)

    return app


def main() -> None:
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
