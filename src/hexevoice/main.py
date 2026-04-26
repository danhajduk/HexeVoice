import asyncio
import hashlib
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse
import uvicorn

from hexevoice.api.models import (
    ApiHealthResponse,
    AssistantTurnRequest,
    AssistantTurnResponse,
    BootstrapAdvertisementRequest,
    BootstrapDiscoveryResponse,
    CapabilityDeclarationResponse,
    CapabilitySummaryResponse,
    CoreConnectionSetupRequest,
    CoreConnectionSetupResponse,
    GovernanceBundleResponse,
    GovernanceRefreshResponse,
    GovernanceReadinessResponse,
    LocalSetupStateResponse,
    NodeStatusResponse,
    NodeIdentitySetupRequest,
    NodeIdentitySetupResponse,
    EndpointHeartbeatRequest,
    EndpointHeartbeatResponse,
    EndpointMetadataUpdateRequest,
    EndpointRegistryListResponse,
    EndpointStatusResponse,
    EndpointVolumeCommandRequest,
    EndpointVolumeCommandResponse,
    FirmwareOtaPushRequest,
    FirmwareOtaPushResponse,
    OnboardingSessionPollResponse,
    OnboardingSessionStartResponse,
    OnboardingStatusResponse,
    ProviderStatusResponse,
    ServiceActionRequest,
    ServiceActionResponse,
    ProviderSetupRequest,
    ProviderSetupResponse,
    ServiceStatusResponse,
    TrustActivationFinalizeResponse,
    TrustStatusRefreshResponse,
    OperationalStatusResponse,
)
from hexevoice.assistant import AssistantTurnService
from hexevoice.capabilities.service import CapabilityDeclarationService
from hexevoice.endpoint.service import EndpointHeartbeatService
from hexevoice.onboarding.approval import ApprovalPollingService
from hexevoice.config.settings import Settings
from hexevoice.governance.service import GovernanceService
from hexevoice.onboarding.bootstrap import BootstrapDiscoveryService
from hexevoice.onboarding.session_start import OnboardingSessionStartService
from hexevoice.onboarding.service import OnboardingStateService
from hexevoice.onboarding.trust_activation import TrustActivationService
from hexevoice.persistence import EndpointRegistryStore, OnboardingStateStore
from hexevoice.providers.setup import ProviderSetupService
from hexevoice.runtime.service import NodeRuntimeService
from hexevoice.supervisor.client import SupervisorApiClient
from hexevoice.trust.status import TrustStatusService
from hexevoice.voice import VoiceSessionManager, WakeDetector
from hexevoice.voice.pipeline import build_voice_turn_pipeline
from hexevoice.voice.wake import build_wake_detector


def configure_backend_logging(settings: Settings) -> Path:
    log_path = settings.resolved_backend_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    level = getattr(logging, settings.backend_log_level.upper(), logging.INFO)

    logger = logging.getLogger("hexevoice")
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, "_hexevoice_backend_handler", False):
            logger.removeHandler(handler)
            handler.close()

    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=settings.backend_log_backup_days,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    file_handler._hexevoice_backend_handler = True  # type: ignore[attr-defined]
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)
    stream_handler._hexevoice_backend_handler = True  # type: ignore[attr-defined]
    logger.addHandler(stream_handler)
    logger.info(
        "Backend logging initialized: path=%s level=%s rotation=midnight backup_days=%s",
        log_path,
        settings.backend_log_level,
        settings.backend_log_backup_days,
    )
    return log_path


