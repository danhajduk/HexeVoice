from fastapi import FastAPI
import uvicorn

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
from hexevoice.persistence import OnboardingStateStore
from hexevoice.runtime.service import NodeRuntimeService


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    onboarding_state_store = OnboardingStateStore(path=app_settings.resolved_onboarding_state_path())
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
