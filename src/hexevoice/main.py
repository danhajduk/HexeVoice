import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import time

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse
import httpx
import uvicorn

from hexevoice.api.models import (
    ApiHealthResponse,
    AssistantTurnRequest,
    AssistantTurnResponse,
    BootstrapAdvertisementRequest,
    BootstrapDiscoveryResponse,
    CapabilityDeclarationResponse,
    CapabilitySelectionRequest,
    CapabilitySummaryResponse,
    CoreConnectionSetupRequest,
    CoreConnectionSetupResponse,
    GovernanceBundleResponse,
    GovernanceRefreshResponse,
    GovernanceReadinessResponse,
    VoiceIntentDispatchRequest,
    VoiceIntentDispatchResponse,
    VoiceIntentInvokeResponse,
    VoiceIntentLifecycleRequest,
    VoiceIntentLookupResponse,
    VoiceIntentRegisterRequest,
    VoiceIntentReviewRequest,
    VoiceIntentStateResponse,
    VoiceIntentUpdateRequest,
    VoiceSessionHistoryDetailResponse,
    VoiceSessionHistoryListResponse,
    VoiceSessionReplayRequest,
    LocalSetupStateResponse,
    NodeMigrationExportRequest,
    NodeMigrationBackupRequest,
    NodeMigrationBackupResponse,
    NodeMigrationImportRequest,
    NodeMigrationImportResponse,
    NodeMigrationPreflightRequest,
    NodeMigrationPreflightResponse,
    NodeMigrationRestoreRequest,
    NodeStatusResponse,
    NodeIdentitySetupRequest,
    NodeIdentitySetupResponse,
    EndpointHeartbeatRequest,
    EndpointHeartbeatResponse,
    EndpointCommandRequest,
    EndpointCommandResponse,
    EndpointLedSimulateCommandRequest,
    EndpointMetadataUpdateRequest,
    EndpointMicroVadCommandRequest,
    EndpointMuteCommandRequest,
    EndpointMediaAssetResponse,
    EndpointMediaDeliverRequest,
    EndpointMediaDeliverResponse,
    EndpointMediaInventoryItem,
    EndpointMediaInventoryResponse,
    EndpointMediaListResponse,
    EndpointMediaUploadRequest,
    EndpointRegistryListResponse,
    EndpointStatusResponse,
    EndpointSpeakCommandRequest,
    EndpointTimeResponse,
    EndpointVolumeCommandRequest,
    EndpointVolumeCommandResponse,
    EndpointVolumeStatusResponse,
    FirmwareOtaPushRequest,
    FirmwareOtaPushResponse,
    OnboardingSessionPollResponse,
    OnboardingSessionStartResponse,
    OnboardingStatusResponse,
    ProviderConfigRequest,
    ProviderStatusResponse,
    ServiceActionRequest,
    ServiceActionResponse,
    SetupBootstrapStatusResponse,
    SetupHostReadinessActionRequest,
    SetupHostReadinessActionResponse,
    SetupHostReadinessResponse,
    SetupCoreConnectionResponse,
    SetupReauthFinalizeResponse,
    SetupReauthStartResponse,
    SetupTrustRecoveryActionResponse,
    ProviderSetupRequest,
    ProviderSetupResponse,
    ServiceStatusResponse,
    TrustActivationFinalizeResponse,
    TrustStatusRefreshResponse,
    TtsSynthesizeRequest,
    TtsSynthesizeTarget,
    TtsSynthesizeResponse,
    OperationalStatusResponse,
)
from hexevoice.assistant import AssistantTurnService, LocalIntentFinder, VoiceIntentRegistry, VoiceIntentStateStore
from hexevoice.capabilities.service import CapabilityDeclarationService
from hexevoice.capabilities.schema import CapabilityManifestValidationError, validate_capability_declaration
from hexevoice.endpoint.media import EndpointMediaAsset, EndpointMediaService, EndpointMediaValidationError
from hexevoice.endpoint.service import EndpointHeartbeatService
from hexevoice.engine_http import async_client_for_engine
from hexevoice.onboarding.approval import ApprovalPollingService
from hexevoice.config.settings import Settings
from hexevoice.governance.service import GovernanceService
from hexevoice import node_ui
from hexevoice.migration import NodeMigrationError, NodeMigrationService
from hexevoice.onboarding.bootstrap import BootstrapDiscoveryService
from hexevoice.onboarding.registration_metadata import RegistrationMetadataRefreshService
from hexevoice.onboarding.session_start import OnboardingSessionStartService
from hexevoice.onboarding.service import OnboardingStateService
from hexevoice.onboarding.trust_activation import TrustActivationService
from hexevoice.persistence import EndpointRegistryStore, OnboardingStateStore, VoiceSessionHistoryStore
from hexevoice.providers.setup import ProviderSetupService
from hexevoice.runtime.service import NodeRuntimeService
from hexevoice.setup_bootstrap import SetupBootstrapStatusService
from hexevoice.setup_host import SetupHostReadinessService
from hexevoice.setup_reauth import SetupReauthService
from hexevoice.setup_trust import SetupTrustRecoveryService
from hexevoice.supervisor.client import SupervisorApiClient
from hexevoice.timer_announcements import TimerSucceededAnnouncementService
from hexevoice.trust.status import TrustStatusService
from hexevoice.tts import TtsAudioService
from hexevoice.tts.runtime_settings import TtsRuntimeSettingsService
from hexevoice.voice import MicroVadChunkRecordingService, VoiceSessionManager, WakeDetector, WakeRecordingService
from hexevoice.voice.pipeline import build_voice_turn_pipeline
from hexevoice.stt_profiles import resolve_stt_model_profile
from hexevoice.stt_profiles import stt_profile_options
from hexevoice.voice.wake import build_wake_detector


def setup_provider_action_sequence(action: str) -> tuple[str, ...]:
    normalized = str(action or "install").strip().lower()
    if normalized in {"download-model", "download-models", "sync-models"}:
        return ("download-models",)
    if normalized == "preload":
        return ("preload",)
    if normalized == "restart":
        return ("restart",)
    if normalized in {"recreate", "recreate-containers"}:
        return ("restart",)
    if normalized == "start":
        return ("start",)
    if normalized in {"install", "build", "rebuild-env", "rebuild-config"}:
        return ("install", "start")
    return ("install", "start")


def configure_backend_logging(settings: Settings) -> Path:
    log_path = settings.resolved_backend_log_path()
    voice_record_log_path = settings.resolved_voice_record_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    voice_record_log_path.parent.mkdir(parents=True, exist_ok=True)
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

    voice_record_logger = logging.getLogger("hexevoice.voice.records")
    voice_record_logger.setLevel(logging.INFO)
    voice_record_logger.propagate = False
    for handler in list(voice_record_logger.handlers):
        if getattr(handler, "_hexevoice_voice_record_handler", False):
            voice_record_logger.removeHandler(handler)
            handler.close()
    voice_record_handler = TimedRotatingFileHandler(
        voice_record_log_path,
        when="midnight",
        interval=1,
        backupCount=settings.backend_log_backup_days,
        encoding="utf-8",
    )
    voice_record_handler.setFormatter(formatter)
    voice_record_handler.setLevel(logging.INFO)
    voice_record_handler._hexevoice_voice_record_handler = True  # type: ignore[attr-defined]
    voice_record_logger.addHandler(voice_record_handler)

    logger.info(
        "Backend logging initialized: path=%s voice_record_path=%s level=%s rotation=midnight backup_days=%s",
        log_path,
        voice_record_log_path,
        settings.backend_log_level,
        settings.backend_log_backup_days,
    )
    return log_path


def _tts_warmup_voices(settings: Settings, *, discovered_warm_voices: list[str] | None = None) -> list[str | None]:
    configured_warm_voices = settings.resolved_piper_tts_warm_voices() or (discovered_warm_voices or [])
    voices: list[str | None] = list(configured_warm_voices)
    voices.extend(settings.resolved_voice_tts_endpoint_voices().values())
    if settings.voice_tts_piper_voice:
        voices.insert(0, settings.voice_tts_piper_voice)
    if not voices:
        voices.append(None)

    deduped: list[str | None] = []
    seen: set[str] = set()
    for voice in voices:
        key = str(voice or "default").strip()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(voice)
    return deduped


async def _discover_piper_warm_voices(settings: Settings) -> list[str]:
    base_url = settings.resolved_voice_tts_piper_base_url()
    if not base_url:
        return []
    try:
        async with async_client_for_engine(
            timeout=2.0,
            socket_path=settings.resolved_voice_tts_piper_socket_path(),
        ) as client:
            response = await client.get(f"{base_url.rstrip('/')}/health")
            response.raise_for_status()
    except httpx.HTTPError:
        return []
    payload = response.json()
    warm_voices = payload.get("warm_voices") if isinstance(payload, dict) else None
    if not isinstance(warm_voices, list):
        return []
    return [str(voice).strip() for voice in warm_voices if str(voice).strip()]


def _seconds_until_next_local_midnight(now: datetime | None = None) -> float:
    current = now or datetime.now().astimezone()
    next_midnight = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1.0, (next_midnight - current).total_seconds())


def cleanup_voice_artifacts_once(*, tts_audio_service, wake_recorder, micro_vad_chunk_recorder=None) -> dict:
    tts_audio_service.cleanup_expired()
    result = {"tts": {"expired_cleanup": "completed"}}
    if wake_recorder is not None:
        result["wake_recordings"] = wake_recorder.cleanup_expired()
    if micro_vad_chunk_recorder is not None:
        result["micro_vad_chunks"] = micro_vad_chunk_recorder.cleanup_expired()
    return result


def firmware_artifact_path_for_settings(settings: Settings, filename: str) -> Path:
    artifact_dir = settings.resolved_firmware_artifact_dir().resolve()
    candidate = (artifact_dir / filename).resolve()
    if candidate.parent != artifact_dir or candidate.suffix != ".bin":
        raise HTTPException(status_code=400, detail="invalid_firmware_filename")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="firmware_artifact_not_found")
    return candidate


def firmware_public_url_for_settings(settings: Settings, filename: str) -> str:
    base_url = settings.public_api_base_url or f"http://127.0.0.1:{settings.api_port}"
    return f"{base_url.rstrip('/')}/api/firmware/artifacts/{filename}"


def endpoint_board_profile(endpoint_status: EndpointStatusResponse) -> str:
    capabilities = endpoint_status.capabilities or {}
    firmware = capabilities.get("firmware") if isinstance(capabilities, dict) else None
    if isinstance(firmware, dict):
        for key in ("board_profile", "profile"):
            value = str(firmware.get(key) or "").strip()
            if value:
                return value
    endpoint_id = endpoint_status.endpoint_id.lower()
    if "pe" in endpoint_id or "ha_voice" in endpoint_id:
        return "ha_voice_pe"
    return "esp_box_3"


def firmware_update_payload(settings: Settings, endpoint_status: EndpointStatusResponse) -> dict:
    profile = endpoint_board_profile(endpoint_status)
    artifact_dir = settings.resolved_firmware_artifact_dir()
    manifest_path = artifact_dir / f"manifest-{profile}.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
    filename = str(manifest.get("filename") or f"hexe_firmware_{profile}.bin")
    path = artifact_dir / filename
    fallback_path = artifact_dir / "hexe_firmware.bin"
    if not path.exists() and fallback_path.exists():
        filename = fallback_path.name
        path = fallback_path
    latest_version = str(manifest.get("version") or "").strip() or None
    current_version = endpoint_status.firmware_version
    artifact_available = path.exists()
    update_available = bool(artifact_available and latest_version and current_version and latest_version != current_version)
    reason = None
    if not artifact_available:
        reason = "firmware_artifact_not_found"
    elif not latest_version:
        reason = "latest_version_unknown"
    elif not current_version:
        reason = "endpoint_version_unknown"
    elif not update_available:
        reason = "up_to_date"
    else:
        reason = "update_available"
    return {
        "board_profile": profile,
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "artifact_available": artifact_available,
        "filename": filename if artifact_available else None,
        "url": firmware_public_url_for_settings(settings, filename) if artifact_available else None,
        "sha256": manifest.get("sha256"),
        "created_at_utc": manifest.get("created_at_utc"),
        "reason": reason,
    }


