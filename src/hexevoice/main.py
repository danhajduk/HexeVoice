import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import re
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
    ProviderStatusResponse,
    ServiceActionRequest,
    ServiceActionResponse,
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
from hexevoice.endpoint.media import EndpointMediaAsset, EndpointMediaService, EndpointMediaValidationError
from hexevoice.endpoint.service import EndpointHeartbeatService
from hexevoice.onboarding.approval import ApprovalPollingService
from hexevoice.config.settings import Settings
from hexevoice.governance.service import GovernanceService
from hexevoice import node_ui
from hexevoice.onboarding.bootstrap import BootstrapDiscoveryService
from hexevoice.onboarding.registration_metadata import RegistrationMetadataRefreshService
from hexevoice.onboarding.session_start import OnboardingSessionStartService
from hexevoice.onboarding.service import OnboardingStateService
from hexevoice.onboarding.trust_activation import TrustActivationService
from hexevoice.persistence import EndpointRegistryStore, OnboardingStateStore, VoiceSessionHistoryStore
from hexevoice.providers.setup import ProviderSetupService
from hexevoice.runtime.service import NodeRuntimeService
from hexevoice.supervisor.client import SupervisorApiClient
from hexevoice.timer_announcements import TimerSucceededAnnouncementService
from hexevoice.trust.status import TrustStatusService
from hexevoice.tts import TtsAudioService
from hexevoice.tts.runtime_settings import TtsRuntimeSettingsService
from hexevoice.voice import MicroVadChunkRecordingService, VoiceSessionManager, WakeDetector, WakeRecordingService
from hexevoice.voice.pipeline import build_voice_turn_pipeline
from hexevoice.voice.wake import build_wake_detector


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
        async with httpx.AsyncClient(timeout=2.0) as client:
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
    service = NodeRuntimeService(
        settings=app_settings,
        onboarding_state_store=onboarding_state_store,
        supervisor_client=supervisor_client,
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

    @app.on_event("shutdown")
    async def stop_background_services():
        timer_announcement_service.stop()
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

    @app.post("/api/assistant/turn", response_model=AssistantTurnResponse)
    async def assistant_turn(payload: AssistantTurnRequest) -> AssistantTurnResponse:
        return assistant_service.handle_turn(payload)

    @app.get("/api/voice/intents", response_model=VoiceIntentStateResponse)
    async def voice_intents_list() -> VoiceIntentStateResponse:
        return VoiceIntentStateResponse.model_validate(voice_intent_registry.snapshot())

    @app.post("/api/voice/intents", response_model=VoiceIntentStateResponse)
    async def voice_intents_register(payload: VoiceIntentRegisterRequest) -> VoiceIntentStateResponse:
        try:
            state = voice_intent_registry.register_intent(**payload.model_dump(mode="python"))
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
        return VoiceIntentInvokeResponse(
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

    @app.post("/api/endpoint/heartbeat", response_model=EndpointHeartbeatResponse)
    async def endpoint_heartbeat(payload: EndpointHeartbeatRequest) -> EndpointHeartbeatResponse:
        return endpoint_service.record_heartbeat(payload)

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
        return status.model_copy(update={"firmware_update": firmware_update_payload(app_settings, status)})

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
        return await asyncio.to_thread(tts_runtime_settings_service.update, payload)

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
        return voice_session_manager.cancel_from_operator()

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

    @app.get("/api/node/ui/overview/node")
    async def node_ui_overview_node() -> dict:
        return node_ui.overview_node(
            app_settings,
            node_ui.as_json(service.status_payload()),
            node_ui.as_json(service.onboarding_payload()),
        )

    @app.get("/api/node/ui/overview/health")
    async def node_ui_overview_health() -> dict:
        return node_ui.overview_health(
            node_ui.as_json(service.status_payload()),
            node_ui.as_json(service.readiness_payload()),
            node_ui.as_json(provider_setup_service.status_payload()),
            node_ui.as_json(service.service_status_payload()),
            voice_session_manager.status(),
        )

    @app.get("/api/node/ui/overview/warnings")
    async def node_ui_overview_warnings() -> dict:
        return node_ui.overview_warnings(
            node_ui.as_json(service.status_payload()),
            node_ui.as_json(service.onboarding_payload()),
            node_ui.as_json(service.readiness_payload()),
            node_ui.as_json(service.service_status_payload()),
            voice_session_manager.status(),
        )

    @app.get("/api/node/ui/overview/facts")
    async def node_ui_overview_facts() -> dict:
        return node_ui.overview_facts(
            node_ui.as_json(service.status_payload()),
            node_ui.as_json(service.onboarding_payload()),
            node_ui_operational_status(),
            node_ui.as_json(provider_setup_service.status_payload()),
            voice_session_manager.status(),
        )

    @app.get("/api/node/ui/runtime/services")
    async def node_ui_runtime_services() -> dict:
        return node_ui.runtime_services(node_ui.as_json(service.service_status_payload()), voice_session_manager.status())

    @app.get("/api/node/ui/providers/status")
    async def node_ui_providers_status() -> dict:
        return node_ui.provider_status(
            node_ui.as_json(service.service_status_payload()),
            voice_session_manager.status(),
            await node_ui_tts_settings(),
        )

    @app.get("/api/node/ui/voice/endpoints")
    async def node_ui_voice_endpoints() -> dict:
        return node_ui.endpoint_records(node_ui_endpoint_statuses(), voice_session_manager.status())

    @app.get("/api/node/ui/voice/endpoint-actions")
    async def node_ui_voice_endpoint_actions() -> dict:
        return node_ui.endpoint_actions(voice_session_manager.status())

    @app.get("/api/node/ui/voice/sessions")
    async def node_ui_voice_sessions(limit: int = 20) -> dict:
        return node_ui.session_records(voice_session_manager.list_session_history(limit=limit))

    @app.get("/api/node/ui/voice/intents")
    async def node_ui_voice_intents() -> dict:
        return node_ui.intent_records(voice_intent_registry.snapshot())

    @app.get("/api/node/ui/voice/intent-actions")
    async def node_ui_voice_intent_actions() -> dict:
        return node_ui.intent_actions(voice_intent_registry.snapshot())

    @app.get("/api/node/ui/voice/tts")
    async def node_ui_voice_tts() -> dict:
        return node_ui.tts_runtime(await node_ui_tts_settings(), voice_session_manager.status())

    @app.get("/api/node/ui/voice/tts-artifacts")
    async def node_ui_voice_tts_artifacts(limit: int = 50) -> dict:
        artifacts = await asyncio.to_thread(tts_audio_service.list_artifacts, limit=limit)
        return node_ui.artifact_records(artifacts)

    @app.get("/api/node/ui/voice/media")
    async def node_ui_voice_media() -> dict:
        assets = {"assets": [node_ui.as_json(endpoint_media_response(asset)) for asset in endpoint_media_service.list_assets()]}
        return node_ui.media_records(assets, node_ui_endpoint_statuses())

    @app.post("/api/node/ui/actions/refresh-status")
    async def node_ui_refresh_status_action() -> dict:
        return {
            "accepted": True,
            "status": "refreshed",
            "updated_at": node_ui.utc_now(),
            "node": node_ui.as_json(service.status_payload()),
        }

    @app.post("/api/node/ui/actions/test-assistant-turn", response_model=AssistantTurnResponse)
    async def node_ui_test_assistant_turn_action() -> AssistantTurnResponse:
        return assistant_service.handle_turn(
            AssistantTurnRequest(endpoint_id="core-rendered-ui-test", text="hello"),
        )

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

    @app.post("/api/onboarding/registration-metadata/refresh")
    async def onboarding_registration_metadata_refresh() -> dict:
        return registration_metadata_refresh_service.refresh()

    @app.get("/api/providers/setup", response_model=ProviderSetupResponse)
    async def provider_setup_status() -> ProviderSetupResponse:
        return provider_setup_service.status_payload()

    @app.put("/api/providers/setup", response_model=ProviderSetupResponse)
    async def provider_setup_save(payload: ProviderSetupRequest) -> ProviderSetupResponse:
        return provider_setup_service.save_setup(payload)

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
        return service.service_status_payload()

    @app.post("/api/services/start", response_model=ServiceActionResponse)
    async def service_start(payload: ServiceActionRequest) -> ServiceActionResponse:
        return await asyncio.to_thread(service.service_action, target=payload.target, action="start")

    @app.post("/api/services/stop", response_model=ServiceActionResponse)
    async def service_stop(payload: ServiceActionRequest) -> ServiceActionResponse:
        return await asyncio.to_thread(service.service_action, target=payload.target, action="stop")

    @app.post("/api/services/install", response_model=ServiceActionResponse)
    async def service_install(payload: ServiceActionRequest) -> ServiceActionResponse:
        return await asyncio.to_thread(service.service_action, target=payload.target, action="install")

    @app.post("/api/services/restart", response_model=ServiceActionResponse)
    async def service_restart(payload: ServiceActionRequest) -> ServiceActionResponse:
        result = await asyncio.to_thread(service.service_action, target=payload.target, action="restart")
        if result.accepted and result.target in {app_settings.piper_tts_service_id, "tts"}:
            await asyncio.to_thread(tts_runtime_settings_service.clear_restart_required)
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