def create_app(
    settings: Settings | None = None,
    voice_session_manager: VoiceSessionManager | None = None,
    voice_wake_detector: WakeDetector | None = None,
) -> FastAPI:
    app_settings = settings or Settings()
    onboarding_state_store = OnboardingStateStore(path=app_settings.resolved_onboarding_state_path())
    endpoint_registry_store = EndpointRegistryStore(path=app_settings.resolved_endpoint_registry_path())
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
    capability_service = CapabilityDeclarationService(
        settings=app_settings,
        onboarding_state_store=onboarding_state_store,
    )
    governance_service = GovernanceService(onboarding_state_store=onboarding_state_store)
    endpoint_service = EndpointHeartbeatService(
        endpoint_registry_store=endpoint_registry_store,
        stale_after_seconds=app_settings.endpoint_stale_after_seconds,
    )
    supervisor_enabled = os.getenv("HEXE_SUPERVISOR_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    supervisor_client = SupervisorApiClient() if supervisor_enabled else None
    service = NodeRuntimeService(
        settings=app_settings,
        onboarding_state_store=onboarding_state_store,
        supervisor_client=supervisor_client,
    )
    assistant_service = AssistantTurnService(settings=app_settings, runtime_service=service)
    voice_turn_pipeline = build_voice_turn_pipeline(settings=app_settings, assistant_service=assistant_service)
    voice_session_manager = voice_session_manager or VoiceSessionManager(
        wake_detector=voice_wake_detector or build_wake_detector(app_settings),
        turn_pipeline=voice_turn_pipeline,
    )
    app = FastAPI(title="HexeVoice")

    @app.on_event("startup")
    async def start_supervisor_heartbeat():
        if app_settings.voice_wake_preload:
            voice_session_manager.preload_wake_detector()
        if app_settings.voice_stt_preload:
            asyncio.create_task(asyncio.to_thread(voice_session_manager.preload_turn_pipeline))

        async def loop():
            while True:
                await service.supervisor_heartbeat_once()
                await asyncio.sleep(5)

        if supervisor_enabled:
            asyncio.create_task(loop())

    @app.get("/health/live")
    async def health_live():
        return {"live": True}

    @app.get("/health/ready")
    async def health_ready():
        return {"ready": service.readiness_payload().operational_ready}

    @app.get("/api/health", response_model=ApiHealthResponse)
    async def api_health() -> ApiHealthResponse:
        return service.api_health_payload()

    @app.post("/api/assistant/turn", response_model=AssistantTurnResponse)
    async def assistant_turn(payload: AssistantTurnRequest) -> AssistantTurnResponse:
        return assistant_service.handle_turn(payload)

    @app.post("/api/endpoint/heartbeat", response_model=EndpointHeartbeatResponse)
    async def endpoint_heartbeat(payload: EndpointHeartbeatRequest) -> EndpointHeartbeatResponse:
        return endpoint_service.record_heartbeat(payload)

    @app.get("/api/endpoint/status", response_model=EndpointStatusResponse)
    async def latest_endpoint_status() -> EndpointStatusResponse:
        return endpoint_service.latest_status()

    @app.get("/api/endpoint/status/{endpoint_id}", response_model=EndpointStatusResponse)
    async def endpoint_status(endpoint_id: str) -> EndpointStatusResponse:
        return endpoint_service.status(endpoint_id)

    @app.get("/api/endpoints", response_model=EndpointRegistryListResponse)
    async def endpoint_registry() -> EndpointRegistryListResponse:
        return endpoint_service.list_statuses()

    @app.patch("/api/endpoints/{endpoint_id}", response_model=EndpointStatusResponse)
    async def endpoint_metadata_update(
        endpoint_id: str,
        payload: EndpointMetadataUpdateRequest,
    ) -> EndpointStatusResponse:
        return endpoint_service.update_metadata(endpoint_id, payload)

    @app.post("/api/endpoint/volume", response_model=EndpointVolumeCommandResponse)
    async def endpoint_volume(payload: EndpointVolumeCommandRequest) -> EndpointVolumeCommandResponse:
        result = await voice_session_manager.push_volume_command(
            endpoint_id=payload.endpoint_id,
            volume_percent=payload.volume_percent,
        )
        return EndpointVolumeCommandResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            volume_percent=payload.volume_percent,
            reason=result.get("reason"),
        )

    def firmware_artifact_path(filename: str) -> Path:
        artifact_dir = app_settings.resolved_firmware_artifact_dir().resolve()
        candidate = (artifact_dir / filename).resolve()
        if candidate.parent != artifact_dir or candidate.suffix != ".bin":
            raise HTTPException(status_code=400, detail="invalid_firmware_filename")
        if not candidate.exists():
            raise HTTPException(status_code=404, detail="firmware_artifact_not_found")
        return candidate

    def firmware_public_url(filename: str) -> str:
        base_url = app_settings.public_api_base_url or f"http://127.0.0.1:{app_settings.api_port}"
        return f"{base_url.rstrip('/')}/api/firmware/artifacts/{filename}"

    @app.get("/api/firmware/artifacts/{filename}")
    async def firmware_artifact(filename: str) -> FileResponse:
        return FileResponse(firmware_artifact_path(filename), media_type="application/octet-stream")

    @app.get("/api/firmware/manifest")
    async def firmware_manifest(filename: str = "hexe_firmware.bin") -> dict:
        path = firmware_artifact_path(filename)
        return {
            "filename": path.name,
            "url": firmware_public_url(path.name),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        }

    @app.post("/api/firmware/ota/push", response_model=FirmwareOtaPushResponse)
    async def firmware_ota_push(payload: FirmwareOtaPushRequest) -> FirmwareOtaPushResponse:
        path = firmware_artifact_path(payload.filename)
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        url = firmware_public_url(path.name)
        result = await voice_session_manager.push_ota_update(
            endpoint_id=payload.endpoint_id,
            firmware_url=url,
            version=payload.version,
            sha256=sha256,
            size_bytes=path.stat().st_size,
        )
        return FirmwareOtaPushResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            firmware_url=url,
            version=payload.version,
            sha256=sha256,
            size_bytes=path.stat().st_size,
            reason=result.get("reason"),
        )

    @app.websocket("/api/voice/ws")
    async def voice_websocket(websocket: WebSocket) -> None:
        await voice_session_manager.handle_websocket(websocket)

    @app.get("/api/voice/status")
    async def voice_status() -> dict:
        return voice_session_manager.status()

    @app.get("/api/voice/tts/{stream_id}")
    async def voice_tts_audio(stream_id: str) -> FileResponse:
        if not stream_id.startswith("tts-") or not stream_id.replace("-", "").isalnum():
            raise HTTPException(status_code=404, detail="tts_stream_not_found")
        tts_dir = app_settings.runtime_dir / "voice_tts"
        candidates = list(tts_dir.glob(f"{stream_id}.*"))
        if not candidates:
            raise HTTPException(status_code=404, detail="tts_stream_not_found")
        return FileResponse(candidates[0])

    @app.post("/api/voice/session/cancel")
    async def voice_session_cancel() -> dict:
        return voice_session_manager.cancel_from_operator()

    @app.get("/api/node/status", response_model=NodeStatusResponse)
    async def node_status() -> NodeStatusResponse:
        return service.status_payload()

    @app.get("/api/onboarding/status", response_model=OnboardingStatusResponse)
    async def onboarding_status() -> OnboardingStatusResponse:
        return service.onboarding_payload()

    @app.get("/api/onboarding/local-setup", response_model=LocalSetupStateResponse)
    async def local_setup_state() -> LocalSetupStateResponse:
        return onboarding_state_service.local_setup_payload()

    @app.post("/api/onboarding/restart", response_model=LocalSetupStateResponse)
    async def restart_onboarding_setup() -> LocalSetupStateResponse:
        return onboarding_state_service.restart_setup()

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

    @app.post("/api/capabilities/declaration", response_model=CapabilityDeclarationResponse)
    async def capabilities_declaration() -> CapabilityDeclarationResponse:
        return capability_service.declare()

    @app.get("/api/governance/current", response_model=GovernanceBundleResponse)
    async def governance_current() -> GovernanceBundleResponse:
        return governance_service.current()

    @app.post("/api/governance/refresh", response_model=GovernanceRefreshResponse)
    async def governance_refresh() -> GovernanceRefreshResponse:
        return governance_service.refresh()

    @app.get("/api/governance/readiness", response_model=GovernanceReadinessResponse)
    async def governance_readiness() -> GovernanceReadinessResponse:
        return service.readiness_payload()

    @app.get("/api/node/operational-status", response_model=OperationalStatusResponse)
    async def node_operational_status() -> OperationalStatusResponse:
        return governance_service.operational_status()

    @app.get("/api/services/status", response_model=ServiceStatusResponse)
    async def services_status() -> ServiceStatusResponse:
        return service.service_status_payload()

    @app.post("/api/services/start", response_model=ServiceActionResponse)
    async def service_start(payload: ServiceActionRequest) -> ServiceActionResponse:
        return await asyncio.to_thread(service.service_action, target=payload.target, action="start")

    @app.post("/api/services/stop", response_model=ServiceActionResponse)
    async def service_stop(payload: ServiceActionRequest) -> ServiceActionResponse:
        return await asyncio.to_thread(service.service_action, target=payload.target, action="stop")

    @app.post("/api/services/restart", response_model=ServiceActionResponse)
    async def service_restart(payload: ServiceActionRequest) -> ServiceActionResponse:
        return await asyncio.to_thread(service.service_action, target=payload.target, action="restart")

    @app.get("/api/providers/{provider_id}/status", response_model=ProviderStatusResponse)
    async def provider_status(provider_id: str) -> ProviderStatusResponse:
        return service.provider_status_payload(provider_id=provider_id)

    return app


def main() -> None:
    settings = Settings()
    configure_backend_logging(settings)
    logging.getLogger("hexevoice").info(
        "Starting HexeVoice backend: host=%s port=%s wake_provider=%s stt_provider=%s tts_provider=%s",
        settings.api_host,
        settings.api_port,
        settings.voice_wake_provider,
        settings.voice_stt_provider,
        settings.voice_tts_provider,
    )
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