def create_app(
    settings: Settings | None = None,
    voice_session_manager: VoiceSessionManager | None = None,
    voice_wake_detector: WakeDetector | None = None,
) -> FastAPI:
    app_settings = settings or Settings()
    onboarding_state_store = OnboardingStateStore(path=app_settings.resolved_onboarding_state_path())
    endpoint_registry_store = EndpointRegistryStore(path=app_settings.resolved_endpoint_registry_path())
    voice_intent_store = VoiceIntentStateStore(path=app_settings.resolved_voice_intent_registry_path())
    voice_session_history_store = VoiceSessionHistoryStore(
        path=app_settings.resolved_voice_session_history_path(),
        max_records=app_settings.voice_session_history_limit,
    )
    voice_intent_registry = VoiceIntentRegistry(store=voice_intent_store)
    onboarding_state_service = OnboardingStateService(onboarding_state_store=onboarding_state_store)
    node_migration_service = NodeMigrationService(settings=app_settings)
    setup_bootstrap_status_service = SetupBootstrapStatusService(settings=app_settings)
    setup_host_readiness_service = SetupHostReadinessService(settings=app_settings)
    setup_reauth_service = SetupReauthService(onboarding_state_store=onboarding_state_store)
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
    setup_trust_recovery_service = SetupTrustRecoveryService(
        settings=app_settings,
        onboarding_state_store=onboarding_state_store,
        approval_service=approval_service,
        trust_activation_service=trust_activation_service,
        reauth_service=setup_reauth_service,
    )
    trust_status_service = TrustStatusService(onboarding_state_store=onboarding_state_store)
    registration_metadata_refresh_service = RegistrationMetadataRefreshService(
        settings=app_settings,
        onboarding_state_store=onboarding_state_store,
    )
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
    endpoint_media_service = EndpointMediaService(media_dir=app_settings.resolved_endpoint_media_dir())
    supervisor_enabled = os.getenv("HEXE_SUPERVISOR_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    supervisor_client = SupervisorApiClient() if supervisor_enabled else None
    engine_heartbeats: dict[str, dict[str, object]] = {}
    service = NodeRuntimeService(
        settings=app_settings,
        onboarding_state_store=onboarding_state_store,
        supervisor_client=supervisor_client,
        engine_heartbeat_fetcher=lambda: engine_heartbeats,
    )
    assistant_service = AssistantTurnService(
        settings=app_settings,
        runtime_service=service,
        intent_finder=LocalIntentFinder(registry=voice_intent_registry),
    )
    voice_turn_pipeline = build_voice_turn_pipeline(settings=app_settings, assistant_service=assistant_service)
    tts_audio_service = TtsAudioService(settings=app_settings, voice_turn_pipeline=voice_turn_pipeline)
    tts_runtime_settings_service = TtsRuntimeSettingsService(settings=app_settings)
    wake_recorder = (
        WakeRecordingService(
            recording_dir=app_settings.resolved_voice_wake_recording_dir(),
            retention_days=app_settings.voice_wake_recording_retention_days,
            preroll_ms=app_settings.voice_wake_recording_preroll_ms,
        )
        if app_settings.voice_wake_recordings_enabled
        else None
    )
    micro_vad_chunk_recorder = (
        MicroVadChunkRecordingService(
            recording_dir=app_settings.resolved_voice_micro_vad_chunk_dir(),
            retention_days=app_settings.voice_micro_vad_chunk_retention_days,
        )
        if app_settings.voice_micro_vad_chunks_enabled
        else None
    )
    voice_session_manager = voice_session_manager or VoiceSessionManager(
        wake_detector=voice_wake_detector or build_wake_detector(app_settings),
        turn_pipeline=voice_turn_pipeline,
        wake_recorder=wake_recorder,
        micro_vad_chunk_recorder=micro_vad_chunk_recorder,
        session_history_store=voice_session_history_store,
    )
    timer_announcement_service = TimerSucceededAnnouncementService(
        settings=app_settings,
        announce=lambda announcement: voice_session_manager.push_timer_announcement(
            endpoint_id=announcement.endpoint_id,
            session_id=announcement.session_id,
            text=announcement.text,
            source_event_id=announcement.event_id,
        ),
    )
    app = FastAPI(title="HexeVoice")
    node_ui_page_cache = node_ui.PageSnapshotCache(
        cache_dir=app_settings.resolved_onboarding_state_path().parent / "rendered_node_ui_pages"
    )
    app.state.node_ui_page_cache = node_ui_page_cache
    app.state.timer_announcement_service = timer_announcement_service
    app.state.voice_artifact_cleanup_status = {
        "name": "every_5_minutes",
        "interval_seconds": 300,
        "last_run_at": None,
        "last_error": None,
        "last_result": None,
    }
    app.state.voice_tts_warmup_status = {
        "name": "every_10_minutes",
        "interval_seconds": 600,
        "enabled": app_settings.voice_tts_provider == "piper",
        "text": "hello",
        "voices": _tts_warmup_voices(app_settings),
        "last_run_at": None,
        "last_error": None,
        "last_results": [],
    }
    app.state.voice_orphan_cleanup_status = {
        "name": "daily_midnight",
        "scheduled_time_local": "00:00",
        "min_age_seconds": 600,
        "last_run_at": None,
        "last_error": None,
        "last_deleted_count": 0,
    }
    log = logging.getLogger("hexevoice")

    @app.on_event("startup")
    async def start_supervisor_heartbeat():
        if app_settings.voice_wake_preload:
            voice_session_manager.preload_wake_detector()
        if app_settings.voice_stt_preload:
            asyncio.create_task(asyncio.to_thread(voice_session_manager.preload_turn_pipeline))
        asyncio.create_task(reconcile_external_stt_provider_config())
        timer_announcement_service.start(asyncio.get_running_loop())

        async def loop():
            while True:
                await service.supervisor_heartbeat_once()
                await asyncio.sleep(5)

        if supervisor_enabled:
            asyncio.create_task(loop())

        async def cleanup_generated_voice_artifacts_every_5_minutes():
            while True:
                try:
                    cleanup_result = await asyncio.to_thread(
                        cleanup_voice_artifacts_once,
                        tts_audio_service=tts_audio_service,
                        wake_recorder=wake_recorder,
                        micro_vad_chunk_recorder=micro_vad_chunk_recorder,
                    )
                    app.state.voice_artifact_cleanup_status["last_run_at"] = datetime.now(UTC).isoformat()
                    app.state.voice_artifact_cleanup_status["last_error"] = None
                    app.state.voice_artifact_cleanup_status["last_result"] = cleanup_result
                except Exception:
                    app.state.voice_artifact_cleanup_status["last_error"] = "cleanup_failed"
                    log.exception("Generated voice artifact cleanup failed")
                await asyncio.sleep(300)

        app.state.voice_artifact_cleanup_task = asyncio.create_task(cleanup_generated_voice_artifacts_every_5_minutes())

        async def cleanup_orphaned_voice_artifacts_daily_at_midnight():
            while True:
                await asyncio.sleep(_seconds_until_next_local_midnight())
                try:
                    deleted_count = await asyncio.to_thread(
                        tts_audio_service.cleanup_orphaned_audio,
                        min_age_seconds=600,
                    )
                    app.state.voice_orphan_cleanup_status["last_run_at"] = datetime.now(UTC).isoformat()
                    app.state.voice_orphan_cleanup_status["last_error"] = None
                    app.state.voice_orphan_cleanup_status["last_deleted_count"] = deleted_count
                except Exception:
                    app.state.voice_orphan_cleanup_status["last_error"] = "orphan_cleanup_failed"
                    log.exception("Generated voice orphan cleanup failed")

        app.state.voice_orphan_cleanup_task = asyncio.create_task(cleanup_orphaned_voice_artifacts_daily_at_midnight())

        async def refresh_piper_tts_warmup_voices() -> list[str | None]:
            discovered_voices = await _discover_piper_warm_voices(app_settings)
            voices = _tts_warmup_voices(app_settings, discovered_warm_voices=discovered_voices)
            app.state.voice_tts_warmup_status["voices"] = voices
            return voices

        async def warm_piper_tts_every_10_minutes():
            while True:
                await asyncio.sleep(600)
                voices = await refresh_piper_tts_warmup_voices()
                results = []
                try:
                    for voice in voices:
                        response = await asyncio.to_thread(
                            tts_audio_service.synthesize,
                            TtsSynthesizeRequest(
                                target=TtsSynthesizeTarget(device_id="backend-tts-warmup"),
                                text="hello",
                                voice=voice,
                                format="wav",
                            ),
                        )
                        results.append(
                            {
                                "voice": voice or "default",
                                "status": response.status,
                                "stream_id": response.stream_id,
                                "provider_id": response.provider_id,
                                "error": response.error,
                            }
                        )
                    app.state.voice_tts_warmup_status["last_run_at"] = datetime.now(UTC).isoformat()
                    app.state.voice_tts_warmup_status["last_error"] = None
                    app.state.voice_tts_warmup_status["last_results"] = results
                except Exception:
                    app.state.voice_tts_warmup_status["last_error"] = "warmup_failed"
                    log.exception("Piper TTS warmup failed")

        if app_settings.voice_tts_provider == "piper":
            asyncio.create_task(refresh_piper_tts_warmup_voices())
            app.state.voice_tts_warmup_task = asyncio.create_task(warm_piper_tts_every_10_minutes())

        app.state.node_ui_page_refresh_task = asyncio.create_task(node_ui_page_cache.maintain_registered_pages())

    @app.on_event("shutdown")
    async def stop_background_services():
        timer_announcement_service.stop()
        page_refresh_task = getattr(app.state, "node_ui_page_refresh_task", None)
        if page_refresh_task is not None:
            page_refresh_task.cancel()
            try:
                await page_refresh_task
            except asyncio.CancelledError:
                pass
        cleanup_task = getattr(app.state, "voice_artifact_cleanup_task", None)
        if cleanup_task is not None:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
        orphan_cleanup_task = getattr(app.state, "voice_orphan_cleanup_task", None)
        if orphan_cleanup_task is not None:
            orphan_cleanup_task.cancel()
            try:
                await orphan_cleanup_task
            except asyncio.CancelledError:
                pass
        warmup_task = getattr(app.state, "voice_tts_warmup_task", None)
        if warmup_task is not None:
            warmup_task.cancel()
            try:
                await warmup_task
            except asyncio.CancelledError:
                pass

    @app.get("/health/live")
    async def health_live():
        return {"live": True}

    @app.get("/health/ready")
    async def health_ready():
        return {"ready": service.readiness_payload().operational_ready}

    @app.get("/api/health", response_model=ApiHealthResponse)
    async def api_health() -> ApiHealthResponse:
        return service.api_health_payload()

    @app.post("/api/setup/supervisor/register-runtime")
    async def setup_supervisor_register_runtime() -> dict[str, object]:
        result = await service.supervisor_heartbeat_once()
        if result is None:
            return {"status": "skipped", "reason": "supervisor_unavailable"}
        return result

    @app.post("/api/engines/heartbeat")
    async def engine_heartbeat(payload: dict[str, object]) -> dict[str, object]:
        engine_id = str(payload.get("engine_id") or payload.get("service_id") or "").strip()
        if engine_id not in {"faster_whisper_stt", "piper_tts"}:
            raise HTTPException(status_code=400, detail="unsupported_engine_id")
        recorded = dict(payload)
        recorded["engine_id"] = engine_id
        recorded["received_at"] = datetime.now(UTC).isoformat()
        engine_heartbeats[engine_id] = recorded
        return {"ok": True, "engine_id": engine_id}

    @app.post("/api/assistant/turn", response_model=AssistantTurnResponse)
    async def assistant_turn(payload: AssistantTurnRequest) -> AssistantTurnResponse:
        response = assistant_service.handle_turn(payload)
        node_ui_page_cache.invalidate()
        return response

    @app.get("/api/voice/intents", response_model=VoiceIntentStateResponse)
    async def voice_intents_list() -> VoiceIntentStateResponse:
        return VoiceIntentStateResponse.model_validate(voice_intent_registry.snapshot())

    @app.post("/api/voice/intents", response_model=VoiceIntentStateResponse)
    async def voice_intents_register(payload: VoiceIntentRegisterRequest) -> VoiceIntentStateResponse:
        try:
            state = voice_intent_registry.register_intent(**payload.model_dump(mode="python"))
            node_ui_page_cache.invalidate()
            return VoiceIntentStateResponse.model_validate(state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/voice/intents/{intent_id}", response_model=VoiceIntentLookupResponse)
    async def voice_intent_get(intent_id: str) -> VoiceIntentLookupResponse:
        try:
            return VoiceIntentLookupResponse(intent=voice_intent_registry.get_intent(intent_id=intent_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/voice/intents/{intent_id}", response_model=VoiceIntentStateResponse)
    async def voice_intent_update(intent_id: str, payload: VoiceIntentUpdateRequest) -> VoiceIntentStateResponse:
        try:
            state = voice_intent_registry.update_intent(
                intent_id=intent_id,
                **payload.model_dump(mode="python", exclude_unset=True),
            )
            node_ui_page_cache.invalidate()
            return VoiceIntentStateResponse.model_validate(state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/voice/intents/{intent_id}/lifecycle", response_model=VoiceIntentStateResponse)
    async def voice_intent_lifecycle(intent_id: str, payload: VoiceIntentLifecycleRequest) -> VoiceIntentStateResponse:
        try:
            state = voice_intent_registry.transition_intent(
                intent_id=intent_id,
                status=payload.status,
                reason=payload.reason,
            )
            node_ui_page_cache.invalidate()
            return VoiceIntentStateResponse.model_validate(state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/voice/intents/{intent_id}/review", response_model=VoiceIntentStateResponse)
    async def voice_intent_review(intent_id: str, payload: VoiceIntentReviewRequest) -> VoiceIntentStateResponse:
        try:
            state = voice_intent_registry.review_intent(
                intent_id=intent_id,
                reviewed_by=payload.reviewed_by,
                review_reason=payload.review_reason,
                status=payload.status,
            )
            node_ui_page_cache.invalidate()
            return VoiceIntentStateResponse.model_validate(state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/voice/intents/dispatch", response_model=VoiceIntentDispatchResponse)
    async def voice_intent_dispatch(payload: VoiceIntentDispatchRequest) -> VoiceIntentDispatchResponse:
        match = assistant_service.match_intent(
            payload.text,
            endpoint_id=payload.endpoint_id,
            session_id=payload.session_id,
        )
        if match is None:
            return VoiceIntentDispatchResponse(matched=False)
        return VoiceIntentDispatchResponse(
            matched=True,
            intent_id=match.intent,
            command=match.command,
            slots=match.slots,
            reply_text=match.reply_text,
            provider_id=match.provider_id,
        )

    @app.post("/api/voice/intents/invoke", response_model=VoiceIntentInvokeResponse)
    async def voice_intent_invoke(payload: VoiceIntentDispatchRequest) -> VoiceIntentInvokeResponse:
        result = await asyncio.to_thread(
            assistant_service.invoke_intent,
            endpoint_id=payload.endpoint_id,
            text=payload.text,
            session_id=payload.session_id,
            reply_audio_factory=tts_audio_service.synthesize_intent_reply,
        )
        response = VoiceIntentInvokeResponse(
            matched=result.matched,
            endpoint_id=result.endpoint_id,
            session_id=result.session_id,
            heard_text=result.heard_text,
            intent_id=result.intent_id,
            command=result.command,
            slots=result.slots or {},
            reply_text=result.reply_text,
            provider_id=result.provider_id,
            recognized_event_id=result.recognized_event_id,
            recognition_event=result.recognition_event,
            dispatch_event=result.dispatch_event,
            reply_audio=result.reply_audio,
            conversation_followup=result.conversation_followup,
            latency_ms=result.latency_ms,
        )
        node_ui_page_cache.invalidate()
        return response

    @app.post("/api/endpoint/heartbeat", response_model=EndpointHeartbeatResponse)
    async def endpoint_heartbeat(payload: EndpointHeartbeatRequest) -> EndpointHeartbeatResponse:
        response = endpoint_service.record_heartbeat(payload)
        node_ui_page_cache.invalidate()
        return response

    @app.get("/api/endpoint/time", response_model=EndpointTimeResponse)
    async def endpoint_time() -> EndpointTimeResponse:
        return endpoint_service.current_time()

    @app.get("/api/endpoint/status", response_model=EndpointStatusResponse)
    async def latest_endpoint_status() -> EndpointStatusResponse:
        status = endpoint_service.latest_status()
        return status.model_copy(update={"firmware_update": firmware_update_payload(app_settings, status)})

    @app.get("/api/endpoint/status/{endpoint_id}", response_model=EndpointStatusResponse)
    async def endpoint_status(endpoint_id: str) -> EndpointStatusResponse:
        status = endpoint_service.status(endpoint_id)
        return status.model_copy(update={"firmware_update": firmware_update_payload(app_settings, status)})

    @app.get("/api/endpoints", response_model=EndpointRegistryListResponse)
    async def endpoint_registry() -> EndpointRegistryListResponse:
        statuses = endpoint_service.list_statuses()
        return EndpointRegistryListResponse(
            endpoints=[
                status.model_copy(update={"firmware_update": firmware_update_payload(app_settings, status)})
                for status in statuses.endpoints
            ]
        )

    @app.patch("/api/endpoints/{endpoint_id}", response_model=EndpointStatusResponse)
    async def endpoint_metadata_update(
        endpoint_id: str,
        payload: EndpointMetadataUpdateRequest,
    ) -> EndpointStatusResponse:
        status = endpoint_service.update_metadata(endpoint_id, payload)
        node_ui_page_cache.invalidate()
        return status.model_copy(update={"firmware_update": firmware_update_payload(app_settings, status)})

    @app.post("/api/endpoint/volume", response_model=EndpointVolumeCommandResponse)
    async def endpoint_volume(payload: EndpointVolumeCommandRequest) -> EndpointVolumeCommandResponse:
        result = await voice_session_manager.push_volume_command(
            endpoint_id=payload.endpoint_id,
            volume_percent=payload.volume_percent,
        )
        node_ui_page_cache.invalidate()
        return EndpointVolumeCommandResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            volume_percent=payload.volume_percent,
            request_id=result.get("request_id"),
            status=result.get("status"),
            reason=result.get("reason"),
        )

    @app.get("/api/endpoint/volume/{endpoint_id}", response_model=EndpointVolumeStatusResponse)
    async def endpoint_volume_status(endpoint_id: str) -> EndpointVolumeStatusResponse:
        result = voice_session_manager.volume_status(endpoint_id)
        return EndpointVolumeStatusResponse(
            endpoint_id=endpoint_id,
            volume_percent=result.get("volume_percent"),
            latest_command=result.get("latest_command"),
        )

    @app.post("/api/endpoint/mute", response_model=EndpointCommandResponse)
    async def endpoint_mute(payload: EndpointMuteCommandRequest) -> EndpointCommandResponse:
        result = await voice_session_manager.push_mute_command(endpoint_id=payload.endpoint_id, muted=payload.muted)
        return EndpointCommandResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            command_type="endpoint.mute",
            request_id=result.get("request_id"),
            status=result.get("status"),
            reason=result.get("reason"),
        )

    @app.post("/api/endpoint/micro-vad", response_model=EndpointCommandResponse)
    async def endpoint_micro_vad(payload: EndpointMicroVadCommandRequest) -> EndpointCommandResponse:
        result = await voice_session_manager.push_micro_vad_command(
            endpoint_id=payload.endpoint_id,
            pause_ms=payload.pause_ms,
        )
        return EndpointCommandResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            command_type="endpoint.micro_vad.set",
            request_id=result.get("request_id"),
            status=result.get("status"),
            reason=result.get("reason"),
        )

    @app.post("/api/endpoint/session/cancel", response_model=EndpointCommandResponse)
    async def endpoint_session_cancel(payload: EndpointCommandRequest) -> EndpointCommandResponse:
        result = await voice_session_manager.push_cancel_command(endpoint_id=payload.endpoint_id)
        return EndpointCommandResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            command_type="endpoint.cancel",
            request_id=result.get("request_id"),
            status=result.get("status"),
            reason=result.get("reason"),
        )

    @app.post("/api/endpoint/replay", response_model=EndpointCommandResponse)
    async def endpoint_replay(payload: EndpointCommandRequest) -> EndpointCommandResponse:
        result = await voice_session_manager.push_replay_command(endpoint_id=payload.endpoint_id)
        return EndpointCommandResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            command_type="endpoint.replay",
            request_id=result.get("request_id"),
            status=result.get("status"),
            reason=result.get("reason"),
        )

    @app.post("/api/endpoint/speak", response_model=EndpointCommandResponse)
    async def endpoint_speak(payload: EndpointSpeakCommandRequest) -> EndpointCommandResponse:
        result = await voice_session_manager.push_speak_command(
            endpoint_id=payload.endpoint_id,
            text=payload.text,
            session_id=payload.session_id,
        )
        return EndpointCommandResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            command_type="endpoint.speak",
            request_id=result.get("request_id"),
            status=result.get("status"),
            reason=result.get("reason"),
        )

    @app.post("/api/endpoint/storage/reformat", response_model=EndpointCommandResponse)
    async def endpoint_storage_reformat(payload: EndpointCommandRequest) -> EndpointCommandResponse:
        result = await voice_session_manager.push_storage_reformat_command(endpoint_id=payload.endpoint_id)
        return EndpointCommandResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            command_type="endpoint.storage.reformat",
            request_id=result.get("request_id"),
            status=result.get("status"),
            reason=result.get("reason"),
        )

    @app.post("/api/endpoint/led/simulate", response_model=EndpointCommandResponse)
    async def endpoint_led_simulate(payload: EndpointLedSimulateCommandRequest) -> EndpointCommandResponse:
        result = await voice_session_manager.push_led_simulation_command(
            endpoint_id=payload.endpoint_id,
            pattern=payload.pattern,
            duration_ms=payload.duration_ms,
        )
        return EndpointCommandResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            command_type="endpoint.led.simulate",
            request_id=result.get("request_id"),
            status=result.get("status"),
            reason=result.get("reason"),
        )

    def endpoint_media_public_url(asset_id: str) -> str:
        base_url = app_settings.public_api_base_url or f"http://127.0.0.1:{app_settings.api_port}"
        return f"{base_url.rstrip('/')}/api/endpoint/media/files/{asset_id}"

    def endpoint_media_response(asset: EndpointMediaAsset) -> EndpointMediaAssetResponse:
        return EndpointMediaAssetResponse(
            **asset.model_dump(mode="json"),
            download_url=endpoint_media_public_url(asset.asset_id),
        )

    def media_error(exc: EndpointMediaValidationError) -> HTTPException:
        return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})

    @app.get("/api/endpoint/media", response_model=EndpointMediaListResponse)
    async def endpoint_media_list() -> EndpointMediaListResponse:
        return EndpointMediaListResponse(
            assets=[endpoint_media_response(asset) for asset in endpoint_media_service.list_assets()]
        )

    def media_inventory_items(inventory: dict, key: str) -> list[EndpointMediaInventoryItem]:
        values = inventory.get(key, [])
        if not isinstance(values, list):
            return []
        items: list[EndpointMediaInventoryItem] = []
        for value in values:
            if isinstance(value, dict) and isinstance(value.get("filename"), str):
                items.append(EndpointMediaInventoryItem.model_validate(value))
        return items

    @app.get("/api/endpoint/media/inventory/{endpoint_id}", response_model=EndpointMediaInventoryResponse)
    async def endpoint_media_inventory(endpoint_id: str) -> EndpointMediaInventoryResponse:
        status = endpoint_service.status(endpoint_id)
        storage = status.capabilities.get("storage") if isinstance(status.capabilities, dict) else {}
        inventory = storage.get("media_inventory", {}) if isinstance(storage, dict) else {}
        if not isinstance(inventory, dict):
            inventory = {}
        return EndpointMediaInventoryResponse(
            endpoint_id=endpoint_id,
            pictures=media_inventory_items(inventory, "pictures"),
            sprites=media_inventory_items(inventory, "sprites"),
            sounds=media_inventory_items(inventory, "sounds"),
            truncated=bool(inventory.get("truncated", False)),
            last_seen_at=status.last_seen_at,
        )

    @app.post("/api/endpoint/media", response_model=EndpointMediaAssetResponse)
    async def endpoint_media_upload(payload: EndpointMediaUploadRequest) -> EndpointMediaAssetResponse:
        rewrite = payload.rewrite if payload.rewrite is not None else payload.overwrite
        log.info(
            "Endpoint media upload started: media_type=%s filename=%s asset_id=%s",
            payload.media_type,
            payload.filename,
            payload.asset_id or "<auto>",
        )
        try:
            asset = await asyncio.to_thread(
                endpoint_media_service.store_upload,
                media_type=payload.media_type,
                filename=payload.filename,
                content_base64=payload.content_base64,
                asset_id=payload.asset_id,
                content_type=payload.content_type,
                metadata=payload.metadata,
                overwrite=rewrite,
            )
        except EndpointMediaValidationError as exc:
            log.warning(
                "Endpoint media upload rejected: media_type=%s filename=%s code=%s",
                payload.media_type,
                payload.filename,
                exc.code,
            )
            raise media_error(exc) from exc
        log.info(
            "Endpoint media upload staged: asset_id=%s destination=%s filename=%s size_bytes=%s",
            asset.asset_id,
            asset.destination,
            asset.filename,
            asset.size_bytes,
        )
        return endpoint_media_response(asset)

    @app.get("/api/endpoint/media/files/{asset_id}")
    async def endpoint_media_file(asset_id: str) -> FileResponse:
        try:
            asset = endpoint_media_service.get_asset(asset_id)
            return FileResponse(endpoint_media_service.payload_path(asset), media_type=asset.content_type)
        except EndpointMediaValidationError as exc:
            raise media_error(exc) from exc

    @app.get("/api/endpoint/media/{asset_id}", response_model=EndpointMediaAssetResponse)
    async def endpoint_media_get(asset_id: str) -> EndpointMediaAssetResponse:
        try:
            return endpoint_media_response(endpoint_media_service.get_asset(asset_id))
        except EndpointMediaValidationError as exc:
            raise media_error(exc) from exc

    @app.delete("/api/endpoint/media/{asset_id}", response_model=EndpointMediaAssetResponse)
    async def endpoint_media_delete(asset_id: str) -> EndpointMediaAssetResponse:
        try:
            return endpoint_media_response(endpoint_media_service.delete_asset(asset_id))
        except EndpointMediaValidationError as exc:
            raise media_error(exc) from exc

    @app.post("/api/endpoint/media/{asset_id}/deliver", response_model=EndpointMediaDeliverResponse)
    async def endpoint_media_deliver(
        asset_id: str,
        payload: EndpointMediaDeliverRequest,
    ) -> EndpointMediaDeliverResponse:
        try:
            asset = endpoint_media_service.get_asset(asset_id)
        except EndpointMediaValidationError as exc:
            raise media_error(exc) from exc
        request_id = f"media_{asset.asset_id}_{hashlib.sha256(asset.sha256.encode()).hexdigest()[:12]}"
        rewrite = payload.rewrite if payload.rewrite is not None else payload.overwrite
        download_url = endpoint_media_public_url(asset.asset_id)
        log.info(
            "Endpoint media delivery requested: endpoint_id=%s asset_id=%s filename=%s size_bytes=%s url=%s",
            payload.endpoint_id,
            asset.asset_id,
            asset.filename,
            asset.size_bytes,
            download_url,
        )
        result = await voice_session_manager.push_media_transfer(
            endpoint_id=payload.endpoint_id,
            request_id=request_id,
            media_type=asset.media_type,
            asset_id=asset.asset_id,
            filename=asset.filename,
            destination=asset.destination,
            download_url=download_url,
            content_type=asset.content_type,
            size_bytes=asset.size_bytes,
            sha256=asset.sha256,
            overwrite=rewrite,
            activate=payload.activate,
            metadata=asset.metadata,
        )
        log.info(
            "Endpoint media delivery dispatch: endpoint_id=%s asset_id=%s accepted=%s status=%s reason=%s request_id=%s",
            payload.endpoint_id,
            asset.asset_id,
            bool(result.get("accepted")),
            result.get("status"),
            result.get("reason"),
            result.get("request_id"),
        )
        return EndpointMediaDeliverResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=payload.endpoint_id,
            asset=endpoint_media_response(asset),
            request_id=result.get("request_id"),
            status=result.get("status"),
            reason=result.get("reason"),
        )

    def firmware_artifact_path(filename: str) -> Path:
        return firmware_artifact_path_for_settings(app_settings, filename)

    def firmware_public_url(filename: str) -> str:
        return firmware_public_url_for_settings(app_settings, filename)

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
        status = voice_session_manager.status()
        status["timer_announcements"] = timer_announcement_service.status()
        status["voice_artifact_cleanup"] = app.state.voice_artifact_cleanup_status
        status["voice_orphan_cleanup"] = app.state.voice_orphan_cleanup_status
        status["voice_tts_warmup"] = app.state.voice_tts_warmup_status
        return status

    @app.get("/api/voice/sessions", response_model=VoiceSessionHistoryListResponse)
    async def voice_sessions(limit: int = 20, endpoint_id: str | None = None) -> VoiceSessionHistoryListResponse:
        return VoiceSessionHistoryListResponse(
            sessions=voice_session_manager.list_session_history(limit=limit, endpoint_id=endpoint_id)
        )

    @app.get("/api/voice/sessions/{session_id}", response_model=VoiceSessionHistoryDetailResponse)
    async def voice_session_detail(session_id: str) -> VoiceSessionHistoryDetailResponse:
        session = voice_session_manager.get_session_history(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="voice_session_not_found")
        return VoiceSessionHistoryDetailResponse(session=session)

    @app.post("/api/voice/sessions/{session_id}/replay", response_model=EndpointCommandResponse)
    async def voice_session_replay(session_id: str, payload: VoiceSessionReplayRequest) -> EndpointCommandResponse:
        result = await voice_session_manager.push_session_replay_command(
            session_id=session_id,
            endpoint_id=payload.endpoint_id,
        )
        return EndpointCommandResponse(
            accepted=bool(result.get("accepted")),
            endpoint_id=str(result.get("endpoint_id") or payload.endpoint_id or ""),
            command_type="endpoint.replay",
            request_id=result.get("request_id"),
            status=result.get("status"),
            reason=result.get("reason"),
        )

    @app.get("/api/voice/wake-recordings/{recording_id}")
    async def voice_wake_recording_audio(recording_id: str) -> FileResponse:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", recording_id or ""):
            raise HTTPException(status_code=404, detail="wake_recording_not_found")
        audio_path = voice_session_manager.wake_recording_path(recording_id)
        if audio_path is None:
            audio_path = app_settings.resolved_voice_wake_recording_dir() / f"{recording_id}.wav"
            audio_path = audio_path if audio_path.is_file() else None
        if audio_path is None:
            raise HTTPException(status_code=404, detail="wake_recording_not_found")
        return FileResponse(audio_path, media_type="audio/wav")

    @app.delete("/api/voice/wake-recordings/{recording_id}")
    async def voice_wake_recording_delete(recording_id: str) -> dict:
        result = voice_session_manager.delete_wake_recording(recording_id)
        if result.get("reason") == "invalid_recording_id":
            raise HTTPException(status_code=404, detail="wake_recording_not_found")
        return result

    @app.post("/api/tts/synthesize", response_model=TtsSynthesizeResponse)
    async def tts_synthesize(payload: TtsSynthesizeRequest) -> TtsSynthesizeResponse:
        return await asyncio.to_thread(tts_audio_service.synthesize, payload)

    @app.get("/api/tts/settings")
    async def tts_settings() -> dict:
        return await asyncio.to_thread(tts_runtime_settings_service.status)

    @app.put("/api/tts/settings")
    async def tts_settings_update(payload: dict) -> dict:
        updated = await asyncio.to_thread(tts_runtime_settings_service.update, payload)
        try:
            applied = await apply_piper_tts_provider_config(
                ProviderConfigRequest(
                    default_voice=updated.get("default_voice"),
                    warm_models=updated.get("warm_voices") if isinstance(updated.get("warm_voices"), list) else None,
                )
            )
            if applied:
                updated = await asyncio.to_thread(tts_runtime_settings_service.clear_restart_required)
        except httpx.HTTPError:
            pass
        return updated

    @app.get("/api/tts/artifacts")
    async def tts_artifacts(limit: int = 50) -> dict:
        return await asyncio.to_thread(tts_audio_service.list_artifacts, limit=limit)

    @app.get("/api/voice/tts/artifacts")
    async def voice_tts_artifacts(limit: int = 50) -> dict:
        return await asyncio.to_thread(tts_audio_service.list_artifacts, limit=limit)

    @app.delete("/api/voice/tts/artifacts/{stream_id}")
    async def voice_tts_artifact_delete(stream_id: str) -> dict:
        result = await asyncio.to_thread(tts_audio_service.delete_artifact, stream_id)
        if result.get("reason") == "invalid_stream_id":
            raise HTTPException(status_code=404, detail="tts_stream_not_found")
        return result

    @app.delete("/api/voice/artifacts/endpoints/{endpoint_id}")
    async def voice_endpoint_artifacts_delete(endpoint_id: str) -> dict:
        sessions = voice_session_manager.list_session_history(limit=200, endpoint_id=endpoint_id)
        deleted_tts: list[dict] = []
        deleted_wake: list[dict] = []
        for session in sessions:
            tts = session.get("tts") if isinstance(session.get("tts"), dict) else {}
            stream_id = tts.get("stream_id")
            if stream_id:
                deleted_tts.append(await asyncio.to_thread(tts_audio_service.delete_artifact, str(stream_id)))
            wake_recording = session.get("wake_recording") if isinstance(session.get("wake_recording"), dict) else {}
            recording_id = wake_recording.get("recording_id")
            if recording_id:
                deleted_wake.append(voice_session_manager.delete_wake_recording(str(recording_id)))
        return {
            "endpoint_id": endpoint_id,
            "session_count": len(sessions),
            "tts_deleted_count": sum(int(item.get("deleted_count") or 0) for item in deleted_tts),
            "wake_deleted_count": sum(int(item.get("deleted_count") or 0) for item in deleted_wake),
            "tts": deleted_tts,
            "wake_recordings": deleted_wake,
        }

    def tts_file_response(stream_id: str, *, variant: str | None, route: str, not_found_detail: str) -> FileResponse:
        fetch_started_at = time.perf_counter()
        audio_path = tts_audio_service.audio_path(stream_id, variant=variant)
        if audio_path is None:
            raise HTTPException(status_code=404, detail=not_found_detail)
        content_type = tts_audio_service.content_type(stream_id, audio_path)
        fetch_latency_ms = round((time.perf_counter() - fetch_started_at) * 1000, 2)
        tts_audio_service.record_fetch_latency(
            stream_id,
            variant=variant,
            latency_ms=fetch_latency_ms,
            audio_path=audio_path,
            route=route,
        )
        return FileResponse(
            audio_path,
            media_type=content_type,
            headers={"X-Hexe-TTS-Fetch-Latency-Ms": str(fetch_latency_ms)},
        )

    @app.get("/api/tts/audio/{stream_id}/{variant}")
    async def tts_audio_variant(stream_id: str, variant: str) -> FileResponse:
        return tts_file_response(
            stream_id,
            variant=variant,
            route="/api/tts/audio/{stream_id}/{variant}",
            not_found_detail="tts_audio_not_found",
        )

    @app.get("/api/tts/audio/{stream_id}")
    async def tts_audio(stream_id: str) -> FileResponse:
        return tts_file_response(
            stream_id,
            variant=None,
            route="/api/tts/audio/{stream_id}",
            not_found_detail="tts_audio_not_found",
        )

    @app.get("/api/tts/audio/{stream_id}/")
    async def tts_audio_base(stream_id: str) -> FileResponse:
        return tts_file_response(
            stream_id,
            variant=None,
            route="/api/tts/audio/{stream_id}/",
            not_found_detail="tts_audio_not_found",
        )

    @app.get("/api/voice/tts/{stream_id}/{variant}")
    async def voice_tts_audio_variant(stream_id: str, variant: str) -> FileResponse:
        return tts_file_response(
            stream_id,
            variant=variant,
            route="/api/voice/tts/{stream_id}/{variant}",
            not_found_detail="tts_stream_not_found",
        )

    @app.get("/api/voice/tts/{stream_id}")
    async def voice_tts_audio(stream_id: str) -> FileResponse:
        return tts_file_response(
            stream_id,
            variant=None,
            route="/api/voice/tts/{stream_id}",
            not_found_detail="tts_stream_not_found",
        )

    @app.get("/api/voice/tts/{stream_id}/")
    async def voice_tts_audio_base(stream_id: str) -> FileResponse:
        return tts_file_response(
            stream_id,
            variant=None,
            route="/api/voice/tts/{stream_id}/",
            not_found_detail="tts_stream_not_found",
        )

    @app.post("/api/voice/session/cancel")
    async def voice_session_cancel() -> dict:
        result = voice_session_manager.cancel_from_operator()
        node_ui_page_cache.invalidate()
        return result

    def node_ui_operational_status() -> dict:
        try:
            return node_ui.as_json(governance_service.operational_status())
        except HTTPException as exc:
            return {"operational_ready": False, "status": "unavailable", "detail": exc.detail}

    def node_ui_endpoint_statuses() -> list[dict]:
        statuses = endpoint_service.list_statuses()
        return [
            node_ui.as_json(status.model_copy(update={"firmware_update": firmware_update_payload(app_settings, status)}))
            for status in statuses.endpoints
        ]

    async def node_ui_tts_settings() -> dict:
        return await asyncio.to_thread(tts_runtime_settings_service.status)

    @app.get("/api/node/ui-manifest")
    async def node_ui_manifest() -> dict:
        return node_ui.manifest(app_settings, node_ui.as_json(service.status_payload()))

    def node_ui_overview_node_payload() -> dict:
        return node_ui.overview_node(
            app_settings,
            node_ui.as_json(service.status_payload()),
            node_ui.as_json(service.onboarding_payload()),
        )

    async def node_ui_services_status() -> dict:
        return node_ui.as_json(await asyncio.to_thread(service.service_status_payload))

    async def node_ui_overview_health_payload() -> dict:
        return node_ui.overview_health(
            node_ui.as_json(service.status_payload()),
            node_ui.as_json(service.readiness_payload()),
            node_ui.as_json(provider_setup_service.status_payload()),
            await node_ui_services_status(),
            voice_session_manager.status(),
        )

    async def node_ui_overview_warnings_payload() -> dict:
        return node_ui.overview_warnings(
            node_ui.as_json(service.status_payload()),
            node_ui.as_json(service.onboarding_payload()),
            node_ui.as_json(service.readiness_payload()),
            await node_ui_services_status(),
            voice_session_manager.status(),
        )

    def node_ui_overview_facts_payload() -> dict:
        return node_ui.overview_facts(
            node_ui.as_json(service.status_payload()),
            node_ui.as_json(service.onboarding_payload()),
            node_ui_operational_status(),
            node_ui.as_json(provider_setup_service.status_payload()),
            voice_session_manager.status(),
        )

    async def node_ui_runtime_services_payload() -> dict:
        return node_ui.runtime_services(await node_ui_services_status(), voice_session_manager.status())

    async def node_ui_providers_status_payload() -> dict:
        tts_settings = await node_ui_tts_settings()
        return node_ui.provider_status(
            await node_ui_services_status(),
            voice_session_manager.status(),
            tts_settings,
            node_ui.as_json(provider_setup_service.status_payload()),
            node_ui_provider_config_context(tts_settings),
        )

    def node_ui_voice_endpoints_payload() -> dict:
        return node_ui.endpoint_records(node_ui_endpoint_statuses(), voice_session_manager.status())

    def node_ui_voice_endpoint_actions_payload() -> dict:
        return node_ui.endpoint_actions(voice_session_manager.status())

    def node_ui_voice_sessions_payload(limit: int = 20) -> dict:
        return node_ui.session_records(voice_session_manager.list_session_history(limit=limit))

    def node_ui_voice_intents_payload() -> dict:
        return node_ui.intent_records(voice_intent_registry.snapshot())

    def node_ui_voice_intent_actions_payload() -> dict:
        return node_ui.intent_actions(voice_intent_registry.snapshot())

    async def node_ui_voice_tts_payload() -> dict:
        return node_ui.tts_runtime(await node_ui_tts_settings(), voice_session_manager.status())

    def node_ui_provider_config_context(tts_settings: dict) -> dict:
        stt_profile = resolve_stt_model_profile(app_settings)
        return {
            "stt": {
                "kind": "stt",
                "profile": stt_profile.name,
                "fallback_profile": stt_profile.fallback_profile,
                "model": stt_profile.model
                if app_settings.voice_stt_provider in {"faster_whisper", "external_faster_whisper"}
                else app_settings.voice_stt_model,
                "device": stt_profile.device,
                "compute_type": stt_profile.compute_type,
                "warm_model": stt_profile.preload,
                "warm_models": [],
                "profile_options": stt_profile_options(),
                "fallback_profile_options": stt_profile_options(),
                "model_options": node_ui_model_options(
                    [
                        "tiny.en",
                        "base.en",
                        "small.en",
                        "medium.en",
                        "large-v3",
                        app_settings.voice_stt_model,
                        app_settings.voice_stt_faster_whisper_model,
                    ]
                ),
                "device_options": node_ui_model_options(["cpu", "cuda"]),
                "compute_type_options": node_ui_model_options(["int8", "float16", "int8_float16", "float32"]),
            },
            "tts": {
                "kind": "tts",
                "default_voice": tts_settings.get("default_voice") or app_settings.voice_tts_piper_voice,
                "warm_models": tts_settings.get("warm_voices") if isinstance(tts_settings.get("warm_voices"), list) else [],
                "model_options": [
                    {"value": model.get("model_id"), "label": model.get("display_name") or node_ui.labelize(model.get("model_id"))}
                    for model in tts_settings.get("models", [])
                    if isinstance(model, dict) and model.get("model_id")
                ],
            },
            "wake": {
                "kind": "wake",
                "default_wakeword": node_ui_wake_models()[0] if node_ui_wake_models() else "",
                "warm_model": app_settings.voice_wake_preload,
                "wakeword_options": node_ui_model_options(node_ui_wake_models()),
            },
        }

    def node_ui_model_options(values: list[str | None]) -> list[dict]:
        options = []
        seen = set()
        for value in values:
            model_id = str(value or "").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            options.append({"value": model_id, "label": node_ui.labelize(model_id)})
        return options

    def node_ui_wake_models() -> list[str]:
        configured = [item.strip() for item in (app_settings.voice_wake_models or "").split(",") if item.strip()]
        model_dir = app_settings.runtime_dir / "openwakeword" / "models"
        discovered = [path.stem for path in sorted(model_dir.glob("*.onnx")) + sorted(model_dir.glob("*.tflite"))] if model_dir.exists() else []
        models = [*configured, *discovered, "Hexe"]
        deduped: list[str] = []
        seen = set()
        for model in models:
            if model in seen:
                continue
            seen.add(model)
            deduped.append(model)
        return deduped

    async def apply_external_stt_provider_config(payload: ProviderConfigRequest) -> bool:
        provider_config = {
            "profile": payload.profile,
            "fallback_profile": payload.fallback_profile,
            "model": payload.model,
            "device": payload.device,
            "compute_type": payload.compute_type,
            "warm_model": payload.warm_model,
        }
        stt_profile = resolve_stt_model_profile(app_settings, provider_config)
        default_model = str(payload.model or "").strip()
        warm_models = [str(model or "").strip() for model in payload.warm_models if str(model or "").strip()]
        if not default_model:
            default_model = stt_profile.model
        device = stt_profile.device
        compute_type = stt_profile.compute_type
        if not default_model and not warm_models:
            return False
        async with async_client_for_engine(
            timeout=app_settings.voice_stt_timeout_s,
            socket_path=app_settings.resolved_voice_stt_service_socket_path(),
        ) as client:
            for warm_model in [model for model in warm_models if model != default_model]:
                await client.put(
                    f"{app_settings.resolved_voice_stt_service_base_url()}/config",
                    json={
                        "model": warm_model,
                        "device": device,
                        "compute_type": compute_type,
                        "warm_model": True,
                    },
                )
            if not default_model:
                return True
            await client.put(
                f"{app_settings.resolved_voice_stt_service_base_url()}/config",
                json={
                    "model": default_model,
                    "device": device,
                    "compute_type": compute_type,
                    "warm_model": bool(payload.warm_model) or default_model in warm_models,
                },
            )
        return True

    async def apply_piper_tts_provider_config(payload: ProviderConfigRequest) -> bool:
        if app_settings.voice_tts_provider != "piper":
            return False
        default_voice = str(payload.default_voice or "").strip()
        warm_voices = payload.warm_models if isinstance(payload.warm_models, list) else None
        if not default_voice and warm_voices is None:
            return False
        request_payload = {
            "default_voice": default_voice or None,
            "warm_voices": [str(voice or "").strip() for voice in (warm_voices or []) if str(voice or "").strip()],
        }
        async with async_client_for_engine(
            timeout=app_settings.voice_tts_timeout_s,
            socket_path=app_settings.resolved_voice_tts_piper_socket_path(),
        ) as client:
            await client.put(
                f"{app_settings.resolved_voice_tts_piper_base_url()}/config",
                json=request_payload,
            )
        return True

    async def reconcile_external_stt_provider_config() -> None:
        await asyncio.sleep(1)
        provider_setup = provider_setup_service.status_payload()
        provider_config = provider_setup.provider_configs.get("external_faster_whisper", {})
        model = str(provider_config.get("model") or "").strip()
        warm_models = provider_config.get("warm_models") if isinstance(provider_config.get("warm_models"), list) else []
        profile = str(provider_config.get("profile") or "").strip()
        fallback_profile = str(provider_config.get("fallback_profile") or "").strip()
        if not model and not warm_models and not profile:
            return
        payload = ProviderConfigRequest(
            profile=profile or None,
            fallback_profile=fallback_profile or None,
            model=model or None,
            device=str(provider_config.get("device") or "").strip() or None,
            compute_type=str(provider_config.get("compute_type") or "").strip() or None,
            warm_model=bool(provider_config.get("warm_model")),
            warm_models=[str(item or "").strip() for item in warm_models if str(item or "").strip()],
        )
        for _attempt in range(30):
            try:
                await apply_external_stt_provider_config(payload)
                return
            except httpx.HTTPError:
                await asyncio.sleep(2)

    def schedule_external_stt_provider_reconcile(result: ServiceActionResponse, action: str) -> None:
        if not result.accepted:
            return
        if action not in {"install", "start", "restart"}:
            return
        if result.target not in {app_settings.voice_stt_service_id, "stt", "stt_engine"}:
            return
        asyncio.create_task(reconcile_external_stt_provider_config())

    async def node_ui_voice_tts_artifacts_payload(limit: int = 50) -> dict:
        artifacts = await asyncio.to_thread(tts_audio_service.list_artifacts, limit=limit)
        return node_ui.artifact_records(artifacts)

    def node_ui_voice_media_payload() -> dict:
        assets = {
            "assets": [
                node_ui.as_json(endpoint_media_response(asset))
                for asset in endpoint_media_service.list_assets()
            ]
        }
        return node_ui.media_records(assets, node_ui_endpoint_statuses())

    async def node_ui_health_page_card() -> dict:
        return node_ui.page_card(
            "node.health",
            "Node Health",
            await node_ui_overview_health_payload(),
            refresh=node_ui.NEAR_LIVE_15S,
        )

    async def build_node_ui_page_overview() -> dict:
        return node_ui.page_snapshot(
            "overview",
            node_ui.NEAR_LIVE_15S,
            [
                await node_ui_health_page_card(),
                node_ui.page_card(
                    "node.warnings",
                    "Operational Warnings",
                    await node_ui_overview_warnings_payload(),
                    refresh=node_ui.MANUAL_REFRESH,
                ),
            ],
        )

    async def build_node_ui_page_runtime() -> dict:
        runtime_services_payload = await node_ui_runtime_services_payload()
        providers_status_payload = await node_ui_providers_status_payload()
        return node_ui.page_snapshot(
            "runtime",
            node_ui.NEAR_LIVE_15S,
            [
                await node_ui_health_page_card(),
                node_ui.page_card(
                    "runtime.services",
                    "Runtime Services",
                    runtime_services_payload,
                    actions=node_ui.runtime_service_action_definitions(runtime_services_payload),
                    refresh=node_ui.NEAR_LIVE_15S,
                ),
                node_ui.page_card(
                    "runtime.providers",
                    "Provider Status",
                    providers_status_payload,
                    actions=node_ui.provider_setup_action_definitions(providers_status_payload),
                    refresh=node_ui.NEAR_LIVE_30S,
                ),
            ],
        )

    async def build_node_ui_page_voice_endpoints() -> dict:
        return node_ui.page_snapshot(
            "voice.endpoints",
            node_ui.NEAR_LIVE_10S,
            [
                await node_ui_health_page_card(),
                node_ui.page_card(
                    "voice.endpoints",
                    "Voice Endpoints",
                    node_ui_voice_endpoints_payload(),
                    detail_endpoint_template="/api/endpoint/status/{endpoint_id}",
                    refresh=node_ui.NEAR_LIVE_10S,
                ),
                node_ui.page_card(
                    "voice.endpoint_actions",
                    "Endpoint Actions",
                    node_ui_voice_endpoint_actions_payload(),
                    actions=[node_ui.cancel_active_session_action(), node_ui.test_assistant_turn_action()],
                    refresh=node_ui.NEAR_LIVE_10S,
                ),
                node_ui.page_card(
                    "voice.sessions",
                    "Recent Sessions",
                    node_ui_voice_sessions_payload(),
                    detail_endpoint_template="/api/voice/sessions/{session_id}",
                    refresh=node_ui.MANUAL_REFRESH,
                ),
            ],
        )

    async def build_node_ui_page_voice_intents() -> dict:
        return node_ui.page_snapshot(
            "voice.intents",
            node_ui.MANUAL_REFRESH,
            [
                await node_ui_health_page_card(),
                node_ui.page_card(
                    "voice.intent_registry",
                    "Registered Intents",
                    node_ui_voice_intents_payload(),
                    detail_endpoint_template="/api/voice/intents/{intent_id}",
                    refresh=node_ui.MANUAL_REFRESH,
                ),
                node_ui.page_card(
                    "voice.intent_actions",
                    "Intent Actions",
                    node_ui_voice_intent_actions_payload(),
                    actions=[node_ui.test_intent_action(), node_ui.invoke_intent_action()],
                    refresh=node_ui.MANUAL_REFRESH,
                ),
            ],
        )

    async def build_node_ui_page_voice_tts() -> dict:
        return node_ui.page_snapshot(
            "voice.tts",
            node_ui.NEAR_LIVE_30S,
            [
                await node_ui_health_page_card(),
                node_ui.page_card(
                    "voice.tts_runtime",
                    "TTS Runtime",
                    await node_ui_voice_tts_payload(),
                    refresh=node_ui.NEAR_LIVE_30S,
                ),
                node_ui.page_card(
                    "voice.tts_artifacts",
                    "Generated TTS Artifacts",
                    await node_ui_voice_tts_artifacts_payload(),
                    refresh=node_ui.MANUAL_REFRESH,
                ),
                node_ui.page_card(
                    "voice.media",
                    "Endpoint Media",
                    node_ui_voice_media_payload(),
                    refresh=node_ui.MANUAL_REFRESH,
                ),
            ],
        )

    node_ui_page_specs = [
        ("overview", node_ui.NEAR_LIVE_15S, build_node_ui_page_overview, 15.0),
        ("runtime", node_ui.NEAR_LIVE_15S, build_node_ui_page_runtime, 15.0),
        ("voice.endpoints", node_ui.NEAR_LIVE_10S, build_node_ui_page_voice_endpoints, 10.0),
        ("voice.intents", node_ui.MANUAL_REFRESH, build_node_ui_page_voice_intents, 60.0),
        ("voice.tts", node_ui.NEAR_LIVE_30S, build_node_ui_page_voice_tts, 30.0),
    ]
    for key, refresh, builder, interval_seconds in node_ui_page_specs:
        node_ui_page_cache.register_page(key, refresh, builder, interval_seconds=interval_seconds)

    @app.get("/api/node/ui/pages/overview")
    async def node_ui_page_overview() -> dict:
        return await node_ui_page_cache.get_or_build("overview", node_ui.NEAR_LIVE_15S, build_node_ui_page_overview)

    @app.get("/api/node/ui/pages/runtime")
    async def node_ui_page_runtime() -> dict:
        return await node_ui_page_cache.get_or_build("runtime", node_ui.NEAR_LIVE_15S, build_node_ui_page_runtime)

    @app.get("/api/node/ui/pages/voice/endpoints")
    async def node_ui_page_voice_endpoints() -> dict:
        return await node_ui_page_cache.get_or_build(
            "voice.endpoints",
            node_ui.NEAR_LIVE_10S,
            build_node_ui_page_voice_endpoints,
        )

    @app.get("/api/node/ui/pages/voice/intents")
    async def node_ui_page_voice_intents() -> dict:
        return await node_ui_page_cache.get_or_build("voice.intents", node_ui.MANUAL_REFRESH, build_node_ui_page_voice_intents)

    @app.get("/api/node/ui/pages/voice/tts")
    async def node_ui_page_voice_tts() -> dict:
        return await node_ui_page_cache.get_or_build("voice.tts", node_ui.NEAR_LIVE_30S, build_node_ui_page_voice_tts)

    @app.get("/api/node/ui/overview/node")
    async def node_ui_overview_node() -> dict:
        return node_ui_overview_node_payload()

    @app.get("/api/node/ui/overview/health")
    async def node_ui_overview_health() -> dict:
        return await node_ui_overview_health_payload()

    @app.get("/api/node/ui/overview/warnings")
    async def node_ui_overview_warnings() -> dict:
        return await node_ui_overview_warnings_payload()

    @app.get("/api/node/ui/overview/facts")
    async def node_ui_overview_facts() -> dict:
        return node_ui_overview_facts_payload()

    @app.get("/api/node/ui/runtime/services")
    async def node_ui_runtime_services() -> dict:
        return await node_ui_runtime_services_payload()

    @app.get("/api/node/ui/providers/status")
    async def node_ui_providers_status() -> dict:
        return await node_ui_providers_status_payload()

    @app.get("/api/node/ui/voice/endpoints")
    async def node_ui_voice_endpoints() -> dict:
        return node_ui_voice_endpoints_payload()

    @app.get("/api/node/ui/voice/endpoint-actions")
    async def node_ui_voice_endpoint_actions() -> dict:
        return node_ui_voice_endpoint_actions_payload()

    @app.get("/api/node/ui/voice/sessions")
    async def node_ui_voice_sessions(limit: int = 20) -> dict:
        return node_ui_voice_sessions_payload(limit=limit)

    @app.get("/api/node/ui/voice/intents")
    async def node_ui_voice_intents() -> dict:
        return node_ui_voice_intents_payload()

    @app.get("/api/node/ui/voice/intent-actions")
    async def node_ui_voice_intent_actions() -> dict:
        return node_ui_voice_intent_actions_payload()

    @app.get("/api/node/ui/voice/tts")
    async def node_ui_voice_tts() -> dict:
        return await node_ui_voice_tts_payload()

    @app.get("/api/node/ui/voice/tts-artifacts")
    async def node_ui_voice_tts_artifacts(limit: int = 50) -> dict:
        return await node_ui_voice_tts_artifacts_payload(limit=limit)

    @app.get("/api/node/ui/voice/media")
    async def node_ui_voice_media() -> dict:
        return node_ui_voice_media_payload()

    @app.post("/api/node/ui/actions/refresh-status")
    async def node_ui_refresh_status_action() -> dict:
        node_ui_page_cache.invalidate()
        return {
            "accepted": True,
            "status": "refreshed",
            "updated_at": node_ui.utc_now(),
            "node": node_ui.as_json(service.status_payload()),
        }

    async def run_node_ui_runtime_service_action(target: str, action: str) -> ServiceActionResponse:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"start", "stop", "restart"}:
            raise HTTPException(status_code=404, detail="unsupported_runtime_service_action")
        result = await asyncio.to_thread(service.service_action, target=target, action=normalized_action)
        if result.accepted and normalized_action == "restart" and result.target in {app_settings.piper_tts_service_id, "tts"}:
            await asyncio.to_thread(tts_runtime_settings_service.clear_restart_required)
        schedule_external_stt_provider_reconcile(result, normalized_action)
        node_ui_page_cache.invalidate("runtime")
        return result

    @app.post("/api/node/ui/runtime/services/{target}/{action}", response_model=ServiceActionResponse)
    async def node_ui_runtime_service_action(target: str, action: str) -> ServiceActionResponse:
        return await run_node_ui_runtime_service_action(target, action)

    @app.post("/api/node/ui/actions/test-assistant-turn", response_model=AssistantTurnResponse)
    async def node_ui_test_assistant_turn_action() -> AssistantTurnResponse:
        response = assistant_service.handle_turn(
            AssistantTurnRequest(endpoint_id="core-rendered-ui-test", text="hello"),
        )
        node_ui_page_cache.invalidate()
        return response

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

    @app.post("/api/node/migration/export")
    async def export_node_migration_bundle(payload: NodeMigrationExportRequest) -> dict:
        try:
            return node_migration_service.export_bundle(payload)
        except NodeMigrationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/node/migration/import", response_model=NodeMigrationImportResponse)
    async def import_node_migration_bundle(payload: NodeMigrationImportRequest) -> NodeMigrationImportResponse:
        try:
            return node_migration_service.import_bundle(payload)
        except NodeMigrationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/node/migration/preflight", response_model=NodeMigrationPreflightResponse)
    async def preflight_node_migration_bundle(payload: NodeMigrationPreflightRequest) -> dict:
        return node_migration_service.preflight_bundle(payload)

    @app.post("/api/node/migration/backup", response_model=NodeMigrationBackupResponse)
    async def backup_node_migration_state(payload: NodeMigrationBackupRequest) -> dict:
        try:
            return node_migration_service.create_backup(payload)
        except NodeMigrationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/node/migration/restore", response_model=NodeMigrationImportResponse)
    async def restore_node_migration_backup(payload: NodeMigrationRestoreRequest) -> NodeMigrationImportResponse:
        try:
            return node_migration_service.restore_backup(payload)
        except NodeMigrationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/setup/bootstrap/status", response_model=SetupBootstrapStatusResponse)
    async def setup_bootstrap_status() -> SetupBootstrapStatusResponse:
        return setup_bootstrap_status_service.status_payload()

    @app.get("/api/setup/host-readiness", response_model=SetupHostReadinessResponse)
    async def setup_host_readiness() -> SetupHostReadinessResponse:
        return setup_host_readiness_service.readiness_payload()

    @app.post(
        "/api/setup/host-readiness/actions/{action}",
        response_model=SetupHostReadinessActionResponse,
    )
    async def setup_host_readiness_action(
        action: str,
        payload: SetupHostReadinessActionRequest,
    ) -> SetupHostReadinessActionResponse:
        return setup_host_readiness_service.run_action(action, payload)

    @app.put("/api/onboarding/local-setup/node-identity", response_model=NodeIdentitySetupResponse)
    async def save_node_identity(payload: NodeIdentitySetupRequest) -> NodeIdentitySetupResponse:
        return onboarding_state_service.save_node_identity(payload)

    @app.put("/api/onboarding/local-setup/core-connection", response_model=CoreConnectionSetupResponse)
    async def save_core_connection(payload: CoreConnectionSetupRequest) -> CoreConnectionSetupResponse:
        return onboarding_state_service.save_core_connection(payload)

    @app.put("/api/setup/core", response_model=SetupCoreConnectionResponse)
    async def setup_core_connection(payload: CoreConnectionSetupRequest) -> SetupCoreConnectionResponse:
        normalized_core_base_url = setup_host_readiness_service._normalize_core_base_url(payload.core_base_url) or str(payload.core_base_url).rstrip("/")
        normalized_core_public_url = setup_host_readiness_service._normalize_core_public_url(payload.core_base_url) or normalized_core_base_url
        normalized_payload = payload.model_copy(update={"core_base_url": normalized_core_base_url})
        saved = onboarding_state_service.save_core_connection(normalized_payload)
        warnings: list[str] = []
        metadata: dict[str, object] = {}
        tested_endpoints: list[dict[str, object]] = []
        reachable = False
        core_identity: dict[str, object] = {}
        core_version: str | None = None
        registration_supported = False
        reauth_supported = False
        supervisor_enrollment_supported = False
        capability_governance_supported = False
        core_base = normalized_core_base_url.rstrip("/")

        async def probe(client: httpx.AsyncClient, path: str, *, method: str = "HEAD") -> dict[str, object]:
            url = f"{core_base}{path}"
            try:
                response = await client.request(method, url)
            except Exception as exc:
                result = {"path": path, "url": url, "method": method, "supported": False, "error": str(exc)}
                tested_endpoints.append(result)
                return result
            supported = response.status_code not in {404, 501}
            result = {
                "path": path,
                "url": url,
                "method": method,
                "status_code": response.status_code,
                "supported": supported,
            }
            tested_endpoints.append(result)
            return result

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                health_url = f"{core_base}/api/health"
                response = await client.get(health_url)
                reachable = response.status_code < 500
                metadata["health_status_code"] = response.status_code
                tested_endpoints.append({"path": "/api/health", "url": health_url, "method": "GET", "status_code": response.status_code})
                if response.headers.get("content-type", "").lower().startswith("application/json"):
                    try:
                        health_payload = response.json()
                    except ValueError:
                        health_payload = {}
                    if isinstance(health_payload, dict):
                        metadata["health"] = health_payload
                        core_version = str(
                            health_payload.get("version")
                            or health_payload.get("core_version")
                            or health_payload.get("app_version")
                            or ""
                        ).strip() or None

                platform_url = f"{core_base}/api/system/platform"
                platform_response = await client.get(platform_url)
                metadata["platform_status_code"] = platform_response.status_code
                tested_endpoints.append(
                    {"path": "/api/system/platform", "url": platform_url, "method": "GET", "status_code": platform_response.status_code}
                )
                if platform_response.status_code < 500 and platform_response.headers.get("content-type", "").lower().startswith("application/json"):
                    try:
                        platform_payload = platform_response.json()
                    except ValueError:
                        platform_payload = {}
                    if isinstance(platform_payload, dict):
                        core_identity = {
                            key: value
                            for key, value in platform_payload.items()
                            if key in {"core_id", "platform_name", "platform_short", "platform_domain", "core_name", "supervisor_name"}
                        }

                probes = {
                    "registration": await probe(client, "/api/system/nodes/onboarding/sessions"),
                    "reauth": await probe(client, "/api/system/nodes/reauth/sessions"),
                    "supervisor_enrollment": await probe(client, "/api/system/supervisors/enrollment-tokens"),
                    "capability_profiles": await probe(client, "/api/system/nodes/capabilities/profiles", method="GET"),
                    "governance": await probe(client, "/api/system/nodes/governance/current", method="GET"),
                }
                metadata["probes"] = probes
                registration_supported = bool(probes["registration"].get("supported"))
                reauth_supported = bool(probes["reauth"].get("supported"))
                supervisor_enrollment_supported = bool(probes["supervisor_enrollment"].get("supported"))
                capability_governance_supported = bool(
                    probes["capability_profiles"].get("supported") and probes["governance"].get("supported")
                )
        except Exception as exc:
            warnings.append(f"core_unreachable:{exc}")
        return SetupCoreConnectionResponse(
            configured=saved.configured,
            core_base_url=saved.core_base_url,
            core_public_url=normalized_core_public_url,
            core_api_url=normalized_core_base_url,
            core_ui_url=normalized_core_public_url,
            reachable=reachable,
            validation_state="validated" if reachable else "deferred",
            recheck_required_before_trust=not reachable,
            core_identity=core_identity,
            core_version=core_version,
            registration_supported=registration_supported,
            reauth_supported=reauth_supported,
            supervisor_enrollment_supported=supervisor_enrollment_supported,
            capability_governance_supported=capability_governance_supported,
            tested_endpoints=tested_endpoints,
            metadata=metadata,
            warnings=warnings,
        )

    @app.post("/api/setup/migration/preflight", response_model=NodeMigrationPreflightResponse)
    async def setup_migration_preflight(payload: NodeMigrationPreflightRequest) -> dict:
        return node_migration_service.preflight_bundle(payload)

    @app.post("/api/setup/migration/import", response_model=NodeMigrationImportResponse)
    async def setup_migration_import(payload: NodeMigrationImportRequest) -> NodeMigrationImportResponse:
        try:
            return node_migration_service.import_bundle(payload)
        except NodeMigrationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/setup/trust/reauth/start", response_model=SetupReauthStartResponse)
    async def setup_reauth_start() -> SetupReauthStartResponse:
        return setup_reauth_service.start()

    @app.post("/api/setup/trust/reauth/finalize", response_model=SetupReauthFinalizeResponse)
    async def setup_reauth_finalize() -> SetupReauthFinalizeResponse:
        return setup_reauth_service.finalize()

    @app.post("/api/setup/trust/actions/{action}", response_model=SetupTrustRecoveryActionResponse)
    async def setup_trust_recovery_action(action: str) -> SetupTrustRecoveryActionResponse:
        return setup_trust_recovery_service.run_action(action)

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

    @app.post("/api/onboarding/registration-metadata/refresh")
    async def onboarding_registration_metadata_refresh() -> dict:
        return registration_metadata_refresh_service.refresh()

    @app.get("/api/providers/setup", response_model=ProviderSetupResponse)
    async def provider_setup_status() -> ProviderSetupResponse:
        return provider_setup_service.status_payload()

    def setup_provider_status_payload() -> dict:
        state = onboarding_state_store.load()
        provider_setup = provider_setup_service.status_payload()
        services = service.service_status_payload()
        component_by_target = {
            str(component.get("restart_target") or component.get("service_id") or ""): component
            for component in services.components
            if isinstance(component, dict)
        }
        provider_targets = {
            "external_faster_whisper": app_settings.voice_stt_service_id,
            "faster_whisper": app_settings.voice_stt_service_id,
            "piper": app_settings.piper_tts_service_id,
            "openwakeword": app_settings.openwakeword_service_id,
            "supervised_openwakeword": app_settings.openwakeword_service_id,
        }
        states = []
        blockers = list(provider_setup.blocking_reasons)
        for provider_id in provider_setup.enabled_providers:
            target = provider_targets.get(provider_id)
            component = component_by_target.get(target or "")
            healthy = bool(component.get("healthy")) if component else provider_id in {"deterministic", "openai", "voice"}
            provider_state_label = "healthy" if healthy else "warning" if provider_id in {"deterministic", "openai", "voice"} else "failed"
            if not healthy and provider_id not in {"deterministic", "openai", "voice"}:
                blockers.append(f"{provider_id}_not_healthy")
            states.append(
                {
                    "provider_id": provider_id,
                    "target": target,
                    "state": provider_state_label,
                    "healthy": healthy,
                    "component": component or {},
                }
            )
        state_by_provider = {provider["provider_id"]: provider for provider in states}
        provider_configs = provider_setup.provider_configs or {}
        selected_assets: list[str] = []
        asset_progress: list[dict[str, object]] = []

        def asset_state(provider_id: str, exists: bool) -> str:
            provider_state = state_by_provider.get(provider_id, {})
            component = provider_state.get("component") if isinstance(provider_state, dict) else {}
            if not isinstance(component, dict):
                component = {}
            if provider_state.get("state") == "failed" and not exists:
                return "failed"
            if provider_id in {"external_faster_whisper", "faster_whisper"} and exists and component.get("loaded") is False:
                return "preloading"
            if exists and provider_state.get("healthy"):
                return "healthy"
            return "downloaded" if exists else "missing"

        def stt_model_exists(model: str) -> bool:
            cache_dir = app_settings.runtime_dir / "stt" / "faster-whisper"
            if not cache_dir.exists():
                return False
            normalized = model.casefold()
            return any(normalized in path.name.casefold() for path in cache_dir.rglob("*"))

        def piper_voice_exists(voice: str) -> bool:
            model_dir = app_settings.resolved_piper_tts_model_dir()
            return (model_dir / f"{voice}.onnx").exists() and (model_dir / f"{voice}.onnx.json").exists()

        def wake_model_exists(model: str) -> bool:
            model_dir = app_settings.runtime_dir / "openwakeword" / "models"
            candidates = [model, model.casefold(), model.lower()]
            return any(
                (model_dir / f"{candidate}.tflite").exists() or (model_dir / f"{candidate}.onnx").exists()
                for candidate in candidates
            )

        def append_asset(provider_id: str, asset_type: str, asset_id: object, exists: bool) -> None:
            if not asset_id:
                return
            asset_name = str(asset_id)
            if asset_name not in selected_assets:
                selected_assets.append(asset_name)
            asset_progress.append(
                {
                    "provider_id": provider_id,
                    "asset_type": asset_type,
                    "asset_id": asset_name,
                    "state": asset_state(provider_id, exists),
                    "retry_action": "download-models",
                }
            )

        for provider_id in provider_setup.enabled_providers:
            config = provider_configs.get(provider_id, {})
            if provider_id in {"external_faster_whisper", "faster_whisper"}:
                for model in [config.get("model"), *(config.get("warm_models") or [])]:
                    append_asset(provider_id, "stt_model", model, stt_model_exists(str(model)) if model else False)
            elif provider_id == "piper":
                for voice in [config.get("default_voice") or config.get("model"), *(config.get("warm_models") or [])]:
                    append_asset(provider_id, "piper_voice", voice, piper_voice_exists(str(voice)) if voice else False)
            elif provider_id in {"openwakeword", "supervised_openwakeword"}:
                for wake_model in [config.get("default_wakeword") or config.get("model"), *(config.get("warm_models") or [])]:
                    append_asset(provider_id, "wake_model", wake_model, wake_model_exists(str(wake_model)) if wake_model else False)

        service_targets = [provider["target"] for provider in states if provider.get("target")]
        unhealthy = [provider["provider_id"] for provider in states if not provider.get("healthy")]
        stt_config = (
            provider_configs.get("external_faster_whisper")
            or provider_configs.get("faster_whisper")
            or {}
        )
        cuda_mode = str(stt_config.get("cuda_mode") or "auto").strip().lower()
        if cuda_mode not in {"auto", "cpu", "cuda", "skip"}:
            cuda_mode = "auto"
        docker_gpu_hint = bool(shutil.which("nvidia-smi"))
        recommended_cuda_mode = "cuda" if docker_gpu_hint else "cpu"
        selected_cuda_profile = (
            "cuda"
            if cuda_mode == "cuda" or (cuda_mode == "auto" and docker_gpu_hint)
            else "cpu"
        )
        cuda_profile = {
            "mode": cuda_mode,
            "requested_device": stt_config.get("device") or "cpu",
            "requested_profile": stt_config.get("profile") or "",
            "recommended_mode": recommended_cuda_mode,
            "selected_profile": selected_cuda_profile,
            "selected_image": "cuda" if selected_cuda_profile == "cuda" else "cpu",
            "docker_gpu_hint": docker_gpu_hint,
            "validation_action": "cuda-preflight",
            "validation_state": "not_checked",
            "warning": None if docker_gpu_hint else "nvidia-smi was not found on the host PATH.",
        }
        apply_plan = [
            {
                "id": "config_writes",
                "label": "Config writes",
                "status": "ready" if provider_setup.configured else "pending",
                "detail": "Persist provider selection and provider_configs in onboarding state.",
                "items": provider_setup.enabled_providers,
            },
            {
                "id": "model_downloads",
                "label": "Model downloads",
                "status": "ready" if selected_assets else "pending",
                "detail": "Download or sync selected STT/TTS/wake assets before service start.",
                "items": selected_assets,
            },
            {
                "id": "container_changes",
                "label": "Docker/container changes",
                "status": "ready" if service_targets else "not_required",
                "detail": "Install/start/recreate provider containers through their control scripts.",
                "items": service_targets,
            },
            {
                "id": "supervisor_registration",
                "label": "Supervisor registration",
                "status": "ready" if state.trust_activation.node_id else "blocked",
                "detail": "Register runtime services with Supervisor after node identity exists.",
                "items": [state.trust_activation.node_id] if state.trust_activation.node_id else [],
            },
            {
                "id": "health_validation",
                "label": "Health validation",
                "status": "ready" if not unhealthy and states else "blocked" if unhealthy else "pending",
                "detail": "Require enabled provider health checks before continuing.",
                "items": unhealthy or [provider["provider_id"] for provider in states],
            },
            {
                "id": "persisted_selections",
                "label": "Persisted selections",
                "status": "ready" if provider_setup.provider_configs else "pending",
                "detail": "Use saved STT/TTS/wake provider config during install, download, and restart actions.",
                "items": list(provider_setup.provider_configs.keys()),
            },
        ]
        supervisor_status = services.supervisor or {}
        supervisor_registration = {
            "node_id": state.trust_activation.node_id,
            "configured": bool(supervisor_status.get("configured")),
            "registered": bool(supervisor_status.get("registered")),
            "last_seen_at": supervisor_status.get("last_seen_at"),
            "last_error": supervisor_status.get("last_error"),
            "register_action": "/api/setup/supervisor/register-runtime",
            "blocked": not bool(state.trust_activation.node_id),
            "service_ids": ["backend", "frontend", *[provider["target"] for provider in states if provider.get("target")]],
        }
        for asset in asset_progress:
            if asset.get("state") in {"missing", "failed"}:
                blockers.append(f"selected_asset_{asset.get('state')}:{asset.get('provider_id')}:{asset.get('asset_id')}")
        for provider_id in provider_setup.enabled_providers:
            if provider_id not in {"voice", "deterministic", "openai"} and provider_id not in provider_configs:
                blockers.append(f"provider_config_missing:{provider_id}")
        if cuda_mode == "cuda" and not docker_gpu_hint:
            blockers.append("cuda_mode_unavailable")
        if supervisor_registration.get("last_error"):
            blockers.append("supervisor_registration_failed")
        blockers = list(dict.fromkeys(blockers))
        return {
            "configured": provider_setup.configured,
            "provider_setup": provider_setup.model_dump(mode="json"),
            "services": services.model_dump(mode="json"),
            "provider_states": states,
            "apply_plan": apply_plan,
            "asset_progress": asset_progress,
            "cuda_profile": cuda_profile,
            "supervisor_registration": supervisor_registration,
            "continue_blocked": bool(blockers),
            "blockers": blockers,
        }

    @app.get("/api/setup/providers/status")
    async def setup_providers_status() -> dict:
        return await asyncio.to_thread(setup_provider_status_payload)

    @app.post("/api/setup/providers/config", response_model=ProviderSetupResponse)
    async def setup_providers_config(payload: ProviderSetupRequest) -> ProviderSetupResponse:
        return provider_setup_service.save_setup(payload)

    @app.post("/api/setup/providers/apply")
    async def setup_providers_apply(payload: dict | None = None) -> dict:
        payload = payload or {}
        target = payload.get("target")
        action = str(payload.get("action") or "install").strip().lower()
        if target:
            result = await asyncio.to_thread(service.service_action, target=str(target), action=action)
            return {"actions": [result.model_dump(mode="json")], "status": await asyncio.to_thread(setup_provider_status_payload)}

        provider_setup = provider_setup_service.status_payload()
        target_by_provider = {
            "external_faster_whisper": app_settings.voice_stt_service_id,
            "faster_whisper": app_settings.voice_stt_service_id,
            "piper": app_settings.piper_tts_service_id,
            "openwakeword": app_settings.openwakeword_service_id,
            "supervised_openwakeword": app_settings.openwakeword_service_id,
        }
        actions = []
        seen_targets: set[str] = set()
        for provider_id in provider_setup.enabled_providers:
            provider_target = target_by_provider.get(provider_id)
            if not provider_target or provider_target in seen_targets:
                continue
            seen_targets.add(provider_target)
            for provider_action in setup_provider_action_sequence(action):
                result = await asyncio.to_thread(service.service_action, target=provider_target, action=provider_action)
                actions.append(result.model_dump(mode="json"))
                if not result.accepted:
                    break
        return {"actions": actions, "status": await asyncio.to_thread(setup_provider_status_payload)}

    def setup_capabilities_status_payload() -> dict:
        state = onboarding_state_store.load()
        capabilities = service.capabilities_payload()
        readiness = service.readiness_payload()
        provider_setup = provider_setup_service.status_payload()
        manifest_preview = capability_service.manifest_preview()
        selected = set(capabilities.selected)
        declared = set(capabilities.declared)
        capability_current = (
            capabilities.capability_status in {"accepted", "declared"}
            and bool(selected)
            and selected.issubset(declared)
        )
        governance_current = (
            state.governance_sync.governance_sync_status == "issued"
            and bool(state.governance_sync.governance_version)
            and bool(state.governance_sync.governance_bundle)
        )

        def manifest_validation_errors(preview: dict) -> list[str]:
            manifest = preview.get("declaration_payload", {}).get("manifest", {})
            node = manifest.get("node") if isinstance(manifest, dict) else {}
            errors: list[str] = []
            if not node.get("node_id"):
                errors.append("node_id_missing")
            if not node.get("node_name"):
                errors.append("node_name_missing")
            if not manifest.get("declared_capabilities"):
                errors.append("capabilities_missing")
            if not manifest.get("enabled_providers"):
                errors.append("enabled_providers_missing")
            if not isinstance(manifest.get("capability_endpoints"), dict):
                errors.append("capability_endpoints_invalid")
            try:
                validate_capability_declaration(manifest)
            except CapabilityManifestValidationError as exc:
                errors.append(str(exc))
            return errors

        def provider_health_blockers(selected_capabilities: set[str]) -> list[str]:
            try:
                services = service.service_status_payload()
            except Exception as exc:
                return [f"provider_health_unavailable:{exc}"]
            components = {component.get("component_id"): component for component in services.model_dump(mode="json").get("components", [])}
            requirements = {
                "voice.inference": "stt",
                "voice.tts.synthesize": "tts",
                "voice.tts.audio_url": "tts",
            }
            health_blockers: list[str] = []
            for capability, component_id in requirements.items():
                if capability not in selected_capabilities:
                    continue
                component = components.get(component_id)
                if component and component.get("healthy") is False:
                    health_blockers.append(f"selected_capability_provider_unhealthy:{capability}:{component_id}")
            return health_blockers

        manifest_errors = manifest_validation_errors(manifest_preview)
        blockers: list[str] = []
        if state.trust_activation.trust_status != "trusted":
            blockers.append("untrusted_node")
        if not state.pre_trust.core_base_url:
            blockers.append("core_connection_not_configured")
        if not provider_setup.declaration_allowed:
            blockers.extend(provider_setup.blocking_reasons or ["provider_setup_incomplete"])
            blockers.append("provider_setup_incomplete")
        for error in manifest_errors:
            blockers.append(f"invalid_manifest:{error}")
        capability_error = state.capability_declaration.last_error or ""
        governance_error = state.governance_sync.last_error or ""
        if capability_error:
            blockers.append("core_declaration_rejected")
            if "core" in capability_error.lower():
                blockers.append("core_unavailable")
        if governance_error:
            blockers.append("governance_sync_failed")
            if "core" in governance_error.lower():
                blockers.append("core_unavailable")
        blockers.extend(provider_health_blockers(selected))
        if not capabilities.selected:
            blockers.append("capability_selection_required")
        if not capability_current:
            blockers.append("capability_declaration_not_current")
        if not governance_current:
            blockers.append("governance_not_current")

        def governance_items(bundle: dict, keys: tuple[str, ...]) -> list:
            for key in keys:
                value = bundle.get(key)
                if value is None:
                    continue
                if isinstance(value, list):
                    return value
                return [value]
            return []

        governance_bundle = state.governance_sync.governance_bundle or {}
        governance_summary = {
            "status": governance_bundle.get("status")
            or governance_bundle.get("governance_status")
            or state.governance_sync.governance_sync_status,
            "accepted": governance_items(
                governance_bundle,
                ("accepted", "accepted_changes", "accepted_requirements", "active_requirements"),
            ),
            "denied": governance_items(
                governance_bundle,
                ("denied", "denied_changes", "denied_requirements", "rejected", "rejected_requirements"),
            ),
            "pending": governance_items(
                governance_bundle,
                ("pending", "pending_changes", "pending_requirements", "review_required"),
            ),
            "local_required_changes": governance_items(
                governance_bundle,
                ("local_required_changes", "required_local_changes", "node_required_changes", "required_changes"),
            ),
            "raw_bundle_present": bool(governance_bundle),
        }
        core_base_url = (state.pre_trust.core_base_url or "").rstrip("/")
        node_id = state.trust_activation.node_id or ""
        recovery_actions = {
            "rebuild_manifest": True,
            "redeclaration": True,
            "governance_sync": True,
            "provider_health_check": True,
            "trust_recheck": True,
            "core_governance_url": f"{core_base_url}/system/governance" if core_base_url else None,
            "core_node_governance_url": f"{core_base_url}/system/nodes/{node_id}/governance"
            if core_base_url and node_id
            else None,
        }

        return {
            "capabilities": capabilities.model_dump(mode="json"),
            "manifest_preview": manifest_preview,
            "manifest_validation": {
                "valid": not manifest_errors,
                "errors": manifest_errors,
            },
            "provider_setup": provider_setup.model_dump(mode="json"),
            "governance": {
                "governance_sync_status": state.governance_sync.governance_sync_status,
                "governance_version": state.governance_sync.governance_version,
                "issued_timestamp": state.governance_sync.issued_timestamp,
                "refresh_interval_s": state.governance_sync.refresh_interval_s,
                "governance_bundle": state.governance_sync.governance_bundle or {},
                "governance_freshness_state": state.governance_sync.governance_freshness_state,
                "governance_outdated": state.governance_sync.governance_outdated,
                "last_refresh_request_at": state.governance_sync.last_refresh_request_at,
            },
            "governance_summary": governance_summary,
            "recovery_actions": recovery_actions,
            "operational": state.operational_status.model_dump(mode="json"),
            "readiness": readiness.model_dump(mode="json"),
            "capability_current": capability_current,
            "governance_current": governance_current,
            "continue_blocked": bool(blockers),
            "blockers": list(dict.fromkeys(blockers)),
        }

    def setup_action_error_payload(action: str, exc: Exception) -> dict:
        status_code = 500
        detail = str(exc)
        if isinstance(exc, HTTPException):
            status_code = exc.status_code
            detail = str(exc.detail)
        elif isinstance(exc, httpx.TimeoutException):
            status_code = 504
            detail = "core_request_timeout"
        elif isinstance(exc, httpx.HTTPError):
            status_code = 502
            detail = f"core_request_failed: {exc}"
        state = onboarding_state_store.load()
        if action == "declare":
            onboarding_state_store.save(
                state.model_copy(
                    update={
                        "capability_declaration": state.capability_declaration.model_copy(
                            update={
                                "capability_status": "rejected" if status_code < 500 else state.capability_declaration.capability_status,
                                "last_error": detail,
                            }
                        )
                    }
                )
            )
        elif action == "sync-governance":
            onboarding_state_store.save(
                state.model_copy(
                    update={
                        "governance_sync": state.governance_sync.model_copy(
                            update={
                                "governance_sync_status": "failed",
                                "last_error": detail,
                            }
                        )
                    }
                )
            )
        return {
            "accepted": False,
            "action": action,
            "status_code": status_code,
            "error": detail,
            "status": setup_capabilities_status_payload(),
        }

    @app.get("/api/setup/capabilities/status")
    async def setup_capabilities_status() -> dict:
        return await asyncio.to_thread(setup_capabilities_status_payload)

    @app.put("/api/setup/capabilities/selection")
    async def setup_capabilities_selection(payload: CapabilitySelectionRequest) -> dict:
        try:
            result = await asyncio.to_thread(capability_service.save_selection, payload)
        except (HTTPException, httpx.HTTPError) as exc:
            return await asyncio.to_thread(setup_action_error_payload, "selection", exc)
        return {
            "accepted": True,
            "action": "selection",
            "result": result.model_dump(mode="json"),
            "status": await asyncio.to_thread(setup_capabilities_status_payload),
        }

    @app.post("/api/setup/capabilities/declare")
    async def setup_capabilities_declare() -> dict:
        try:
            result = await asyncio.to_thread(capability_service.declare)
        except (HTTPException, httpx.HTTPError) as exc:
            return await asyncio.to_thread(setup_action_error_payload, "declare", exc)
        return {
            "accepted": True,
            "action": "declare",
            "result": result.model_dump(mode="json"),
            "status": await asyncio.to_thread(setup_capabilities_status_payload),
        }

    @app.post("/api/setup/capabilities/sync-governance")
    async def setup_capabilities_sync_governance() -> dict:
        try:
            result = await asyncio.to_thread(governance_service.refresh)
        except (HTTPException, httpx.HTTPError) as exc:
            return await asyncio.to_thread(setup_action_error_payload, "sync-governance", exc)
        return {
            "accepted": True,
            "action": "sync-governance",
            "result": result.model_dump(mode="json"),
            "status": await asyncio.to_thread(setup_capabilities_status_payload),
        }

    def setup_ready_state_path() -> Path:
        return app_settings.runtime_dir / "setup" / "ready-state.json"

    def read_setup_ready_state() -> dict:
        try:
            payload = json.loads(setup_ready_state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def write_setup_ready_state(payload: dict) -> None:
        payload["updated_at"] = datetime.now(UTC).isoformat()
        path = setup_ready_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)

    def setup_ready_check(
        check_id: str,
        label: str,
        ok: bool,
        message: str,
        *,
        required: bool = True,
        detail: dict | None = None,
    ) -> dict:
        return {
            "id": check_id,
            "label": label,
            "status": "pass" if ok else ("fail" if required else "warn"),
            "required": required,
            "message": message,
            "detail": detail or {},
        }

    def setup_ready_runtime_missing() -> list[str]:
        config_path = Path("config/runtime-dirs.json")
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ["config/runtime-dirs.json"]
        runtime_dirs = payload.get("runtime_dirs")
        if not isinstance(runtime_dirs, list):
            return ["config/runtime-dirs.json"]
        return [
            str(item)
            for item in runtime_dirs
            if str(item).strip() and not (app_settings.runtime_dir / str(item)).is_dir()
        ]

    def setup_ready_frontend_url() -> str:
        host_payload = setup_host_readiness_service.readiness_payload()
        return (app_settings.public_ui_base_url or host_payload.ui_base_url).rstrip("/") + "/"

    def setup_ready_smoke_payload() -> dict:
        checks: list[dict] = []
        state = onboarding_state_store.load()
        provider_status = setup_provider_status_payload()
        capability_status = setup_capabilities_status_payload()
        host_status = setup_host_readiness_service.readiness_payload()
        service_status = service.service_status_payload().model_dump(mode="json")
        service_components = {
            component.get("component_id"): component
            for component in service_status.get("components", [])
            if component.get("component_id")
        }
        enabled_providers = set(provider_status.get("provider_setup", {}).get("enabled_providers") or [])
        selected_capabilities = set(capability_status.get("capabilities", {}).get("selected") or [])

        checks.append(setup_ready_check("backend", "Backend API", True, "Backend API handled the smoke request."))

        frontend_url = setup_ready_frontend_url()
        try:
            response = httpx.get(frontend_url, timeout=2.0, follow_redirects=False)
            frontend_ok = response.status_code < 500
            frontend_message = f"Frontend returned HTTP {response.status_code}."
        except httpx.HTTPError as exc:
            frontend_ok = False
            frontend_message = f"Frontend is unreachable: {exc}"
        checks.append(
            setup_ready_check(
                "frontend",
                "Frontend",
                frontend_ok,
                frontend_message,
                detail={"url": frontend_url},
            )
        )

        trusted = (
            state.trust_activation.trust_status == "trusted"
            and bool(state.trust_activation.node_id)
            and bool(state.trust_activation.node_trust_token)
        )
        checks.append(
            setup_ready_check(
                "trust",
                "Trust",
                trusted,
                "Node trust is active." if trusted else "Node trust is not active.",
                detail={"trust_status": state.trust_activation.trust_status, "node_id": state.trust_activation.node_id},
            )
        )

        governance_current = bool(capability_status.get("governance_current"))
        checks.append(
            setup_ready_check(
                "governance",
                "Governance",
                governance_current,
                "Governance bundle is current." if governance_current else "Governance has not been refreshed locally.",
                detail=capability_status.get("governance", {}),
            )
        )

        provider_blockers = list(provider_status.get("blockers") or [])
        provider_states = provider_status.get("provider_states") or []
        unhealthy_providers = [
            provider.get("provider_id")
            for provider in provider_states
            if provider.get("state") == "failed" or provider.get("healthy") is False
        ]
        providers_ok = not provider_blockers and not unhealthy_providers
        checks.append(
            setup_ready_check(
                "providers",
                "Providers",
                providers_ok,
                "Enabled providers are ready." if providers_ok else "Provider readiness is still blocked.",
                detail={"blockers": provider_blockers, "unhealthy": unhealthy_providers, "states": provider_states},
            )
        )
        provider_requirements = [
            ("stt_provider_response", "STT provider response", "stt", "voice.inference" in selected_capabilities),
            (
                "tts_provider_response",
                "TTS provider response",
                "tts",
                bool({"voice.tts.synthesize", "voice.tts.audio_url"} & selected_capabilities),
            ),
            (
                "wake_provider_response",
                "Wake provider response",
                "wake",
                bool({"openwakeword", "supervised_openwakeword"} & enabled_providers),
            ),
        ]
        for check_id, label, component_id, required in provider_requirements:
            component = service_components.get(component_id) or {}
            healthy = bool(component.get("healthy"))
            status = component.get("status") or "unknown"
            checks.append(
                setup_ready_check(
                    check_id,
                    label,
                    healthy,
                    f"{label} is healthy." if healthy else f"{label} is {status}.",
                    required=required,
                    detail=component,
                )
            )

        backend_component = service_components.get("backend") or {}
        backend_provider_ok = bool(backend_component.get("healthy", True)) and all(
            (service_components.get(component_id) or {}).get("healthy")
            for capability, component_id in {
                "voice.inference": "stt",
                "voice.tts.synthesize": "tts",
                "voice.tts.audio_url": "tts",
            }.items()
            if capability in selected_capabilities
        )
        checks.append(
            setup_ready_check(
                "backend_provider_calls",
                "Backend provider calls",
                backend_provider_ok,
                "Backend can route selected voice capabilities to providers."
                if backend_provider_ok
                else "Backend provider routing has unhealthy selected providers.",
                detail={"selected_capabilities": sorted(selected_capabilities), "components": service_components},
            )
        )

        runtime_missing = setup_ready_runtime_missing()
        checks.append(
            setup_ready_check(
                "runtime_dirs",
                "Runtime directories",
                not runtime_missing,
                "Runtime directory skeleton is present." if not runtime_missing else "Runtime directories are missing.",
                detail={"missing": runtime_missing[:30]},
            )
        )
        sockets_dir = app_settings.runtime_dir / "sockets"
        checks.append(
            setup_ready_check(
                "sockets",
                "Runtime sockets",
                sockets_dir.is_dir(),
                "Socket directory is present." if sockets_dir.is_dir() else "Socket directory is missing.",
                detail={"path": str(sockets_dir)},
            )
        )

        firmware_dir = app_settings.resolved_firmware_artifact_dir()
        firmware_files = list(firmware_dir.glob("*.bin")) if firmware_dir.exists() else []
        manifest_present = (firmware_dir / "manifest.json").exists()
        firmware_ok = bool(firmware_files) and manifest_present
        checks.append(
            setup_ready_check(
                "firmware",
                "Firmware artifacts",
                firmware_ok,
                "Firmware manifest and binaries are present." if firmware_ok else "Firmware manifest or binaries are missing.",
                detail={"dir": str(firmware_dir), "bin_count": len(firmware_files), "manifest": manifest_present},
            )
        )

        lan_ok = bool(host_status.lan_host and host_status.api_base_url and host_status.ui_base_url)
        checks.append(
            setup_ready_check(
                "lan_urls",
                "LAN URLs",
                lan_ok,
                "LAN URLs are available." if lan_ok else "LAN URLs are incomplete.",
                detail={"lan_host": host_status.lan_host, "api_base_url": host_status.api_base_url, "ui_base_url": host_status.ui_base_url},
            )
        )
        host_alias = next((check for check in host_status.checks if check.id == "host_alias"), None)
        checks.append(
            setup_ready_check(
                "host_alias",
                "HexeVoice host alias",
                bool(host_alias and host_alias.status == "pass"),
                host_alias.message if host_alias else "Host alias check unavailable.",
                required=False,
                detail=host_alias.detail if host_alias else {},
            )
        )

        try:
            if not state.pre_trust.core_base_url or not state.trust_activation.node_id or not state.trust_activation.node_trust_token:
                raise HTTPException(status_code=400, detail="core_visibility_identity_missing")
            response = httpx.get(
                f"{state.pre_trust.core_base_url.rstrip('/')}/api/system/nodes/operational-status/{state.trust_activation.node_id}",
                headers={"X-Node-Trust-Token": state.trust_activation.node_trust_token},
                timeout=5.0,
            )
            response.raise_for_status()
            core_detail = response.json()
            core_visible = bool(core_detail.get("operational_ready"))
            core_message = "Core reports this node operational." if core_visible else "Core can see the node but does not report operational readiness."
        except (HTTPException, httpx.HTTPError) as exc:
            core_visible = False
            core_message = f"Core node visibility check failed: {exc.detail if isinstance(exc, HTTPException) else exc}"
            core_detail = {}
        checks.append(
            setup_ready_check(
                "core_node_visibility",
                "Core node visibility",
                core_visible,
                core_message,
                detail=core_detail,
            )
        )
        core_trust_ok = core_detail.get("trust_status") == "trusted"
        checks.append(
            setup_ready_check(
                "core_trust_visibility",
                "Core trust visibility",
                core_trust_ok,
                "Core reports trusted node state." if core_trust_ok else "Core does not report trusted node state.",
                detail=core_detail,
            )
        )
        core_capability_ok = core_detail.get("capability_status") in {"accepted", "declared"}
        checks.append(
            setup_ready_check(
                "core_capability_visibility",
                "Core capability visibility",
                core_capability_ok,
                "Core reports accepted capabilities." if core_capability_ok else "Core capability status is not accepted.",
                detail=core_detail,
            )
        )
        local_governance_version = capability_status.get("governance", {}).get("governance_version")
        core_governance_version = core_detail.get("active_governance_version")
        governance_currency_ok = governance_current and (
            not core_governance_version or core_governance_version == local_governance_version
        )
        checks.append(
            setup_ready_check(
                "governance_currency",
                "Governance currency",
                governance_currency_ok,
                "Local governance is current with Core."
                if governance_currency_ok
                else "Local governance is not current with Core.",
                detail={"local_governance_version": local_governance_version, "core_governance_version": core_governance_version},
            )
        )
        supervisor_registration = provider_status.get("supervisor_registration") or {}
        supervisor_configured = bool(supervisor_registration.get("configured"))
        supervisor_registered = bool(supervisor_registration.get("registered"))
        checks.append(
            setup_ready_check(
                "supervisor_registration",
                "Supervisor registration",
                supervisor_registered if supervisor_configured else False,
                "Supervisor has runtime service registration."
                if supervisor_registered
                else "Supervisor registration is pending or not configured.",
                required=supervisor_configured,
                detail=supervisor_registration,
            )
        )

        failures = [check for check in checks if check["required"] and check["status"] == "fail"]
        warnings = [check for check in checks if check["status"] == "warn"]
        payload = {
            "ok": not failures,
            "completed": False,
            "summary": {
                "passed": len([check for check in checks if check["status"] == "pass"]),
                "failed": len(failures),
                "warnings": len(warnings),
            },
            "checks": checks,
            "ran_at": datetime.now(UTC).isoformat(),
        }
        ready_state = read_setup_ready_state()
        ready_state["last_smoke"] = payload
        write_setup_ready_state(ready_state)
        return payload

    def setup_ready_status_payload() -> dict:
        ready_state = read_setup_ready_state()
        state = onboarding_state_store.load()
        last_smoke = ready_state.get("last_smoke") if isinstance(ready_state.get("last_smoke"), dict) else None
        completed = bool(ready_state.get("completed_at"))
        return {
            "completed": completed,
            "completed_at": ready_state.get("completed_at"),
            "continue_blocked": not bool(last_smoke and last_smoke.get("ok")),
            "current_step_id": state.normalized_current_step_id(),
            "operational_ready": state.operational_status.operational_ready,
            "last_smoke": last_smoke,
            "setup_root_redirect_active": not completed,
            "dashboard_url": (app_settings.public_ui_base_url or setup_host_readiness_service.readiness_payload().ui_base_url).rstrip("/") + "/",
        }

    @app.get("/api/setup/ready/status")
    async def setup_ready_status() -> dict:
        return await asyncio.to_thread(setup_ready_status_payload)

    @app.post("/api/setup/ready/run-smoke-test")
    async def setup_ready_run_smoke_test() -> dict:
        smoke = await asyncio.to_thread(setup_ready_smoke_payload)
        return {"accepted": True, "smoke": smoke, "status": await asyncio.to_thread(setup_ready_status_payload)}

    @app.post("/api/setup/ready/complete")
    async def setup_ready_complete() -> dict:
        ready_state = await asyncio.to_thread(read_setup_ready_state)
        last_smoke = ready_state.get("last_smoke") if isinstance(ready_state.get("last_smoke"), dict) else None
        if not last_smoke or not last_smoke.get("ok"):
            return {
                "accepted": False,
                "message": "required_smoke_checks_not_passed",
                "status": await asyncio.to_thread(setup_ready_status_payload),
            }
        state = onboarding_state_store.load()
        updated = state.model_copy(
            update={
                "operational_status": state.operational_status.model_copy(update={"operational_ready": True}),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": "ready",
                        "last_completed_step_id": "governance_sync",
                        "last_transition_at": datetime.now(UTC).isoformat(),
                    }
                ),
            }
        )
        onboarding_state_store.save(updated)
        ready_state["completed_at"] = datetime.now(UTC).isoformat()
        await asyncio.to_thread(write_setup_ready_state, ready_state)
        return {
            "accepted": True,
            "message": "setup_complete",
            "status": await asyncio.to_thread(setup_ready_status_payload),
        }

    @app.put("/api/providers/setup", response_model=ProviderSetupResponse)
    async def provider_setup_save(payload: ProviderSetupRequest) -> ProviderSetupResponse:
        return provider_setup_service.save_setup(payload)

    @app.put("/api/node/ui/providers/{provider_id}/setup", response_model=ProviderSetupResponse)
    async def node_ui_provider_setup_save(provider_id: str, payload: ProviderConfigRequest) -> ProviderSetupResponse:
        response = provider_setup_service.save_provider_setup(provider_id, payload)
        if provider_id == "piper":
            current_tts = await asyncio.to_thread(tts_runtime_settings_service.status)
            updated_tts = await asyncio.to_thread(
                tts_runtime_settings_service.update,
                {
                    "default_voice": payload.default_voice or current_tts.get("default_voice"),
                    "warm_voices": payload.warm_models or current_tts.get("warm_voices", []),
                    "conversion_sample_rates_hz": current_tts.get("conversion_sample_rates_hz", []),
                    "conversion_policy": current_tts.get("conversion_policy"),
                },
            )
            try:
                applied = await apply_piper_tts_provider_config(
                    ProviderConfigRequest(
                        default_voice=updated_tts.get("default_voice"),
                        warm_models=updated_tts.get("warm_voices") if isinstance(updated_tts.get("warm_voices"), list) else None,
                    )
                )
                if applied:
                    await asyncio.to_thread(tts_runtime_settings_service.clear_restart_required)
            except httpx.HTTPError:
                pass
        if provider_id == "external_faster_whisper" and (
            payload.profile
            or payload.fallback_profile
            or payload.model
            or payload.warm_models
            or payload.device
            or payload.compute_type
        ):
            try:
                await apply_external_stt_provider_config(payload)
            except httpx.HTTPError:
                pass
        return response

    @app.get("/api/capabilities", response_model=CapabilitySummaryResponse)
    async def capabilities_status() -> CapabilitySummaryResponse:
        return service.capabilities_payload()

    @app.put("/api/capabilities/selection", response_model=CapabilitySummaryResponse)
    async def capabilities_selection_save(payload: CapabilitySelectionRequest) -> CapabilitySummaryResponse:
        return capability_service.save_selection(payload)

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
        return await asyncio.to_thread(service.service_status_payload)

    @app.post("/api/services/start", response_model=ServiceActionResponse)
    async def service_start(payload: ServiceActionRequest) -> ServiceActionResponse:
        result = await asyncio.to_thread(service.service_action, target=payload.target, action="start")
        schedule_external_stt_provider_reconcile(result, "start")
        return result

    @app.post("/api/services/stop", response_model=ServiceActionResponse)
    async def service_stop(payload: ServiceActionRequest) -> ServiceActionResponse:
        return await asyncio.to_thread(service.service_action, target=payload.target, action="stop")

    @app.post("/api/services/install", response_model=ServiceActionResponse)
    async def service_install(payload: ServiceActionRequest) -> ServiceActionResponse:
        result = await asyncio.to_thread(service.service_action, target=payload.target, action="install")
        schedule_external_stt_provider_reconcile(result, "install")
        return result

    @app.post("/api/services/restart", response_model=ServiceActionResponse)
    async def service_restart(payload: ServiceActionRequest) -> ServiceActionResponse:
        result = await asyncio.to_thread(service.service_action, target=payload.target, action="restart")
        if result.accepted and result.target in {app_settings.piper_tts_service_id, "tts"}:
            await asyncio.to_thread(tts_runtime_settings_service.clear_restart_required)
        schedule_external_stt_provider_reconcile(result, "restart")
        return result

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
