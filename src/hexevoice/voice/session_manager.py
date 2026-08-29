from __future__ import annotations

import asyncio
import base64
import contextvars
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from hexevoice.persistence.voice_placement_calibration import VoicePlacementCalibrationStore
from hexevoice.persistence.voice_quality_observation_log import VoiceQualityObservationLog
from hexevoice.persistence.voice_session_history import VoiceSessionHistoryStore
from hexevoice.voice.contracts import (
    ENDPOINT_TO_BACKEND_EVENTS,
    VoiceAudioChunkPayload,
    VoiceCommandAckPayload,
    VoiceCommandErrorPayload,
    VoiceErrorPayload,
    VoiceEventEnvelope,
    VoiceEventType,
    VoiceResponseTextPayload,
    VoiceSessionSnapshot,
    VoiceSessionStartPayload,
    VoiceSessionState,
    VoiceTranscriptPayload,
    VoiceTtsPlaybackPayload,
    VoiceTtsReadyPayload,
    VoiceVadSpeechStartedPayload,
    VoiceWakeCandidatePayload,
    is_valid_voice_session_transition,
    project_ux_state,
    project_voice_state,
)
from hexevoice.voice.pipeline import TtsSynthesis, VoiceTurnAudioSummary, VoiceTurnPipeline
from hexevoice.voice.audio_quality import analyze_pcm_s16le_audio
from hexevoice.voice.placement import (
    PlacementReportInput,
    build_active_placement_report,
    build_long_window_placement_report,
)
from hexevoice.voice.records import record_voice_event
from hexevoice.voice.micro_vad_chunks import MicroVadChunkRecordingService
from hexevoice.voice.wake import OpenWakeWordWakeDetector, WakeDetectionResult, WakeDetector
from hexevoice.voice.wake_election import (
    DEFAULT_WAKE_ELECTION_WINDOW_MS,
    WakeCandidate,
    WakeCandidateElection,
    WakeElectionDecision,
)
from hexevoice.voice.wake_recordings import WakeRecordingService


log = logging.getLogger(__name__)
FOLLOWUP_LISTEN_TIMEOUT_S = 10.0
PRE_AUDIO_SESSION_REPLACEMENT_GRACE_MS = 750

LATENCY_POINT_ORDER = {
    "vad_voice_detected": 0,
    "wake_word_detected": 1,
    "vad_silence": 2,
    "stt_start": 3,
    "stt_end": 4,
    "intent_processing_done": 5,
    "tts_start": 6,
    "tts_end": 7,
    "session_end": 8,
}

PLAYBACK_INTERRUPT_STOP_PHRASES = {
    "stop",
    "stop it",
    "stop timer",
    "stop the timer",
    "stop alarm",
    "stop the alarm",
    "silence timer",
    "silence the timer",
    "dismiss timer",
    "dismiss the timer",
    "turn off timer",
    "turn off the timer",
    "turn off alarm",
    "turn off the alarm",
}


def _normalize_playback_interrupt_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _level_to_ratio(value: object) -> float:
    if isinstance(value, (int, float)):
        return round(max(0.0, float(value)) / 32768.0, 6)
    return 0.0


def _is_playback_stop_phrase(text: str) -> bool:
    normalized = _normalize_playback_interrupt_text(text)
    return normalized in PLAYBACK_INTERRUPT_STOP_PHRASES


def tts_synthesis_metadata(tts: TtsSynthesis) -> dict[str, Any]:
    return {
        "content_type": tts.content_type,
        "stream_id": tts.stream_id,
        "audio_url": tts.audio_url,
        "endpoint_audio_url": tts.endpoint_audio_url,
        "audio_urls": tts.audio_urls,
        "provider_id": tts.provider_id,
        "model_id": tts.model_id,
        "voice_id": tts.voice_id,
        "audio_variant": tts.audio_variant,
        "audio_variant_sample_rate_hz": tts.audio_variant_sample_rate_hz,
        "audio_variant_source_sample_rate_hz": tts.audio_variant_source_sample_rate_hz,
        "audio_variants": tts.audio_variants,
        "planned_audio_variants": tts.planned_audio_variants,
        "pending_audio_variants": tts.pending_audio_variants,
        "conversion_policy": tts.conversion_policy,
        "raw_audio_path": tts.raw_audio_path,
        "raw_sample_rate_hz": tts.raw_sample_rate_hz,
        "output_sample_rate_hz": tts.output_sample_rate_hz,
        "variant_sample_rates_hz": tts.variant_sample_rates_hz,
        "tts_timing_breakdown_ms": tts.timing_breakdown_ms,
        "metadata_path": tts.metadata_path,
        "expires_at": tts.expires_at,
        "ttl_seconds": tts.ttl_seconds,
        "error": tts.error,
    }


def endpoint_tts_audio_url(tts: TtsSynthesis | dict[str, Any]) -> str | None:
    if isinstance(tts, TtsSynthesis):
        return tts.endpoint_audio_url or tts.audio_url
    return tts.get("endpoint_audio_url") or tts.get("audio_url")


@dataclass
class EndpointSessionRuntime:
    connection_active: bool = False
    websocket: WebSocket | None = None
    connected_endpoint_id: str | None = None
    active_session: VoiceSessionSnapshot | None = None
    chunk_count: int = 0
    audio_chunks: list[bytes] = field(default_factory=list)
    ambient_audio_chunks: list[bytes] = field(default_factory=list)
    audio_format: Any = None
    sequence: int = 0
    active_session_history: dict[str, Any] | None = None
    last_transcript: str | None = None
    last_response: str | None = None
    last_transcript_metadata: dict | None = None
    last_error: dict | None = None
    last_tts: dict | None = None
    last_tts_playback: dict | None = None
    tts_playback_history: list[dict[str, object]] = field(default_factory=list)
    active_playbacks: dict[str, dict[str, object]] = field(default_factory=dict)
    last_playback_interrupt: dict | None = None
    last_assistant: dict | None = None
    last_turn_timings: dict | None = None
    last_event_type: str | None = None
    last_command_ack: dict | None = None
    last_command_error: dict | None = None
    pending_session_followup: dict[str, Any] | None = None
    followup_timeout_task: asyncio.Task | None = None


def _runtime_property(field_name: str):
    def getter(self: "VoiceSessionManager"):
        return getattr(self._current_runtime(), field_name)

    def setter(self: "VoiceSessionManager", value):
        state = self._current_runtime()
        if field_name == "connected_endpoint_id":
            self._unbind_runtime_endpoint(state)
        setattr(state, field_name, value)
        if field_name == "connected_endpoint_id" and value:
            self._bind_runtime_endpoint(state, str(value))
        if field_name == "websocket" and value is not None and state.connected_endpoint_id:
            self._bind_runtime_endpoint(state, state.connected_endpoint_id)

    return property(getter, setter)


class VoiceSessionManager:
    _connection_active = _runtime_property("connection_active")
    _websocket = _runtime_property("websocket")
    _connected_endpoint_id = _runtime_property("connected_endpoint_id")
    _active_session = _runtime_property("active_session")
    _chunk_count = _runtime_property("chunk_count")
    _audio_chunks = _runtime_property("audio_chunks")
    _ambient_audio_chunks = _runtime_property("ambient_audio_chunks")
    _audio_format = _runtime_property("audio_format")
    _sequence = _runtime_property("sequence")
    _active_session_history = _runtime_property("active_session_history")
    _last_transcript = _runtime_property("last_transcript")
    _last_response = _runtime_property("last_response")
    _last_transcript_metadata = _runtime_property("last_transcript_metadata")
    _last_error = _runtime_property("last_error")
    _last_tts = _runtime_property("last_tts")
    _last_tts_playback = _runtime_property("last_tts_playback")
    _tts_playback_history = _runtime_property("tts_playback_history")
    _active_playbacks = _runtime_property("active_playbacks")
    _last_playback_interrupt = _runtime_property("last_playback_interrupt")
    _last_assistant = _runtime_property("last_assistant")
    _last_turn_timings = _runtime_property("last_turn_timings")
    _last_event_type = _runtime_property("last_event_type")
    _last_command_ack = _runtime_property("last_command_ack")
    _last_command_error = _runtime_property("last_command_error")
    _pending_session_followup = _runtime_property("pending_session_followup")
    _followup_timeout_task = _runtime_property("followup_timeout_task")

    def __init__(
        self,
        *,
        wake_detector: WakeDetector | None = None,
        turn_pipeline: VoiceTurnPipeline | None = None,
        wake_recorder: WakeRecordingService | None = None,
        micro_vad_chunk_recorder: MicroVadChunkRecordingService | None = None,
        session_history_store: VoiceSessionHistoryStore | None = None,
        placement_calibration_store: VoicePlacementCalibrationStore | None = None,
        quality_observation_log: VoiceQualityObservationLog | None = None,
        pre_wake_timeout_s: float = 10.0,
        max_active_session_s: float = 60.0,
        privacy_mode_enabled: bool = False,
        wake_election_window_ms: int = DEFAULT_WAKE_ELECTION_WINDOW_MS,
    ) -> None:
        self._default_runtime = EndpointSessionRuntime()
        self._runtime_context: contextvars.ContextVar[EndpointSessionRuntime | None] = contextvars.ContextVar(
            "hexevoice_endpoint_runtime",
            default=None,
        )
        self._endpoint_runtimes: dict[str, EndpointSessionRuntime] = {}
        self._wake_detector = wake_detector or OpenWakeWordWakeDetector()
        self._turn_pipeline = turn_pipeline
        self._wake_recorder = wake_recorder
        self._micro_vad_chunk_recorder = micro_vad_chunk_recorder
        self._session_history_store = session_history_store
        self._placement_calibration_store = placement_calibration_store
        self._quality_observation_log = quality_observation_log
        self._command_records: dict[str, dict[str, object]] = {}
        self._last_volume_percent_by_endpoint: dict[str, int] = {}
        self._command_timeout_s = 10.0
        self._pre_wake_timeout_s = pre_wake_timeout_s
        self._max_active_session_s = max_active_session_s
        self._privacy_mode_enabled = privacy_mode_enabled
        self._event_diagnostics: list[dict[str, object]] = []
        self._wake_history: list[dict[str, object]] = []
        self._wake_confidence_history: list[dict[str, object]] = []
        self._wake_election = WakeCandidateElection(window_ms=wake_election_window_ms)
        self._speaker_enrollment_capture_windows: dict[str, dict[str, object]] = {}
        self._active_placement_test_windows: dict[str, dict[str, object]] = {}

    def _current_runtime(self) -> EndpointSessionRuntime:
        runtime_context = getattr(self, "_runtime_context", None)
        if runtime_context is None:
            return self._default_runtime
        return runtime_context.get() or self._default_runtime

    def _bind_runtime_endpoint(self, state: EndpointSessionRuntime, endpoint_id: str) -> None:
        existing = self._endpoint_runtimes.get(endpoint_id)
        if (
            existing is not None
            and existing is not state
            and existing.connection_active
            and existing.websocket is not None
        ):
            return
        self._endpoint_runtimes[endpoint_id] = state

    def _unbind_runtime_endpoint(self, state: EndpointSessionRuntime) -> None:
        endpoint_id = state.connected_endpoint_id
        if endpoint_id and self._endpoint_runtimes.get(endpoint_id) is state:
            del self._endpoint_runtimes[endpoint_id]

    def _runtime_for_endpoint(self, endpoint_id: str) -> EndpointSessionRuntime | None:
        state = self._endpoint_runtimes.get(endpoint_id)
        if state is not None and state.connection_active and state.websocket is not None:
            return state
        if (
            self._default_runtime.connection_active
            and self._default_runtime.websocket is not None
            and self._default_runtime.connected_endpoint_id == endpoint_id
        ):
            return self._default_runtime
        return None

    def _runtime_switch_for_endpoint(self, endpoint_id: str) -> EndpointSessionRuntime | None:
        runtime = self._runtime_for_endpoint(endpoint_id)
        return runtime if runtime is not None and runtime is not self._current_runtime() else None

    def _status_runtime(self) -> EndpointSessionRuntime:
        for state in self._endpoint_runtimes.values():
            if state.active_session is not None:
                return state
        for state in self._endpoint_runtimes.values():
            if state.connection_active:
                return state
        for state in self._endpoint_runtimes.values():
            if state.last_event_type is not None:
                return state
        return self._default_runtime

    def _runtime_status_summary(self, state: EndpointSessionRuntime) -> dict[str, Any]:
        active_session = state.active_session
        active_snapshot = active_session.model_dump(mode="json") if active_session else None
        projection = project_voice_state(
            connection_active=state.connection_active,
            active_session=active_session,
        ).model_dump(mode="json")
        return {
            "endpoint_id": state.connected_endpoint_id,
            "connection_state": projection["connection_state"],
            "ux_state": projection["ux_state"],
            "session_state": projection["session_state"],
            "transport_health": projection["transport_health"],
            "active_session": active_snapshot,
            "last_event_type": state.last_event_type,
            "last_session_id": active_snapshot["session_id"] if active_snapshot else None,
        }

    @staticmethod
    def _status_visible_session(state: EndpointSessionRuntime) -> VoiceSessionSnapshot | None:
        session = state.active_session
        if session is None:
            return None
        if session.session_state != "idle":
            return session
        wake = state.active_session_history.get("wake") if state.active_session_history else None
        if isinstance(wake, dict) and wake.get("outcome") == "accepted":
            return session
        if session.wake_source in {"button", "manual"}:
            return session
        return None

    def _active_session_timeout_reason(self, *, now: datetime) -> str | None:
        if self._active_session is None:
            return None
        age_s = (now - self._active_session.started_at).total_seconds()
        if self._active_session.session_state == "idle" and age_s > self._pre_wake_timeout_s:
            return "pre_wake_timeout"
        if (
            self._active_session.session_state in {"wake_detected", "listening", "capturing"}
            and age_s > self._max_active_session_s
        ):
            return "active_session_timeout"
        return None

    def _cancel_timed_out_active_session(self, *, reason: str) -> VoiceEventEnvelope | None:
        if self._active_session is None:
            return None
        session = self._active_session
        self._set_session_state("cancelled")
        session.cancel_reason = reason
        cancelled = self._state_event(
            "session.cancelled",
            session,
            extra_payload={
                "reason": reason,
                "message": "Active voice session timed out before audio completion.",
                "timeout_s": self._pre_wake_timeout_s if reason == "pre_wake_timeout" else self._max_active_session_s,
            },
        )
        self._persist_active_session_history(session, completion_reason=reason)
        self._release_active_session_wake_stream()
        self._clear_active_session_runtime()
        return cancelled

    def _expire_stale_active_sessions(self) -> None:
        now = datetime.now(UTC)
        for state in list(self._endpoint_runtimes.values()) + [self._default_runtime]:
            if not state.connection_active or state.active_session is None:
                continue
            token = self._runtime_context.set(state)
            try:
                timeout_reason = self._active_session_timeout_reason(now=now)
                if timeout_reason is not None:
                    log.warning(
                        "Voice session timed out: endpoint_id=%s session_id=%s reason=%s",
                        self._active_session.endpoint_id,
                        self._active_session.session_id,
                        timeout_reason,
                    )
                    self._cancel_timed_out_active_session(reason=timeout_reason)
            finally:
                self._runtime_context.reset(token)

    async def handle_websocket(self, websocket: WebSocket, *, endpoint_id: str | None = None) -> None:
        await websocket.accept()
        initial_endpoint_id = endpoint_id.strip() if endpoint_id else None
        runtime = EndpointSessionRuntime(connection_active=True, websocket=websocket)
        token = self._runtime_context.set(runtime)
        self._connection_active = True
        self._websocket = websocket
        if initial_endpoint_id:
            if self._runtime_for_endpoint(initial_endpoint_id) is not None:
                await websocket.send_json(
                    self._error_event(
                        endpoint_id=initial_endpoint_id,
                        session_id=None,
                        code="endpoint_already_connected",
                        message="This endpoint already has an active WebSocket.",
                        recoverable=False,
                    ).model_dump(mode="json")
                )
                await websocket.close(code=1008)
                return
            self._connected_endpoint_id = initial_endpoint_id
            log.info("Voice endpoint bound to WebSocket: endpoint_id=%s source=query", initial_endpoint_id)
        log.info("Voice WebSocket connected")
        try:
            while True:
                raw_message = await websocket.receive_text()
                log.debug("Received voice WebSocket message bytes=%s", len(raw_message))
                for event in self._handle_raw_message(raw_message):
                    log.debug(
                        "Sending voice event: event_type=%s endpoint_id=%s session_id=%s sequence=%s",
                        event.event_type,
                        event.endpoint_id,
                        event.session_id,
                        event.sequence,
                    )
                    await websocket.send_json(event.model_dump(mode="json"))
        except WebSocketDisconnect:
            pass
        finally:
            if self._active_session is not None:
                self._set_session_state("cancelled")
                self._active_session.cancel_reason = "websocket_disconnected"
                self._persist_active_session_history(
                    self._active_session,
                    completion_reason="websocket_disconnected",
                )
            self._release_active_session_wake_stream()
            self._cancel_followup_timeout_task()
            self._websocket = None
            self._connection_active = False
            self._clear_active_session_runtime()
            self._runtime_context.reset(token)
            log.info("Voice WebSocket disconnected")

    async def push_ota_update(
        self,
        *,
        endpoint_id: str,
        firmware_url: str,
        version: str | None,
        profile: str | None,
        sha256: str | None,
        size_bytes: int | None,
        signature_algorithm: str | None,
        signature_key_id: str | None,
        manifest_signature: str | None,
    ) -> dict:
        runtime = self._runtime_for_endpoint(endpoint_id)
        if runtime is None:
            log.warning("OTA push rejected: endpoint_id=%s reason=endpoint_not_connected", endpoint_id)
            return {"accepted": False, "reason": "endpoint_not_connected"}

        log.info(
            "OTA push accepted for endpoint: endpoint_id=%s version=%s size_bytes=%s",
            endpoint_id,
            version,
            size_bytes,
        )
        token = self._runtime_context.set(runtime)
        try:
            request_id = f"cmd_{uuid4().hex}"
            event = VoiceEventEnvelope(
                event_type="ota.update",
                endpoint_id=endpoint_id,
                direction="backend_to_endpoint",
                session_id=self._active_session.session_id if self._active_session else None,
                sequence=self._next_sequence(),
                payload={
                    "request_id": request_id,
                    "url": firmware_url,
                    "version": version,
                    "profile": profile,
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                    "signature_algorithm": signature_algorithm,
                    "signature_key_id": signature_key_id,
                    "manifest_signature": manifest_signature,
                },
            )
            await runtime.websocket.send_json(event.model_dump(mode="json"))
            self._last_event_type = "ota.update"
            record = self._record_command(
                request_id=request_id,
                endpoint_id=endpoint_id,
                command_type="ota.update",
                event_type="ota.update",
                timeout_s=180.0,
            )
            return {"accepted": True, "request_id": request_id, "status": record["status"]}
        finally:
            self._runtime_context.reset(token)

    def clear_ota_commands(self, *, endpoint_id: str | None = None) -> int:
        request_ids = [
            request_id
            for request_id, record in self._command_records.items()
            if record.get("command_type") == "ota.update"
            and (endpoint_id is None or record.get("endpoint_id") == endpoint_id)
        ]
        for request_id in request_ids:
            del self._command_records[request_id]
        return len(request_ids)

    async def push_volume_command(self, *, endpoint_id: str, volume_percent: int) -> dict:
        result = await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.volume",
            command_type="endpoint.volume.set",
            payload={"volume_percent": volume_percent},
        )
        if result.get("accepted"):
            self._last_volume_percent_by_endpoint[endpoint_id] = volume_percent
            log.info("Volume command sent to endpoint: endpoint_id=%s volume_percent=%s", endpoint_id, volume_percent)
        return result

    async def push_mute_command(self, *, endpoint_id: str, muted: bool) -> dict:
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.mute",
            command_type="endpoint.mute",
            payload={"muted": muted},
        )

    async def push_micro_vad_command(
        self,
        *,
        endpoint_id: str,
        pause_ms: int | None = None,
        energy_threshold: int | None = None,
    ) -> dict:
        payload: dict[str, int] = {}
        if pause_ms is not None:
            payload["pause_ms"] = pause_ms
        if energy_threshold is not None:
            payload["energy_threshold"] = energy_threshold
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.micro_vad",
            command_type="endpoint.micro_vad.set",
            payload=payload,
        )

    async def push_endpoint_provisioning_apply_command(
        self,
        *,
        endpoint_id: str,
        provisioning: dict[str, object | None],
    ) -> dict:
        payload: dict[str, object] = {}
        field_map = {
            "provisioned_endpoint_id": "endpoint_id",
            "display_name": "display_name",
            "backend_host": "backend_host",
            "http_port": "http_port",
            "ws_port": "ws_port",
            "use_tls": "use_tls",
            "wifi_ssid": "wifi_ssid",
            "wifi_password": "wifi_password",
        }
        for source_key, target_key in field_map.items():
            value = provisioning.get(source_key)
            if value is not None:
                payload[target_key] = value
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.provisioning.apply",
            command_type="endpoint.provisioning.apply",
            payload=payload,
        )

    async def push_endpoint_provisioning_reset_command(self, *, endpoint_id: str) -> dict:
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.provisioning.reset",
            command_type="endpoint.provisioning.reset",
            payload={},
        )

    async def push_cancel_command(self, *, endpoint_id: str, reason: str = "operator_cancelled") -> dict:
        runtime = self._runtime_for_endpoint(endpoint_id)
        result = await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.cancel",
            command_type="endpoint.cancel",
            payload={"reason": reason},
        )
        if result.get("accepted") and runtime is not None:
            token = self._runtime_context.set(runtime)
            try:
                if self._active_session is not None:
                    self._set_session_state("cancelled")
                    self._active_session.cancel_reason = reason
                    self._persist_active_session_history(
                        self._active_session,
                        completion_reason=reason,
                    )
                    self._release_active_session_wake_stream()
                    self._clear_active_session_runtime()
            finally:
                self._runtime_context.reset(token)
        return result

    async def push_listen_command(self, *, endpoint_id: str, reason: str = "operator_requested") -> dict:
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.listen",
            command_type="endpoint.listen",
            payload={"reason": reason},
        )

    async def push_playback_stop_command(self, *, endpoint_id: str, reason: str = "operator_stop") -> dict:
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="playback.stop",
            command_type="playback.stop",
            payload={"reason": reason},
        )

    async def push_replay_command(self, *, endpoint_id: str) -> dict:
        runtime = self._runtime_switch_for_endpoint(endpoint_id)
        if runtime is not None:
            token = self._runtime_context.set(runtime)
            try:
                return await self.push_replay_command(endpoint_id=endpoint_id)
            finally:
                self._runtime_context.reset(token)
        if self._last_transcript and self._turn_pipeline is not None:
            replay_text = f"I heard {self._last_transcript}"
            session_id = self._active_session.session_id if self._active_session else f"{endpoint_id}-replay"
            tts = self._turn_pipeline.synthesize_reply(
                endpoint_id=endpoint_id,
                session_id=session_id,
                text=replay_text,
            )
            self._last_response = replay_text
            self._last_tts = tts_synthesis_metadata(tts)
            if tts.error:
                return {"accepted": False, "reason": tts.error, "status": "failed"}
        if not self._last_tts or not self._last_tts.get("stream_id"):
            replay_session = (
                self._session_history_store.latest_replay_eligible(endpoint_id=endpoint_id)
                if self._session_history_store is not None
                else None
            )
            if replay_session is not None:
                return await self._push_session_replay(session=replay_session, endpoint_id=endpoint_id)
            return {"accepted": False, "reason": "no_replay_available", "status": "failed"}
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.replay",
            command_type="endpoint.replay",
            payload={
                "stream_id": self._last_tts.get("stream_id"),
                "content_type": self._last_tts.get("content_type"),
                "audio_url": endpoint_tts_audio_url(self._last_tts),
            },
        )

    async def push_session_replay_command(self, *, session_id: str, endpoint_id: str | None = None) -> dict:
        if self._session_history_store is None:
            return {"accepted": False, "reason": "session_history_unavailable", "status": "failed"}
        session = self._session_history_store.get_session(session_id)
        if session is None:
            return {"accepted": False, "reason": "session_not_found", "status": "failed"}
        return await self._push_session_replay(session=session, endpoint_id=endpoint_id)

    async def push_speak_command(self, *, endpoint_id: str, text: str, session_id: str | None = None) -> dict:
        runtime = self._runtime_switch_for_endpoint(endpoint_id)
        if runtime is not None:
            token = self._runtime_context.set(runtime)
            try:
                return await self.push_speak_command(endpoint_id=endpoint_id, text=text, session_id=session_id)
            finally:
                self._runtime_context.reset(token)
        if self._turn_pipeline is None:
            return {"accepted": False, "reason": "turn_pipeline_unavailable", "status": "failed"}
        spoken_text = str(text or "").strip()
        if not spoken_text:
            return {"accepted": False, "reason": "speak_text_required", "status": "failed"}
        command_session_id = session_id or f"{endpoint_id}-speak"
        tts = self._turn_pipeline.synthesize_reply(
            endpoint_id=endpoint_id,
            session_id=command_session_id,
            text=spoken_text,
        )
        self._last_response = spoken_text
        self._last_tts = tts_synthesis_metadata(tts)
        if tts.error:
            return {"accepted": False, "reason": tts.error, "status": "failed"}
        if not tts.stream_id:
            return {"accepted": False, "reason": "tts_stream_unavailable", "status": "failed"}
        record_voice_event(
            "endpoint.speak.ready",
            endpoint_id=endpoint_id,
            session_id=command_session_id,
            provider_id=tts.provider_id,
            content_type=tts.content_type,
            stream_id=tts.stream_id,
            audio_url=endpoint_tts_audio_url(tts),
            audio_variant=tts.audio_variant,
            raw_sample_rate_hz=tts.raw_sample_rate_hz,
            output_sample_rate_hz=tts.output_sample_rate_hz,
            spoken_text=spoken_text,
        )
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.replay",
            command_type="endpoint.speak",
            request_id=f"endpoint_speak_{uuid4().hex}",
            payload={
                "stream_id": tts.stream_id,
                "content_type": tts.content_type,
                "audio_url": endpoint_tts_audio_url(tts),
                "text": spoken_text,
            },
        )

    async def push_play_sound_command(
        self,
        *,
        endpoint_id: str,
        audio_url: str | None = None,
        stream_id: str | None = None,
        content_type: str | None = None,
        text: str | None = None,
        voice: str | None = None,
        session_id: str | None = None,
        source_event_id: str | None = None,
        interaction_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        loop: bool = False,
        mic_mode: str = "pause_for_playback",
    ) -> dict:
        runtime = self._runtime_switch_for_endpoint(endpoint_id)
        if runtime is not None:
            token = self._runtime_context.set(runtime)
            try:
                return await self.push_play_sound_command(
                    endpoint_id=endpoint_id,
                    audio_url=audio_url,
                    stream_id=stream_id,
                    content_type=content_type,
                    text=text,
                    voice=voice,
                    session_id=session_id,
                    source_event_id=source_event_id,
                    interaction_id=interaction_id,
                    metadata=metadata,
                    loop=loop,
                    mic_mode=mic_mode,
                )
            finally:
                self._runtime_context.reset(token)
        requested_audio_url = str(audio_url or "").strip()
        spoken_text = str(text or "").strip()
        playback_mic_mode = mic_mode if mic_mode in {"pause_for_playback", "interrupt_only"} else "pause_for_playback"
        command_session_id = session_id or f"{endpoint_id}-play-sound"
        tts: TtsSynthesis | None = None
        if not requested_audio_url and spoken_text:
            if self._turn_pipeline is None:
                return {"accepted": False, "reason": "turn_pipeline_unavailable", "status": "failed"}
            tts = self._turn_pipeline.synthesize_reply(
                endpoint_id=endpoint_id,
                session_id=command_session_id,
                text=spoken_text,
                voice=voice,
            )
            self._last_response = spoken_text
            self._last_tts = tts_synthesis_metadata(tts)
            if tts.error:
                return {"accepted": False, "reason": tts.error, "status": "failed"}
            if not tts.stream_id:
                return {"accepted": False, "reason": "tts_stream_unavailable", "status": "failed"}
            requested_audio_url = endpoint_tts_audio_url(tts) or ""
            stream_id = tts.stream_id
            content_type = tts.content_type
        if not requested_audio_url:
            return {"accepted": False, "reason": "audio_url_or_text_required", "status": "failed"}

        record_voice_event(
            "endpoint.play_sound.ready",
            endpoint_id=endpoint_id,
            session_id=command_session_id,
            command="ui.play_sound",
            stream_id=stream_id or (tts.stream_id if tts else None),
            content_type=content_type or (tts.content_type if tts else "audio/wav"),
            audio_url=requested_audio_url,
            spoken_text=spoken_text or None,
            source_event_id=source_event_id,
            interaction_id=interaction_id,
            loop=loop,
            mic_mode=playback_mic_mode,
            metadata=metadata or {},
        )
        payload: dict[str, Any] = {
            "stream_id": stream_id or (tts.stream_id if tts else None),
            "content_type": content_type or (tts.content_type if tts else "audio/wav"),
            "audio_url": requested_audio_url,
            "text": spoken_text or None,
            "source_event_id": source_event_id,
            "interaction_id": interaction_id,
            "command": "ui.play_sound",
            "metadata": metadata or {},
            "mic_mode": playback_mic_mode,
        }
        if loop:
            payload["loop"] = True
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.replay",
            command_type="endpoint.play_sound",
            request_id=f"endpoint_play_sound_{uuid4().hex}",
            session_id=command_session_id,
            payload=payload,
        )

    async def push_timer_announcement(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        text: str,
        source_event_id: str | None = None,
    ) -> dict:
        runtime = self._runtime_switch_for_endpoint(endpoint_id)
        if runtime is not None:
            token = self._runtime_context.set(runtime)
            try:
                return await self.push_timer_announcement(
                    endpoint_id=endpoint_id,
                    session_id=session_id,
                    text=text,
                    source_event_id=source_event_id,
                )
            finally:
                self._runtime_context.reset(token)
        if self._turn_pipeline is None:
            return {"accepted": False, "reason": "turn_pipeline_unavailable", "status": "failed"}
        announcement_text = str(text or "").strip()
        if not announcement_text:
            return {"accepted": False, "reason": "announcement_text_required", "status": "failed"}
        tts = self._turn_pipeline.synthesize_reply(
            endpoint_id=endpoint_id,
            session_id=session_id,
            text=announcement_text,
        )
        self._last_response = announcement_text
        self._last_tts = tts_synthesis_metadata(tts)
        if tts.error:
            return {"accepted": False, "reason": tts.error, "status": "failed"}
        if not tts.stream_id:
            return {"accepted": False, "reason": "tts_stream_unavailable", "status": "failed"}
        record_voice_event(
            "timer.announcement.ready",
            endpoint_id=endpoint_id,
            session_id=session_id,
            provider_id=tts.provider_id,
            content_type=tts.content_type,
            stream_id=tts.stream_id,
            audio_url=endpoint_tts_audio_url(tts),
            audio_variant=tts.audio_variant,
            raw_sample_rate_hz=tts.raw_sample_rate_hz,
            output_sample_rate_hz=tts.output_sample_rate_hz,
            spoken_text=announcement_text,
            source_event_id=source_event_id,
        )
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.replay",
            command_type="endpoint.announcement.timer",
            request_id=f"timer_announcement_{uuid4().hex}",
            session_id=session_id,
            payload={
                "stream_id": tts.stream_id,
                "content_type": tts.content_type,
                "audio_url": endpoint_tts_audio_url(tts),
                "announcement_type": "timer.create_succeeded",
                "text": announcement_text,
                "source_event_id": source_event_id,
            },
        )

    async def push_media_transfer(
        self,
        *,
        endpoint_id: str,
        request_id: str,
        media_type: str,
        asset_id: str,
        filename: str,
        destination: str,
        download_url: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        overwrite: bool,
        activate: bool,
        metadata: dict,
    ) -> dict:
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.media.transfer",
            command_type="endpoint.media.transfer",
            request_id=request_id,
            payload={
                "media_type": media_type,
                "asset_id": asset_id,
                "filename": filename,
                "destination": destination,
                "download_url": download_url,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "overwrite": overwrite,
                "rewrite": overwrite,
                "activate": activate,
                "metadata": metadata,
            },
        )

    async def push_storage_reformat_command(self, *, endpoint_id: str) -> dict:
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.storage.reformat",
            command_type="endpoint.storage.reformat",
            payload={},
        )

    async def push_led_simulation_command(self, *, endpoint_id: str, pattern: str, duration_ms: int) -> dict:
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.led.simulate",
            command_type="endpoint.led.simulate",
            payload={"pattern": pattern, "duration_ms": duration_ms},
        )

    def volume_status(self, endpoint_id: str) -> dict:
        self._expire_commands()
        latest = self._latest_command(endpoint_id=endpoint_id, command_type="endpoint.volume.set")
        return {
            "volume_percent": self._last_volume_percent_by_endpoint.get(endpoint_id),
            "latest_command": latest,
        }

    async def _push_endpoint_command(
        self,
        *,
        endpoint_id: str,
        event_type: VoiceEventType,
        command_type: str,
        payload: dict[str, object],
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        runtime = self._runtime_for_endpoint(endpoint_id)
        if runtime is None or runtime.websocket is None:
            log.warning("Endpoint command rejected: endpoint_id=%s command_type=%s reason=endpoint_not_connected", endpoint_id, command_type)
            return {"accepted": False, "reason": "endpoint_not_connected", "status": "failed"}
        token = self._runtime_context.set(runtime)
        try:
            request_id = request_id or f"cmd_{uuid4().hex}"
            event = VoiceEventEnvelope(
                event_type=event_type,
                endpoint_id=endpoint_id,
                direction="backend_to_endpoint",
                session_id=session_id or (self._active_session.session_id if self._active_session else None),
                sequence=self._next_sequence(),
                payload={"request_id": request_id, **payload},
            )
            await runtime.websocket.send_json(event.model_dump(mode="json"))
            self._last_event_type = event_type
            record = self._record_command(
                request_id=request_id,
                endpoint_id=endpoint_id,
                command_type=command_type,
                event_type=event_type,
            )
            return {"accepted": True, "request_id": request_id, "status": record["status"]}
        finally:
            self._runtime_context.reset(token)

    def _handle_raw_message(self, raw_message: str) -> list[VoiceEventEnvelope]:
        try:
            raw_payload = json.loads(raw_message)
        except json.JSONDecodeError:
            log.warning("Invalid voice WebSocket JSON received bytes=%s", len(raw_message))
            self._record_event_diagnostic(
                code="invalid_json",
                endpoint_id="unknown",
                session_id=None,
                event_type=None,
                message="Voice WebSocket messages must be valid JSON envelopes.",
            )
            return [
                self._error_event(
                    endpoint_id="unknown",
                    session_id=None,
                    code="invalid_json",
                    message="Voice WebSocket messages must be valid JSON envelopes.",
                    recoverable=True,
                )
            ]

        try:
            event = VoiceEventEnvelope.model_validate(raw_payload)
        except ValidationError as exc:
            first_error = exc.errors()[0]
            location = ".".join(str(part) for part in first_error.get("loc", []))
            message = str(first_error["msg"])
            if location:
                message = f"{location}: {message}"
            log.warning("Invalid voice event envelope: error=%s", message)
            self._record_event_diagnostic(
                code="invalid_event_envelope",
                endpoint_id=self._safe_endpoint_id(raw_payload),
                session_id=self._safe_session_id(raw_payload),
                event_type=self._safe_event_type(raw_payload),
                message=message,
            )
            return [
                self._error_event(
                    endpoint_id=self._safe_endpoint_id(raw_payload),
                    session_id=self._safe_session_id(raw_payload),
                    code="invalid_event_envelope",
                    message=message,
                    recoverable=True,
                )
            ]

        if event.direction != "endpoint_to_backend":
            log.warning(
                "Rejected voice event with invalid direction: endpoint_id=%s session_id=%s direction=%s",
                event.endpoint_id,
                event.session_id,
                event.direction,
            )
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="invalid_direction",
                    message="Endpoint WebSocket messages must use endpoint_to_backend direction.",
                    recoverable=True,
                )
            ]

        if event.event_type not in ENDPOINT_TO_BACKEND_EVENTS:
            log.warning(
                "Rejected unsupported endpoint voice event: endpoint_id=%s session_id=%s event_type=%s",
                event.endpoint_id,
                event.session_id,
                event.event_type,
            )
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="unsupported_endpoint_event",
                    message=f"{event.event_type} is not accepted from endpoints.",
                    recoverable=True,
                )
            ]

        existing_runtime = self._endpoint_runtimes.get(event.endpoint_id)
        current_runtime = self._current_runtime()
        if (
            existing_runtime is not None
            and existing_runtime is not current_runtime
            and existing_runtime.connection_active
            and existing_runtime.websocket is not None
        ):
            log.error(
                "Voice endpoint conflict: endpoint_id=%s already has an active WebSocket event_type=%s",
                event.endpoint_id,
                event.event_type,
            )
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="endpoint_already_connected",
                    message="This endpoint already has an active WebSocket.",
                    recoverable=False,
                )
            ]

        if self._connected_endpoint_id is not None and event.endpoint_id != self._connected_endpoint_id:
            log.error(
                "Voice endpoint conflict: connected_endpoint_id=%s incoming_endpoint_id=%s event_type=%s",
                self._connected_endpoint_id,
                event.endpoint_id,
                event.event_type,
            )
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="endpoint_conflict",
                    message="This WebSocket is already bound to another endpoint.",
                    recoverable=False,
                )
            ]

        if self._connected_endpoint_id is None:
            self._connected_endpoint_id = event.endpoint_id
            log.info("Voice endpoint bound to WebSocket: endpoint_id=%s", event.endpoint_id)

        if self._can_merge_placement_test_event(event):
            log.debug(
                "Merging placement-test event into active capture: endpoint_id=%s active_session_id=%s incoming_session_id=%s event_type=%s",
                event.endpoint_id,
                self._active_session.session_id if self._active_session else None,
                event.session_id,
                event.event_type,
            )
            event = event.model_copy(update={"session_id": self._active_session.session_id})

        if self._can_merge_speaker_enrollment_event(event):
            log.debug(
                "Merging Speaker ID enrollment event into active capture: endpoint_id=%s active_session_id=%s incoming_session_id=%s event_type=%s",
                event.endpoint_id,
                self._active_session.session_id if self._active_session else None,
                event.session_id,
                event.event_type,
            )
            event = event.model_copy(update={"session_id": self._active_session.session_id})

        handlers = {
            "session.start": self._handle_session_start,
            "audio.chunk": self._handle_audio_chunk,
            "audio.end": self._handle_audio_end,
            "vad.speech_started": self._handle_vad_speech_started,
            "wake.candidate": self._handle_wake_candidate,
            "session.cancel": self._handle_session_cancel,
            "session.ping": self._handle_session_ping,
            "command.ack": self._handle_command_ack,
            "command.error": self._handle_command_error,
            "tts.playback.download_started": self._handle_tts_playback_event,
            "tts.playback.first_audio_frame": self._handle_tts_playback_event,
            "tts.playback.completed": self._handle_tts_playback_event,
            "tts.playback.failed": self._handle_tts_playback_event,
            "playback.stop": self._handle_tts_playback_event,
        }
        return handlers[event.event_type](event)

    def _handle_wake_candidate(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]
        if session.session_state != "idle":
            try:
                payload = VoiceWakeCandidatePayload.model_validate(event.payload)
            except ValidationError as exc:
                return [
                    self._error_event(
                        endpoint_id=event.endpoint_id,
                        session_id=event.session_id,
                        code="invalid_wake_candidate",
                        message=str(exc.errors()[0]["msg"]),
                        recoverable=True,
                    )
                ]
            candidate = self._wake_candidate_from_payload(
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                payload=payload,
                received_at=event.timestamp,
            )
            decision = WakeElectionDecision(
                election_id=f"wake_election_{uuid4().hex}",
                accepted=False,
                reason="wake_already_detected",
                window_ms=self._wake_election.window_ms,
                candidate=candidate,
                winner=None,
                candidates=(candidate,),
                decided_at=event.timestamp,
            )
            return [self._wake_election_event(session=session, decision=decision)]

        try:
            payload = VoiceWakeCandidatePayload.model_validate(event.payload)
        except ValidationError as exc:
            log.warning(
                "Invalid wake.candidate payload: endpoint_id=%s session_id=%s error=%s",
                event.endpoint_id,
                event.session_id,
                str(exc.errors()[0]["msg"]),
            )
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="invalid_wake_candidate",
                    message=str(exc.errors()[0]["msg"]),
                    recoverable=True,
                )
            ]

        candidate = self._wake_candidate_from_payload(
            endpoint_id=event.endpoint_id,
            session_id=session.session_id,
            payload=payload,
            received_at=event.timestamp,
        )
        decision = self._wake_election.submit_candidate(candidate)
        self._record_wake_confidence(
            endpoint_id=event.endpoint_id,
            session_id=session.session_id,
            model=candidate.model,
            confidence=candidate.confidence,
            detected=True,
            accepted=decision.accepted,
            reason=decision.reason,
            source=candidate.source,
            chunk_index=candidate.chunk_index,
            chunk_count=candidate.chunk_count,
        )
        if not decision.accepted:
            return [self._wake_election_event(session=session, decision=decision)]
        events = self._accept_wake_candidate(
            session=session,
            candidate=candidate,
            detected_at=event.timestamp,
            decision=decision,
        )
        events.append(
            self._state_event(
                "session.state",
                session,
                extra_payload={
                    "wake": {
                        "detected": True,
                        "confidence": candidate.confidence,
                        "model": candidate.model,
                        "source": candidate.source,
                        "reason": decision.reason,
                        "election": decision.model_dump(),
                    }
                },
            )
        )
        return events

    def _wake_candidate_from_payload(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        payload: VoiceWakeCandidatePayload,
        received_at: datetime,
    ) -> WakeCandidate:
        metadata = dict(payload.metadata or {})
        if payload.detection_window_ms is not None:
            metadata["detection_window_ms"] = payload.detection_window_ms
        return WakeCandidate(
            endpoint_id=endpoint_id,
            session_id=session_id,
            source=payload.source,
            model=payload.model,
            confidence=payload.confidence,
            received_at=received_at,
            detected_at=payload.detected_at,
            chunk_index=payload.chunk_index,
            chunk_count=payload.chunk_count,
            frame_level=payload.frame_level,
            speech_peak_level=payload.speech_peak_level,
            noise_floor_level=payload.noise_floor_level,
            ambient_level=payload.ambient_level,
            snr_db=payload.snr_db,
            endpoint_audio_profile_version=payload.endpoint_audio_profile_version,
            metadata=metadata,
        )

    def _wake_election_event(
        self,
        *,
        session: VoiceSessionSnapshot,
        decision: WakeElectionDecision,
    ) -> VoiceEventEnvelope:
        return self._state_event(
            "wake.election.result",
            session,
            extra_payload={
                "election": decision.model_dump(),
                "stand_down": not decision.accepted,
                "winner_endpoint_id": decision.winner.endpoint_id if decision.winner else None,
                "winner_session_id": decision.winner.session_id if decision.winner else None,
                "reason": decision.reason,
            },
        )

    def _accept_wake_candidate(
        self,
        *,
        session: VoiceSessionSnapshot,
        candidate: WakeCandidate,
        detected_at: datetime,
        decision: WakeElectionDecision | None = None,
    ) -> list[VoiceEventEnvelope]:
        if self._wake_recorder is not None:
            self._wake_recorder.mark_accepted_wake(
                endpoint_id=session.endpoint_id,
                session_id=session.session_id,
                model=candidate.model,
                confidence=candidate.confidence,
                source=candidate.source,
                chunk_index=candidate.chunk_index,
                chunk_count=candidate.chunk_count,
            )
        if self._micro_vad_chunk_recorder is not None:
            self._micro_vad_chunk_recorder.mark_session_accepted(
                endpoint_id=session.endpoint_id,
                session_id=session.session_id,
            )
        wake_payload = {
            "outcome": "accepted",
            "detected": True,
            "endpoint_id": session.endpoint_id,
            "session_id": session.session_id,
            "model": candidate.model,
            "confidence": candidate.confidence,
            "detected_at": detected_at.isoformat(),
            "source": candidate.source,
            "chunk_index": candidate.chunk_index,
            "chunk_count": candidate.chunk_count,
        }
        if decision is not None:
            wake_payload["election_id"] = decision.election_id
            wake_payload["election"] = decision.model_dump()
        self._record_wake_history(wake_payload)
        self._set_active_session_wake(wake_payload)
        self._append_latency_point("wake_word_detected", "Wake word detected", detected_at)
        log.info(
            "Wake accepted: endpoint_id=%s session_id=%s source=%s model=%s confidence=%s chunk_index=%s",
            session.endpoint_id,
            session.session_id,
            candidate.source,
            candidate.model,
            candidate.confidence,
            candidate.chunk_index,
        )
        record_voice_event(
            "wake.accepted",
            endpoint_id=session.endpoint_id,
            session_id=session.session_id,
            model=candidate.model,
            confidence=candidate.confidence,
            source=candidate.source,
            chunk_index=candidate.chunk_index,
            chunk_count=candidate.chunk_count,
            election_id=decision.election_id if decision else None,
        )
        self._set_session_state("wake_detected")
        wake_event = self._state_event(
            "wake.accepted",
            session,
            extra_payload={
                "wake": {
                    "confidence": candidate.confidence,
                    "model": candidate.model,
                    "source": candidate.source,
                },
                **({"election": decision.model_dump()} if decision is not None else {}),
            },
        )
        self._set_session_state("listening")
        return [wake_event]

    def _handle_session_start(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        if self._active_session is not None:
            if self._should_complete_placement_test_on_new_session(event):
                log.info(
                    "Completing placement test before new session: endpoint_id=%s active_session_id=%s incoming_session_id=%s",
                    event.endpoint_id,
                    self._active_session.session_id,
                    event.session_id,
                )
                completed_events = self._handle_audio_end(
                    event.model_copy(
                        update={
                            "event_type": "audio.end",
                            "session_id": self._active_session.session_id,
                            "payload": {"reason": "superseded_by_new_placement_session"},
                        }
                    )
                )
                if self._active_session is None:
                    return completed_events + self._handle_session_start(event)
                return completed_events
            if self._should_complete_speaker_enrollment_on_new_session(event):
                log.info(
                    "Completing Speaker ID enrollment capture before new session: endpoint_id=%s active_session_id=%s incoming_session_id=%s",
                    event.endpoint_id,
                    self._active_session.session_id,
                    event.session_id,
                )
                completed_events = self._handle_audio_end(
                    event.model_copy(
                        update={
                            "event_type": "audio.end",
                            "session_id": self._active_session.session_id,
                            "payload": {"reason": "superseded_by_new_enrollment_session"},
                        }
                    )
                )
                if self._active_session is None:
                    return completed_events + self._handle_session_start(event)
                return completed_events
            if self._can_merge_playback_interrupt_event(event):
                log.debug(
                    "Reusing active playback interrupt session: endpoint_id=%s active_session_id=%s incoming_session_id=%s",
                    event.endpoint_id,
                    self._active_session.session_id,
                    event.session_id,
                )
                return [self._state_event("session.state", self._active_session)]
            if self._can_replace_active_session(event):
                log.info(
                    "Replacing stale pre-audio voice session: endpoint_id=%s stale_session_id=%s incoming_session_id=%s stale_state=%s",
                    event.endpoint_id,
                    self._active_session.session_id,
                    event.session_id,
                    self._active_session.session_state,
                )
                self._set_session_state("cancelled")
                self._active_session.cancel_reason = "superseded_by_new_session"
                self._persist_active_session_history(
                    self._active_session,
                    completion_reason="superseded_by_new_session",
                )
                self._release_active_session_wake_stream()
                self._clear_active_session_runtime()
            else:
                log.warning(
                    "Rejected session start because active session exists: endpoint_id=%s active_session_id=%s incoming_session_id=%s",
                    event.endpoint_id,
                    self._active_session.session_id,
                    event.session_id,
                )
                return [
                    self._error_event(
                        endpoint_id=event.endpoint_id,
                        session_id=event.session_id,
                        code="active_session_exists",
                        message="Only one active voice session is supported for the MVP.",
                        recoverable=True,
                    )
                ]

        try:
            payload = VoiceSessionStartPayload.model_validate(event.payload)
        except ValidationError as exc:
            log.warning(
                "Invalid session.start payload: endpoint_id=%s session_id=%s error=%s",
                event.endpoint_id,
                event.session_id,
                str(exc.errors()[0]["msg"]),
            )
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="invalid_session_start",
                    message=str(exc.errors()[0]["msg"]),
                    recoverable=True,
                )
            ]

        session_id = event.session_id or f"voice-session-{uuid4().hex[:12]}"
        self._active_session = VoiceSessionSnapshot(
            session_id=session_id,
            endpoint_id=event.endpoint_id,
            session_state="idle",
            connection_state="connected",
            ux_state="wake_armed",
            started_at=event.timestamp,
            last_updated_at=event.timestamp,
            wake_source=payload.wake_source,
        )
        self._chunk_count = 0
        self._audio_chunks = []
        self._ambient_audio_chunks = []
        self._audio_format = payload.audio_format
        self._begin_active_session_history(
            session=self._active_session,
            start_payload=payload,
        )
        placement_window = self._active_placement_test_window(event.endpoint_id)
        if placement_window is not None:
            self._update_active_session_history(
                placement_test={
                    **placement_window,
                    "session_id": session_id,
                    "status": "capturing",
                }
            )
        log.info(
            "Voice session started: endpoint_id=%s session_id=%s wake_source=%s sample_rate_hz=%s",
            event.endpoint_id,
            session_id,
            payload.wake_source,
            payload.audio_format.sample_rate_hz,
        )
        if payload.wake_source in {"button", "manual"}:
            self._set_session_state("wake_detected")
            self._record_wake_history(
                {
                    "outcome": "accepted",
                    "detected": True,
                    "endpoint_id": event.endpoint_id,
                    "session_id": session_id,
                    "model": payload.wake_source,
                    "confidence": 1.0,
                    "source": payload.wake_source,
                    "chunk_count": 0,
                }
            )
            self._set_active_session_wake(
                {
                    "outcome": "accepted",
                    "detected": True,
                    "model": payload.wake_source,
                    "confidence": 1.0,
                    "detected_at": event.timestamp.isoformat(),
                    "source": payload.wake_source,
                    "chunk_count": 0,
                }
            )
            self._append_latency_point("wake_word_detected", "Wake word detected", event.timestamp)
            self._record_wake_confidence(
                endpoint_id=event.endpoint_id,
                session_id=session_id,
                model=payload.wake_source,
                confidence=1.0,
                detected=True,
                accepted=True,
                source=payload.wake_source,
                chunk_count=0,
            )
            record_voice_event(
                "wake.accepted",
                endpoint_id=event.endpoint_id,
                session_id=session_id,
                model=payload.wake_source,
                confidence=1.0,
                source=payload.wake_source,
                chunk_count=0,
            )
            wake_event = self._state_event(
                "wake.accepted",
                self._active_session,
                extra_payload={
                    "wake": {
                        "confidence": 1.0,
                        "model": payload.wake_source,
                        "source": payload.wake_source,
                    }
                },
            )
            self._set_session_state("listening")
            return [wake_event, self._state_event("session.state", self._active_session)]
        return [self._state_event("session.state", self._active_session)]

    def _can_replace_active_session(self, event: VoiceEventEnvelope) -> bool:
        if self._active_session is None or self._active_session.endpoint_id != event.endpoint_id:
            return False
        active_age_ms = (event.timestamp - self._active_session.started_at).total_seconds() * 1000
        if self._chunk_count == 0 and active_age_ms < PRE_AUDIO_SESSION_REPLACEMENT_GRACE_MS:
            return False
        if self._active_session.session_state == "idle":
            wake = self._active_session_history.get("wake") if self._active_session_history else None
            return not (isinstance(wake, dict) and wake.get("outcome") == "accepted")
        if self._active_session.session_state in {"wake_detected", "listening"}:
            return self._chunk_count == 0
        return False

    def _can_merge_playback_interrupt_event(self, event: VoiceEventEnvelope) -> bool:
        return (
            self._active_session is not None
            and self._active_session.endpoint_id == event.endpoint_id
            and self._active_session.session_state == "idle"
            and self._active_playback_interrupt(event.endpoint_id) is not None
            and event.event_type in {"session.start", "vad.speech_started", "audio.chunk", "audio.end"}
        )

    def _active_session_is_speaker_enrollment_capture(self) -> bool:
        if self._active_session is None or self._active_session_history is None:
            return False
        wake = self._active_session_history.get("wake")
        return isinstance(wake, dict) and wake.get("source") == "speaker_id_enrollment_capture"

    def _active_session_is_placement_test(self) -> bool:
        if self._active_session is None or self._active_session_history is None:
            return False
        placement = self._active_session_history.get("placement_test")
        return isinstance(placement, dict) and placement.get("test_id")

    def _can_merge_placement_test_event(self, event: VoiceEventEnvelope) -> bool:
        return (
            self._active_session is not None
            and self._active_session.endpoint_id == event.endpoint_id
            and event.session_id != self._active_session.session_id
            and self._active_session_is_placement_test()
            and self._active_placement_test_window(event.endpoint_id) is not None
            and event.event_type in {"vad.speech_started", "audio.chunk", "audio.end", "session.cancel"}
        )

    def _can_merge_speaker_enrollment_event(self, event: VoiceEventEnvelope) -> bool:
        return (
            self._active_session is not None
            and self._active_session.endpoint_id == event.endpoint_id
            and event.session_id != self._active_session.session_id
            and self._active_session_is_speaker_enrollment_capture()
            and self._active_speaker_enrollment_capture_window(event.endpoint_id) is not None
            and event.event_type in {"vad.speech_started", "audio.chunk", "audio.end", "session.cancel"}
        )

    def _should_complete_placement_test_on_new_session(self, event: VoiceEventEnvelope) -> bool:
        return (
            self._active_session is not None
            and self._active_session.endpoint_id == event.endpoint_id
            and event.session_id != self._active_session.session_id
            and event.event_type == "session.start"
            and self._active_session.session_state in {"listening", "capturing"}
            and self._active_session_is_placement_test()
            and self._active_placement_test_window(event.endpoint_id) is not None
            and self._chunk_count > 0
        )

    def _should_complete_speaker_enrollment_on_new_session(self, event: VoiceEventEnvelope) -> bool:
        return (
            self._active_session is not None
            and self._active_session.endpoint_id == event.endpoint_id
            and event.session_id != self._active_session.session_id
            and event.event_type == "session.start"
            and self._active_session.session_state in {"listening", "capturing"}
            and self._active_session_is_speaker_enrollment_capture()
            and self._active_speaker_enrollment_capture_window(event.endpoint_id) is not None
            and self._chunk_count > 0
        )

    def _handle_audio_chunk(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]
        timeout_reason = self._active_session_timeout_reason(now=event.timestamp)
        if timeout_reason is not None:
            cancelled = self._cancel_timed_out_active_session(reason=timeout_reason)
            return [cancelled] if cancelled is not None else []

        try:
            payload = VoiceAudioChunkPayload.model_validate(event.payload)
        except ValidationError as exc:
            log.warning(
                "Invalid audio.chunk payload: endpoint_id=%s session_id=%s error=%s",
                event.endpoint_id,
                event.session_id,
                str(exc.errors()[0]["msg"]),
            )
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="invalid_audio_chunk",
                    message=str(exc.errors()[0]["msg"]),
                    recoverable=True,
                )
            ]

        self._chunk_count += 1
        self._audio_format = payload.audio_format
        self._update_active_session_audio(payload)
        log.debug(
            "Voice audio chunk: endpoint_id=%s session_id=%s chunk_index=%s chunk_count=%s has_payload=%s",
            event.endpoint_id,
            session.session_id,
            payload.chunk_index,
            self._chunk_count,
            bool(payload.payload_base64),
        )
        audio_bytes: bytes | None = None
        if payload.payload_base64:
            try:
                audio_bytes = base64.b64decode(payload.payload_base64, validate=True)
            except ValueError:
                pass
        events: list[VoiceEventEnvelope] = []
        placement_test_window = self._active_placement_test_window(event.endpoint_id)
        placement_debug_audio = bool(placement_test_window and placement_test_window.get("debug_record_audio"))
        if (
            audio_bytes is not None
            and self._micro_vad_chunk_recorder is not None
            and not (placement_test_window is not None and not placement_debug_audio)
        ):
            self._micro_vad_chunk_recorder.capture_audio_chunk(
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                payload=payload,
                audio_bytes=audio_bytes,
                received_at=event.timestamp,
            )
        enrollment_capture_window = self._active_speaker_enrollment_capture_window(event.endpoint_id)
        placement_capture_candidate = (
            audio_bytes is not None
            and placement_test_window is not None
            and session.session_state in {"idle", "wake_detected", "listening", "capturing"}
        )
        enrollment_capture_candidate = (
            audio_bytes is not None
            and enrollment_capture_window is not None
            and session.session_state in {"idle", "wake_detected", "listening", "capturing"}
        )
        if (
            audio_bytes is not None
            and self._wake_recorder is not None
            and (session.session_state == "idle" or enrollment_capture_candidate)
            and not (placement_capture_candidate and not placement_debug_audio)
        ):
            self._wake_recorder.capture_wake_chunk(
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                audio_format=payload.audio_format,
                audio_bytes=audio_bytes,
            )
        enrollment_capture_accepted = (
            enrollment_capture_candidate
            and not self._active_session_is_speaker_enrollment_capture()
        )
        placement_capture_accepted = placement_capture_candidate and not self._active_session_is_placement_test()
        if placement_capture_accepted and placement_test_window is not None:
            self._set_active_session_wake(
                {
                    "outcome": "accepted",
                    "detected": True,
                    "model": "placement_test_capture",
                    "confidence": 1.0,
                    "detected_at": event.timestamp.isoformat(),
                    "source": "placement_test_capture",
                    "chunk_index": payload.chunk_index,
                    "chunk_count": self._chunk_count,
                }
            )
            self._update_active_session_history(
                placement_test={
                    **placement_test_window,
                    "session_id": session.session_id,
                    "status": "capturing",
                    "first_audio_chunk_index": payload.chunk_index,
                }
            )
            self._append_latency_point("wake_word_detected", "Placement test capture accepted", event.timestamp)
            record_voice_event(
                "placement_test.capture.started",
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                test_id=placement_test_window.get("test_id"),
                room=placement_test_window.get("room"),
                zone=placement_test_window.get("zone"),
                position_label=placement_test_window.get("position_label"),
                chunk_index=payload.chunk_index,
                chunk_count=self._chunk_count,
            )
            self._set_session_state("listening")
        if enrollment_capture_accepted:
            if self._wake_recorder is not None:
                self._wake_recorder.mark_accepted_wake(
                    endpoint_id=event.endpoint_id,
                    session_id=session.session_id,
                    model="speaker_enrollment_capture",
                    confidence=1.0,
                    source="speaker_id_enrollment_capture",
                    chunk_index=payload.chunk_index,
                    chunk_count=self._chunk_count,
                )
            if self._micro_vad_chunk_recorder is not None:
                self._micro_vad_chunk_recorder.mark_session_accepted(
                    endpoint_id=event.endpoint_id,
                    session_id=session.session_id,
                )
            self._set_active_session_wake(
                {
                    "outcome": "accepted",
                    "detected": True,
                    "model": "speaker_enrollment_capture",
                    "confidence": 1.0,
                    "detected_at": event.timestamp.isoformat(),
                    "source": "speaker_id_enrollment_capture",
                    "chunk_index": payload.chunk_index,
                    "chunk_count": self._chunk_count,
                }
            )
            self._append_latency_point("wake_word_detected", "Enrollment capture accepted", event.timestamp)
            log.info(
                "Speaker enrollment capture accepted without wake: endpoint_id=%s session_id=%s chunk_index=%s",
                event.endpoint_id,
                session.session_id,
                payload.chunk_index,
            )
            record_voice_event(
                "speaker_id.enrollment_capture.started",
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                chunk_index=payload.chunk_index,
                chunk_count=self._chunk_count,
            )
            self._set_session_state("listening")
        if (
            audio_bytes is not None
            and session.session_state == "idle"
            and self._active_playback_interrupt(event.endpoint_id) is not None
        ):
            self._audio_chunks.append(audio_bytes)
        detection = (
            self._wake_detector.inspect_chunk(
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                chunk=payload,
            )
            if session.session_state == "idle"
            else WakeDetectionResult(detected=False, reason="wake_already_detected")
        )
        backend_candidate: WakeCandidate | None = None
        backend_decision: WakeElectionDecision | None = None
        if detection.detected and session.session_state == "idle":
            backend_candidate = WakeCandidate(
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                source="backend_openwakeword",
                model=detection.model,
                confidence=detection.confidence,
                received_at=event.timestamp,
                detected_at=event.timestamp,
                chunk_index=payload.chunk_index,
                chunk_count=self._chunk_count,
                frame_level=payload.frame_level,
                speech_peak_level=payload.speech_peak_level,
                noise_floor_level=payload.noise_floor_level,
            )
            backend_decision = self._wake_election.submit_candidate(backend_candidate)
        wake_accepted = bool(backend_decision and backend_decision.accepted and session.session_state == "idle")
        if audio_bytes is not None and not wake_accepted and self._is_ambient_reference_chunk(session):
            self._ambient_audio_chunks.append(audio_bytes)
        self._record_wake_confidence(
            endpoint_id=event.endpoint_id,
            session_id=session.session_id,
            model=detection.model,
            confidence=detection.confidence,
            detected=detection.detected,
            accepted=wake_accepted,
            reason=backend_decision.reason if backend_decision else detection.reason,
            source="backend_openwakeword",
            chunk_index=payload.chunk_index,
            chunk_count=self._chunk_count,
        )
        if wake_accepted and backend_candidate is not None:
            events.extend(
                self._accept_wake_candidate(
                    session=session,
                    candidate=backend_candidate,
                    detected_at=event.timestamp,
                    decision=backend_decision,
                )
            )

        if session.session_state in {"listening", "capturing"}:
            self._set_session_state("capturing")
            if audio_bytes is not None and not wake_accepted and not enrollment_capture_accepted:
                self._audio_chunks.append(audio_bytes)

        events.append(
            self._state_event(
                "session.state",
                session,
                extra_payload={
                    "chunk_index": payload.chunk_index,
                    "chunk_count": self._chunk_count,
                    "audio_format": payload.audio_format.model_dump(mode="json"),
                    "wake": {
                        "detected": detection.detected,
                        "confidence": detection.confidence,
                        "model": detection.model,
                        "reason": backend_decision.reason if backend_decision else detection.reason,
                        "source": "backend_openwakeword",
                        "election": backend_decision.model_dump() if backend_decision else None,
                    },
                },
            )
        )
        return events

    def _handle_audio_end(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]

        if session.session_state == "idle":
            playback_stop = self._playback_interrupt_stop_event(session)
            completion_reason = "playback_interrupt_stop" if playback_stop is not None else "wake_not_detected"
            self._set_session_state("cancelled")
            session.cancel_reason = completion_reason
            wake_status = self._wake_detector.status().get("last_detection") or {}
            self._record_wake_history(
                {
                    "outcome": "not_detected",
                    "detected": False,
                    "endpoint_id": session.endpoint_id,
                    "session_id": session.session_id,
                    "model": wake_status.get("model"),
                    "confidence": wake_status.get("confidence"),
                    "reason": wake_status.get("reason") or completion_reason,
                    "chunk_count": self._chunk_count,
                }
            )
            self._set_active_session_wake(
                {
                    "outcome": "not_detected",
                    "detected": False,
                    "model": wake_status.get("model"),
                    "confidence": wake_status.get("confidence"),
                    "reason": wake_status.get("reason") or completion_reason,
                    "chunk_count": self._chunk_count,
                }
            )
            log.info(
                "Voice session cancelled before wake: endpoint_id=%s session_id=%s chunks=%s",
                session.endpoint_id,
                session.session_id,
                self._chunk_count,
            )
            record_voice_event(
                "wake.not_detected",
                endpoint_id=session.endpoint_id,
                session_id=session.session_id,
                model=wake_status.get("model"),
                confidence=wake_status.get("confidence"),
                reason=wake_status.get("reason") or completion_reason,
                chunk_count=self._chunk_count,
            )
            cancelled = self._state_event("session.cancelled", session)
            self._persist_active_session_history(
                session,
                completion_reason=completion_reason,
            )
            self._release_active_session_wake_stream()
            self._clear_active_session_runtime()
            return ([playback_stop] if playback_stop is not None else []) + [cancelled]

        if session.session_state == "wake_detected":
            self._set_session_state("listening")

        audio_end_reason = event.payload.get("reason") if isinstance(event.payload, dict) else None
        if audio_end_reason == "vad_silence":
            self._set_active_session_vad(
                {
                    "speech_ended_at": event.timestamp.isoformat(),
                    "speech_end_reason": "vad_silence",
                }
            )
            self._append_latency_point("vad_silence", "VAD silence", event.timestamp)
        self._update_vad_latency("audio_end", event.timestamp)
        self._set_session_state("transcribing")
        events: list[VoiceEventEnvelope] = []
        placement_test_window = self._active_placement_test_window(session.endpoint_id)
        if self._active_session_is_placement_test() and self._turn_pipeline is not None:
            return self._complete_active_placement_test(session=session, event=event, placement_window=placement_test_window)
        wake_recording = self._record_accepted_wake_session(session)
        enrollment_capture_window = self._active_speaker_enrollment_capture_window(session.endpoint_id)
        if enrollment_capture_window is not None and self._turn_pipeline is not None:
            audio_summary = VoiceTurnAudioSummary(
                endpoint_id=session.endpoint_id,
                session_id=session.session_id,
                chunk_count=self._chunk_count,
                sample_rate_hz=self._audio_format.sample_rate_hz if self._audio_format else None,
                encoding=self._audio_format.encoding if self._audio_format else None,
                channels=self._audio_format.channels if self._audio_format else 1,
                audio_bytes=b"".join(self._audio_chunks),
                ambient_audio_bytes=b"".join(self._ambient_audio_chunks) or None,
                endpoint_audio_metrics=self._endpoint_audio_metrics(),
            )
            stt_started_at = datetime.now(UTC)
            self._append_latency_point("stt_start", "STT start", stt_started_at)
            turn_started_at = datetime.now(UTC)
            transcript = self._turn_pipeline.transcribe_audio(audio_summary)
            stt_ended_at = datetime.now(UTC)
            stt_ms = round((stt_ended_at - stt_started_at).total_seconds() * 1000, 2)
            audio_quality = analyze_pcm_s16le_audio(
                audio_summary.audio_bytes,
                sample_rate_hz=audio_summary.sample_rate_hz,
                channels=audio_summary.channels,
                encoding=audio_summary.encoding,
                ambient_audio_bytes=audio_summary.ambient_audio_bytes,
                endpoint_audio_metrics=audio_summary.endpoint_audio_metrics,
            )
            total_ms = round((datetime.now(UTC) - turn_started_at).total_seconds() * 1000, 2)
            self._append_latency_point("stt_end", "STT end", stt_ended_at)
            self._last_transcript_metadata = {
                "provider_id": transcript.provider_id,
                "model": transcript.model,
                "confidence": transcript.confidence,
                "duration_ms": transcript.duration_ms,
                "text_chars": len(transcript.text or ""),
                "error": transcript.error,
                "audio_quality": audio_quality.as_context(),
                "speaker_enrollment_capture": {
                    "mode": enrollment_capture_window.get("mode"),
                    "started_at": enrollment_capture_window.get("started_at"),
                    "expires_at": enrollment_capture_window.get("expires_at"),
                },
            }
            transcript_metadata = {**self._last_transcript_metadata, "text": transcript.text}
            self._last_turn_timings = {
                "stt_ms": stt_ms,
                "assistant_ms": 0.0,
                "tts_ms": 0.0,
                "total_ms": total_ms,
            }
            self._update_active_session_history(
                transcript=transcript_metadata,
                turn_timings=self._last_turn_timings,
                speaker_enrollment_capture={
                    "mode": enrollment_capture_window.get("mode"),
                    "started_at": enrollment_capture_window.get("started_at"),
                    "expires_at": enrollment_capture_window.get("expires_at"),
                    "tts_suppressed": True,
                },
            )
            wake_recording = self._attach_wake_recording_transcript(
                wake_recording,
                transcript=transcript_metadata,
            )
            record_voice_event(
                "speaker_id.enrollment_capture.completed",
                endpoint_id=session.endpoint_id,
                session_id=session.session_id,
                provider_id=transcript.provider_id,
                model=transcript.model,
                confidence=transcript.confidence,
                duration_ms=transcript.duration_ms,
                text_chars=len(transcript.text or ""),
                transcript_text=transcript.text,
                error=transcript.error,
                stt_ms=stt_ms,
                total_ms=total_ms,
                audio_quality=audio_quality.as_context(),
            )
            if transcript.error:
                error = self._error_event(
                    endpoint_id=session.endpoint_id,
                    session_id=session.session_id,
                    code="stt_failed",
                    message=transcript.error,
                    recoverable=True,
                )
                self._set_session_state("failed")
                self._persist_active_session_history(
                    session,
                    completion_reason="stt_failed",
                    error_state=error.payload,
                    wake_recording=wake_recording,
                )
                self._release_active_session_wake_stream()
                self._clear_active_session_runtime()
                return [error]
            events.append(
                self._state_event(
                    "transcript.final",
                    session,
                    extra_payload=VoiceTranscriptPayload(
                        text=transcript.text,
                        confidence=transcript.confidence,
                    ).model_dump(mode="json"),
                )
            )
            self._last_transcript = transcript.text
            self._last_response = "Speaker enrollment capture recorded."
            self._last_assistant = {
                "provider_id": "speaker_id_enrollment_capture",
                "duration_ms": 0.0,
                "text": self._last_response,
                "text_chars": len(self._last_response),
                "error": None,
                "handled_locally": True,
                "provider_metadata": {"tts_suppressed": True},
            }
            self._last_tts = None
            self._update_active_session_history(assistant=self._last_assistant)
            self._set_session_state("local_command")
            self._set_session_state("completed")
            events.append(
                self._state_event(
                    "session.completed",
                    session,
                    extra_payload={
                        "completion_reason": "speaker_enrollment_capture",
                        "chunk_count": self._chunk_count,
                        **({"wake_recording": wake_recording} if wake_recording else {}),
                    },
                )
            )
            self._persist_active_session_history(
                session,
                completion_reason="speaker_enrollment_capture",
                wake_recording=wake_recording,
            )
            self._release_active_session_wake_stream()
            self._clear_active_session_runtime()
            return events
        if self._turn_pipeline is not None:
            stt_started_at = datetime.now(UTC)
            self._append_latency_point("stt_start", "STT start", stt_started_at)
            turn = self._turn_pipeline.complete_turn(
                VoiceTurnAudioSummary(
                    endpoint_id=session.endpoint_id,
                    session_id=session.session_id,
                    chunk_count=self._chunk_count,
                    sample_rate_hz=self._audio_format.sample_rate_hz if self._audio_format else None,
                    encoding=self._audio_format.encoding if self._audio_format else None,
                    channels=self._audio_format.channels if self._audio_format else 1,
                    audio_bytes=b"".join(self._audio_chunks),
                    ambient_audio_bytes=b"".join(self._ambient_audio_chunks) or None,
                    endpoint_audio_metrics=self._endpoint_audio_metrics(),
                )
            )
            stt_ended_at = stt_started_at + timedelta(milliseconds=turn.timings.stt_ms)
            intent_done_at = stt_ended_at + timedelta(milliseconds=turn.timings.assistant_ms)
            tts_started_at = intent_done_at
            tts_ended_at = tts_started_at + timedelta(milliseconds=turn.timings.tts_ms)
            self._append_latency_point("stt_end", "STT end", stt_ended_at)
            self._append_latency_point("intent_processing_done", "Intent processing done", intent_done_at)
            self._append_latency_point("tts_start", "TTS start", tts_started_at)
            self._append_latency_point("tts_end", "TTS end", tts_ended_at)
            self._last_transcript_metadata = {
                "provider_id": turn.transcript.provider_id,
                "model": turn.transcript.model,
                "confidence": turn.transcript.confidence,
                "duration_ms": turn.transcript.duration_ms,
                "text_chars": len(turn.transcript.text or ""),
                "error": turn.transcript.error,
            }
            if turn.speaker_identity is not None:
                self._last_transcript_metadata["speaker_identity"] = turn.speaker_identity.as_context()
            if turn.audio_quality is not None:
                self._last_transcript_metadata["audio_quality"] = turn.audio_quality.as_context()
            transcript_metadata = {**self._last_transcript_metadata, "text": turn.transcript.text}
            self._last_turn_timings = {
                "stt_ms": turn.timings.stt_ms,
                "assistant_ms": turn.timings.assistant_ms,
                "tts_ms": turn.timings.tts_ms,
                "total_ms": turn.timings.total_ms,
            }
            self._update_active_session_history(
                transcript=transcript_metadata,
                turn_timings=self._last_turn_timings,
            )
            wake_recording = self._attach_wake_recording_transcript(
                wake_recording,
                transcript=transcript_metadata,
            )
            log.info(
                "Voice transcript finalized: endpoint_id=%s session_id=%s provider=%s model=%s duration_ms=%s text_chars=%s error=%s stt_ms=%s assistant_ms=%s tts_ms=%s total_ms=%s",
                session.endpoint_id,
                session.session_id,
                turn.transcript.provider_id,
                turn.transcript.model,
                turn.transcript.duration_ms,
                len(turn.transcript.text or ""),
                turn.transcript.error,
                turn.timings.stt_ms,
                turn.timings.assistant_ms,
                turn.timings.tts_ms,
                turn.timings.total_ms,
            )
            record_voice_event(
                "transcript.final",
                endpoint_id=session.endpoint_id,
                session_id=session.session_id,
                provider_id=turn.transcript.provider_id,
                model=turn.transcript.model,
                confidence=turn.transcript.confidence,
                duration_ms=turn.transcript.duration_ms,
                text_chars=len(turn.transcript.text or ""),
                transcript_text=turn.transcript.text,
                error=turn.transcript.error,
                stt_ms=turn.timings.stt_ms,
                assistant_ms=turn.timings.assistant_ms,
                tts_ms=turn.timings.tts_ms,
                total_ms=turn.timings.total_ms,
                speaker_identity=turn.speaker_identity.as_context() if turn.speaker_identity else None,
                audio_quality=turn.audio_quality.as_context() if turn.audio_quality else None,
            )
            if turn.transcript.error:
                error = self._error_event(
                    endpoint_id=session.endpoint_id,
                    session_id=session.session_id,
                    code="stt_failed",
                    message=turn.transcript.error,
                    recoverable=True,
                )
                self._set_session_state("failed")
                self._persist_active_session_history(
                    session,
                    completion_reason="stt_failed",
                    error_state=error.payload,
                    wake_recording=wake_recording,
                )
                self._release_active_session_wake_stream()
                self._clear_active_session_runtime()
                return [error]
            events.append(
                self._state_event(
                    "transcript.final",
                    session,
                    extra_payload=VoiceTranscriptPayload(
                        text=turn.transcript.text,
                        confidence=turn.transcript.confidence,
                    ).model_dump(mode="json"),
                )
            )
            self._last_transcript = turn.transcript.text
            if turn.assistant_response.handled_locally:
                self._set_session_state("local_command")
            else:
                self._set_session_state("routing")
            events.append(
                self._state_event(
                    "response.text",
                    session,
                    extra_payload=VoiceResponseTextPayload(text=turn.assistant_response.spoken_text).model_dump(
                        mode="json"
                    ),
                )
            )
            self._last_response = turn.assistant_response.spoken_text
            self._last_assistant = {
                "provider_id": turn.assistant_response.provider_id,
                "model": turn.assistant_response.model,
                "duration_ms": turn.timings.assistant_ms,
                "text": turn.assistant_response.spoken_text,
                "text_chars": len(turn.assistant_response.spoken_text or ""),
                "error": turn.assistant_response.error,
                "handled_locally": turn.assistant_response.handled_locally,
                "provider_latency_ms": turn.assistant_response.provider_latency_ms,
                "provider_metadata": turn.assistant_response.provider_metadata,
                "fallback_used": turn.assistant_response.fallback_used,
                "fallback_reason": turn.assistant_response.fallback_reason,
                "intent_latency_ms": turn.assistant_response.intent_latency_ms,
                "conversation_followup": turn.assistant_response.conversation_followup,
            }
            if turn.speaker_identity is not None:
                self._last_assistant["speaker_identity"] = turn.speaker_identity.as_context()
            self._update_active_session_history(assistant=self._last_assistant)
            self._set_session_state("responding")
            self._last_tts = tts_synthesis_metadata(turn.tts)
            self._last_tts["spoken_text"] = turn.assistant_response.spoken_text
            self._last_tts["transcript"] = self._attach_tts_sidecar_turn_text(
                turn.tts,
                transcript=transcript_metadata,
                spoken_text=turn.assistant_response.spoken_text,
            )
            self._update_active_session_history(tts=self._last_tts)
            record_voice_event(
                "tts.ready",
                endpoint_id=session.endpoint_id,
                session_id=session.session_id,
                provider_id=turn.tts.provider_id,
                content_type=turn.tts.content_type,
                stream_id=turn.tts.stream_id,
                audio_url=endpoint_tts_audio_url(turn.tts),
                audio_variant=turn.tts.audio_variant,
                raw_sample_rate_hz=turn.tts.raw_sample_rate_hz,
                output_sample_rate_hz=turn.tts.output_sample_rate_hz,
                text_chars=len(turn.assistant_response.spoken_text or ""),
                spoken_text=turn.assistant_response.spoken_text,
                duration_ms=turn.timings.tts_ms,
                error=turn.tts.error,
            )
            if turn.tts.error:
                events.append(
                    self._error_event(
                        endpoint_id=session.endpoint_id,
                        session_id=session.session_id,
                        code="tts_failed",
                        message=turn.tts.error,
                        recoverable=True,
                    )
                )
                self._set_session_state("failed")
                self._persist_active_session_history(
                    session,
                    completion_reason="tts_failed",
                    error_state=events[-1].payload,
                    wake_recording=wake_recording,
                )
                self._release_active_session_wake_stream()
                self._clear_active_session_runtime()
                return events
            events.append(
                self._state_event(
                    "tts.ready",
                    session,
                    extra_payload=VoiceTtsReadyPayload(
                        content_type=turn.tts.content_type,
                        stream_id=turn.tts.stream_id,
                        audio_url=endpoint_tts_audio_url(turn.tts),
                    ).model_dump(mode="json"),
                )
            )
            self._update_vad_latency("tts_ready", datetime.now(UTC))
            if turn.assistant_response.conversation_followup:
                self._pending_session_followup = {
                    **turn.assistant_response.conversation_followup,
                    "listen_timeout_ms": int(FOLLOWUP_LISTEN_TIMEOUT_S * 1000),
                    "state": "waiting_for_tts_playback",
                }
                self._update_active_session_history(conversation_followup=self._pending_session_followup)
                return events
        else:
            self._set_session_state("local_command")
            self._set_session_state("responding")
        self._set_session_state("completed")
        events.append(
            self._state_event(
                "session.completed",
                session,
                extra_payload={
                    "completion_reason": "turn_completed",
                    "chunk_count": self._chunk_count,
                    **({"wake_recording": wake_recording} if wake_recording else {}),
                },
            )
        )
        self._persist_active_session_history(
            session,
            completion_reason="turn_completed",
            wake_recording=wake_recording,
        )
        self._release_active_session_wake_stream()
        self._clear_active_session_runtime()
        return events

    def _complete_active_placement_test(
        self,
        *,
        session: VoiceSessionSnapshot,
        event: VoiceEventEnvelope,
        placement_window: dict[str, object] | None,
    ) -> list[VoiceEventEnvelope]:
        transcribe_audio = getattr(self._turn_pipeline, "transcribe_audio", None)
        if not callable(transcribe_audio):
            return [
                self._error_event(
                    endpoint_id=session.endpoint_id,
                    session_id=session.session_id,
                    code="placement_test_pipeline_unavailable",
                    message="Placement tests require the voice turn pipeline STT path.",
                    recoverable=True,
                )
            ]

        placement_test = dict(self._active_session_history.get("placement_test") or {}) if self._active_session_history else {}
        if placement_window is not None:
            placement_test = {**placement_window, **placement_test}
        audio_summary = VoiceTurnAudioSummary(
            endpoint_id=session.endpoint_id,
            session_id=session.session_id,
            chunk_count=self._chunk_count,
            sample_rate_hz=self._audio_format.sample_rate_hz if self._audio_format else None,
            encoding=self._audio_format.encoding if self._audio_format else None,
            channels=self._audio_format.channels if self._audio_format else 1,
            audio_bytes=b"".join(self._audio_chunks),
            ambient_audio_bytes=b"".join(self._ambient_audio_chunks) or None,
            endpoint_audio_metrics=self._endpoint_audio_metrics(),
        )
        stt_started_at = datetime.now(UTC)
        self._append_latency_point("stt_start", "STT start", stt_started_at)
        turn_started_at = datetime.now(UTC)
        transcript = transcribe_audio(audio_summary)
        stt_ended_at = datetime.now(UTC)
        self._append_latency_point("stt_end", "STT end", stt_ended_at)
        audio_quality = analyze_pcm_s16le_audio(
            audio_summary.audio_bytes,
            sample_rate_hz=audio_summary.sample_rate_hz,
            channels=audio_summary.channels,
            encoding=audio_summary.encoding,
            ambient_audio_bytes=audio_summary.ambient_audio_bytes,
            endpoint_audio_metrics=audio_summary.endpoint_audio_metrics,
        )
        identify_speaker = getattr(self._turn_pipeline, "identify_speaker", None)
        speaker_identity = identify_speaker(audio_summary) if callable(identify_speaker) else None
        speaker_context = (
            speaker_identity.as_context()
            if hasattr(speaker_identity, "as_context")
            else dict(speaker_identity or {"status": "unavailable", "reason": "pipeline_identify_speaker_unavailable"})
        )
        transcript_context = {
            "provider_id": transcript.provider_id,
            "model": transcript.model,
            "confidence": transcript.confidence,
            "duration_ms": transcript.duration_ms,
            "text_chars": len(transcript.text or ""),
            "error": transcript.error,
            "text": transcript.text,
        }
        report = build_active_placement_report(
            PlacementReportInput(
                test=placement_test,
                transcript=transcript_context,
                speaker_identity=speaker_context,
                audio_quality=audio_quality.as_context(),
                related_reports=self._related_placement_reports(placement_test),
            )
        )
        completed_at = datetime.now(UTC)
        debug_requested = bool(placement_test.get("debug_record_audio"))
        placement_record = {
            **placement_test,
            "schema_version": 1,
            "mode": "active",
            "status": "completed",
            "session_id": session.session_id,
            "endpoint_id": session.endpoint_id,
            "completed_at": completed_at.isoformat(),
            "chunk_count": self._chunk_count,
            "raw_audio": {
                "persisted": False,
                "debug_record_audio": debug_requested,
                "retention_days": 1 if debug_requested else None,
                "reason": "disabled_by_default" if not debug_requested else "debug_recording_not_configured",
            },
            "transcript": transcript_context,
            "speaker_identity": speaker_context,
            "audio_quality": audio_quality.as_context(),
            "report": report,
        }
        self._last_transcript = transcript.text
        self._last_response = "Placement test recorded."
        self._last_tts = None
        self._last_turn_timings = {
            "stt_ms": round((stt_ended_at - stt_started_at).total_seconds() * 1000, 2),
            "assistant_ms": 0.0,
            "tts_ms": 0.0,
            "total_ms": round((completed_at - turn_started_at).total_seconds() * 1000, 2),
            "speaker_id_ms": speaker_context.get("duration_ms"),
        }
        self._last_transcript_metadata = {
            **{key: value for key, value in transcript_context.items() if key != "text"},
            "speaker_identity": speaker_context,
            "audio_quality": audio_quality.as_context(),
            "placement_test": {
                "test_id": placement_record.get("test_id"),
                "room": placement_record.get("room"),
                "zone": placement_record.get("zone"),
                "position_label": placement_record.get("position_label"),
                "score": report.get("score"),
                "recommendation": report.get("recommendation"),
            },
        }
        self._last_assistant = {
            "provider_id": "placement_test",
            "duration_ms": 0.0,
            "text": self._last_response,
            "text_chars": len(self._last_response),
            "error": None,
            "handled_locally": True,
            "provider_metadata": {"tts_suppressed": True, "placement_test": placement_record.get("test_id")},
        }
        self._update_active_session_history(
            transcript=transcript_context,
            turn_timings=self._last_turn_timings,
            assistant=self._last_assistant,
            placement_test=placement_record,
        )
        record_voice_event(
            "placement_test.completed",
            endpoint_id=session.endpoint_id,
            session_id=session.session_id,
            test_id=placement_record.get("test_id"),
            room=placement_record.get("room"),
            zone=placement_record.get("zone"),
            position_label=placement_record.get("position_label"),
            score=report.get("score"),
            recommendation=report.get("recommendation"),
            warnings=report.get("warnings"),
            transcript_text=transcript.text,
            speaker_identity=speaker_context,
            audio_quality=audio_quality.as_context(),
        )
        self._active_placement_test_windows.pop(session.endpoint_id, None)
        events = [
            self._state_event(
                "transcript.final",
                session,
                extra_payload=VoiceTranscriptPayload(
                    text=transcript.text,
                    confidence=transcript.confidence,
                ).model_dump(mode="json"),
            )
        ]
        self._set_session_state("local_command")
        self._set_session_state("completed")
        events.append(
            self._state_event(
                "session.completed",
                session,
                extra_payload={
                    "completion_reason": "placement_test",
                    "chunk_count": self._chunk_count,
                    "placement_test": placement_record,
                },
            )
        )
        self._persist_active_session_history(session, completion_reason="placement_test")
        self._release_active_session_wake_stream()
        self._clear_active_session_runtime()
        return events

    def status(self) -> dict:
        self._expire_commands()
        self._expire_stale_active_sessions()
        selected_runtime = self._status_runtime()
        token = self._runtime_context.set(selected_runtime)
        try:
            active_session = self._status_visible_session(selected_runtime)
            active_snapshot = active_session.model_dump(mode="json") if active_session else None
            state_projection = project_voice_state(
                connection_active=self._connection_active,
                active_session=active_session,
            ).model_dump(mode="json")
            connected_endpoint_ids = sorted(
                endpoint_id
                for endpoint_id, state in self._endpoint_runtimes.items()
                if state.connection_active and state.websocket is not None
            )
            latest_replay_session = (
                self._session_history_store.latest_replay_eligible(endpoint_id=self._connected_endpoint_id)
                if self._session_history_store is not None
                else None
            )
            session_history = (
                {
                    **self._session_history_store.status(),
                    "recent_sessions": self._session_history_store.list_sessions(limit=5),
                }
                if self._session_history_store is not None
                else {"enabled": False, "recent_sessions": []}
            )
            return {
                "endpoint_id": self._connected_endpoint_id,
                "connected_endpoint_ids": connected_endpoint_ids,
                "connection_count": len(connected_endpoint_ids),
                "endpoints": {
                    endpoint_id: self._runtime_status_summary(state)
                    for endpoint_id, state in sorted(self._endpoint_runtimes.items())
                    if state.connection_active and state.websocket is not None
                },
                "connection_state": state_projection["connection_state"],
                "ux_state": state_projection["ux_state"],
                "session_state": state_projection["session_state"],
                "transport_health": state_projection["transport_health"],
                "state_projection": state_projection,
                "active_session": active_snapshot,
                "last_session_id": active_snapshot["session_id"] if active_snapshot else None,
                "last_event_type": self._last_event_type,
                "last_transcript": self._last_transcript,
                "last_transcript_metadata": self._last_transcript_metadata,
                "last_turn_timings": self._last_turn_timings,
                "last_response": self._last_response,
                "last_assistant": self._last_assistant,
                "last_tts": self._last_tts,
                "last_tts_playback": self._last_tts_playback,
                "tts_playback_history": list(self._tts_playback_history),
                "active_playbacks": list(self._active_playbacks.values()),
                "last_playback_interrupt": self._last_playback_interrupt,
                "last_error": self._last_error,
                "last_command_ack": self._last_command_ack,
                "last_command_error": self._last_command_error,
                "commands": list(self._command_records.values()),
                "event_diagnostics": list(self._event_diagnostics),
                "wake_provider": self._wake_detector.status(),
                "wake_election": self._wake_election.status(),
                "wake_history": list(self._wake_history),
                "wake_confidence_history": list(self._wake_confidence_history),
                "wake_recordings": self._wake_recorder.status() if self._wake_recorder else {"enabled": False},
                "speaker_enrollment_capture": {
                    "blocked": self._privacy_mode_enabled,
                    "blocked_reason": "privacy_mode_enabled" if self._privacy_mode_enabled else None,
                    "active_windows": self.speaker_enrollment_capture_windows(),
                },
                "placement_tests": {
                    "blocked": self._privacy_mode_enabled,
                    "blocked_reason": "privacy_mode_enabled" if self._privacy_mode_enabled else None,
                    "active_windows": self.placement_test_windows(),
                    "recent_reports": self.list_placement_tests(limit=5),
                },
                "placement_calibrations": self.passive_placement_calibration_status(),
                "voice_quality_observations": self.voice_quality_observation_status(),
                "endpoint_audio_quality": self.endpoint_audio_quality_stats(),
                "privacy_mode": {
                    "enabled": self._privacy_mode_enabled,
                    "blocked_features": [
                        "speaker_id_lookup",
                        "profile_learning_eligibility",
                        "observation_logging",
                        "debug_raw_audio_recording",
                        "active_placement_tests",
                        "passive_ambient_calibration",
                        "admin_maintenance_voice_intents",
                        "profile_enrollment_captures",
                    ]
                    if self._privacy_mode_enabled
                    else [],
                },
                "session_history": session_history,
                "turn_pipeline": self._turn_pipeline.status() if self._turn_pipeline else None,
                "supported_actions": {
                    "refresh": True,
                    "test_assistant_turn": True,
                    "stop_session": active_session is not None,
                    "replay_response": bool(connected_endpoint_ids)
                    and (self._last_tts is not None or latest_replay_session is not None),
                    "mute_endpoint": bool(connected_endpoint_ids),
                    "set_volume": bool(connected_endpoint_ids),
                    "send_media": bool(connected_endpoint_ids),
                    "reconnect": False,
                },
            }
        finally:
            self._runtime_context.reset(token)

    def list_session_history(self, *, limit: int = 20, endpoint_id: str | None = None) -> list[dict[str, Any]]:
        if self._session_history_store is None:
            return []
        return self._session_history_store.list_sessions(limit=limit, endpoint_id=endpoint_id)

    def endpoint_audio_quality_stats(self, *, limit: int = 200, endpoint_id: str | None = None) -> dict[str, object]:
        bounded_limit = max(1, min(int(limit), 500))
        if self._session_history_store is None:
            return {
                "schema_version": 1,
                "enabled": False,
                "source": "voice_session_history",
                "window": {"session_limit": bounded_limit, "observed_session_count": 0},
                "endpoints": [],
                "endpoint_count": 0,
            }

        sessions = self._session_history_store.list_sessions(limit=bounded_limit, endpoint_id=endpoint_id)
        groups: dict[str, dict[str, Any]] = {}
        observed_count = 0
        for session in sessions:
            transcript = session.get("transcript") if isinstance(session.get("transcript"), dict) else {}
            audio_quality = transcript.get("audio_quality") if isinstance(transcript.get("audio_quality"), dict) else None
            if audio_quality is None:
                continue
            current_endpoint_id = str(session.get("endpoint_id") or "unknown").strip() or "unknown"
            group = groups.setdefault(
                current_endpoint_id,
                {
                    "endpoint_id": current_endpoint_id,
                    "sample_count": 0,
                    "ok_count": 0,
                    "warning_count": 0,
                    "status_counts": {},
                    "warning_counts": {},
                    "snr_values": [],
                    "latest_observed_at": None,
                    "latest_session_id": None,
                    "latest_status": None,
                    "latest_warnings": [],
                },
            )
            observed_count += 1
            group["sample_count"] += 1
            status = str(audio_quality.get("status") or "unknown")
            group["status_counts"][status] = int(group["status_counts"].get(status, 0)) + 1
            warnings = [str(warning) for warning in audio_quality.get("warnings") or [] if warning]
            if status == "ok" and not warnings:
                group["ok_count"] += 1
            if warnings or status not in {"ok", "unknown"}:
                group["warning_count"] += 1
            for warning in warnings:
                group["warning_counts"][warning] = int(group["warning_counts"].get(warning, 0)) + 1
            snr_db = _float_or_none(audio_quality.get("snr_db"))
            if snr_db is not None:
                group["snr_values"].append(snr_db)
            observed_at = (
                session.get("completed_at")
                or session.get("updated_at")
                or session.get("started_at")
            )
            if group["latest_observed_at"] is None or str(observed_at or "") > str(group["latest_observed_at"] or ""):
                group["latest_observed_at"] = observed_at
                group["latest_session_id"] = session.get("session_id")
                group["latest_status"] = status
                group["latest_warnings"] = warnings

        endpoints = [_endpoint_audio_quality_summary(group) for group in groups.values()]
        endpoints.sort(
            key=lambda item: str((item.get("latest") if isinstance(item.get("latest"), dict) else {}).get("observed_at") or ""),
            reverse=True,
        )
        return {
            "schema_version": 1,
            "enabled": True,
            "source": "voice_session_history",
            "window": {
                "session_limit": bounded_limit,
                "scanned_session_count": len(sessions),
                "observed_session_count": observed_count,
            },
            "endpoints": endpoints,
            "endpoint_count": len(endpoints),
        }

    def voice_quality_observation_status(self) -> dict[str, object]:
        if self._quality_observation_log is None:
            return {
                "enabled": False,
                "blocked": self._privacy_mode_enabled,
                "blocked_reason": "privacy_mode_enabled" if self._privacy_mode_enabled else None,
                "retention_policy": "one_calendar_month",
            }
        return {
            **self._quality_observation_log.status(),
            "blocked": self._privacy_mode_enabled,
            "blocked_reason": "privacy_mode_enabled" if self._privacy_mode_enabled else None,
        }

    def cleanup_voice_quality_observations(self) -> dict[str, object]:
        if self._quality_observation_log is None:
            return {"enabled": False, "status": "unavailable", "retention_policy": "one_calendar_month"}
        return {"enabled": self._quality_observation_log.enabled, "status": "ok", **self._quality_observation_log.cleanup()}

    def start_speaker_enrollment_capture_window(self, *, endpoint_id: str, ttl_seconds: int = 300) -> dict[str, object]:
        endpoint_id = str(endpoint_id or "").strip()
        if not endpoint_id:
            raise ValueError("endpoint_id_required")
        if self._privacy_mode_enabled:
            raise ValueError("privacy_mode_enabled")
        ttl_seconds = max(30, min(int(ttl_seconds), 900))
        now = datetime.now(UTC)
        window = {
            "endpoint_id": endpoint_id,
            "mode": "speaker_id_enrollment",
            "started_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "ttl_seconds": ttl_seconds,
            "active": True,
        }
        self._speaker_enrollment_capture_windows[endpoint_id] = window
        return dict(window)

    def speaker_enrollment_capture_windows(self) -> list[dict[str, object]]:
        self._expire_speaker_enrollment_capture_windows()
        return [dict(window) for window in self._speaker_enrollment_capture_windows.values()]

    def _expire_speaker_enrollment_capture_windows(self) -> None:
        now = datetime.now(UTC)
        expired: list[str] = []
        for endpoint_id, window in self._speaker_enrollment_capture_windows.items():
            expires_at = str(window.get("expires_at") or "")
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=UTC)
            except ValueError:
                expired.append(endpoint_id)
                continue
            if expires_dt <= now:
                expired.append(endpoint_id)
        for endpoint_id in expired:
            self._speaker_enrollment_capture_windows.pop(endpoint_id, None)

    def _active_speaker_enrollment_capture_window(self, endpoint_id: str) -> dict[str, object] | None:
        self._expire_speaker_enrollment_capture_windows()
        window = self._speaker_enrollment_capture_windows.get(endpoint_id)
        return dict(window) if window else None

    def start_placement_test_window(
        self,
        *,
        endpoint_id: str,
        room: str,
        zone: str | None = None,
        position_label: str | None = None,
        expected_phrase: str,
        expected_speaker_public_id: str | None = None,
        ttl_seconds: int = 300,
        debug_record_audio: bool = False,
    ) -> dict[str, object]:
        endpoint_id = str(endpoint_id or "").strip()
        room = str(room or "").strip()
        expected_phrase = str(expected_phrase or "").strip()
        if not endpoint_id:
            raise ValueError("endpoint_id_required")
        if not room:
            raise ValueError("room_required")
        if not expected_phrase:
            raise ValueError("expected_phrase_required")
        if self._privacy_mode_enabled:
            raise ValueError("privacy_mode_enabled")
        ttl_seconds = max(30, min(int(ttl_seconds), 900))
        now = datetime.now(UTC)
        window = {
            "schema_version": 1,
            "test_id": f"placement-{uuid4().hex[:12]}",
            "endpoint_id": endpoint_id,
            "room": room,
            "zone": str(zone or "").strip() or None,
            "position_label": str(position_label or "").strip() or None,
            "expected_phrase": expected_phrase,
            "expected_speaker_public_id": str(expected_speaker_public_id or "").strip() or None,
            "mode": "active",
            "started_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "ttl_seconds": ttl_seconds,
            "active": True,
            "debug_record_audio": bool(debug_record_audio),
            "raw_audio_policy": "discard_after_metrics" if not debug_record_audio else "debug_retention_one_day",
        }
        self._active_placement_test_windows[endpoint_id] = window
        return dict(window)

    def placement_test_windows(self) -> list[dict[str, object]]:
        self._expire_placement_test_windows()
        return [dict(window) for window in self._active_placement_test_windows.values()]

    def list_placement_tests(self, *, limit: int = 20, endpoint_id: str | None = None) -> list[dict[str, object]]:
        if self._session_history_store is None:
            return []
        tests: list[dict[str, object]] = []
        for session in self._session_history_store.list_sessions(limit=200, endpoint_id=endpoint_id):
            placement = session.get("placement_test") if isinstance(session.get("placement_test"), dict) else None
            if placement and placement.get("report"):
                tests.append(dict(placement))
            if len(tests) >= max(1, min(limit, 50)):
                break
        return tests

    def get_placement_test(self, test_id: str) -> dict[str, object] | None:
        target = str(test_id or "").strip()
        if not target:
            return None
        for report in self.list_placement_tests(limit=50):
            if report.get("test_id") == target:
                return report
        return None

    def start_passive_placement_calibration(
        self,
        *,
        endpoint_id: str,
        room: str,
        zone: str | None = None,
        duration_hours: float = 24,
        sample_interval_seconds: int = 600,
        retention_days: int = 3,
        debug_record_audio: bool = False,
    ) -> dict[str, object]:
        if self._privacy_mode_enabled:
            raise ValueError("privacy_mode_enabled")
        if self._placement_calibration_store is None:
            raise ValueError("placement_calibration_store_unavailable")
        window = self._placement_calibration_store.start_window(
            endpoint_id=endpoint_id,
            room=room,
            zone=zone,
            duration_hours=duration_hours,
            sample_interval_seconds=sample_interval_seconds,
            retention_days=retention_days,
            debug_record_audio=debug_record_audio,
        )
        record_voice_event(
            "placement_calibration.started",
            endpoint_id=window.get("endpoint_id"),
            calibration_id=window.get("calibration_id"),
            room=window.get("room"),
            zone=window.get("zone"),
            duration_hours=window.get("duration_hours"),
            sample_interval_seconds=window.get("sample_interval_seconds"),
        )
        return window

    def cancel_passive_placement_calibration(self, calibration_id: str) -> dict[str, object] | None:
        if self._placement_calibration_store is None:
            return None
        window = self._placement_calibration_store.cancel_window(calibration_id)
        if window is not None:
            record_voice_event(
                "placement_calibration.cancelled",
                endpoint_id=window.get("endpoint_id"),
                calibration_id=window.get("calibration_id"),
                room=window.get("room"),
                zone=window.get("zone"),
            )
        return window

    def record_passive_placement_sample(
        self,
        *,
        calibration_id: str,
        metrics: dict[str, object],
        observed_at: str | None = None,
    ) -> dict[str, object]:
        if self._privacy_mode_enabled:
            raise ValueError("privacy_mode_enabled")
        if self._placement_calibration_store is None:
            raise ValueError("placement_calibration_store_unavailable")
        sample = self._placement_calibration_store.record_sample(
            calibration_id=calibration_id,
            metrics=metrics,
            observed_at=observed_at,
        )
        record_voice_event(
            "placement_calibration.sample.recorded",
            endpoint_id=sample.get("endpoint_id"),
            calibration_id=sample.get("calibration_id"),
            sample_id=sample.get("sample_id"),
            metrics=list((sample.get("metrics") or {}).keys()) if isinstance(sample.get("metrics"), dict) else [],
        )
        return sample

    def passive_placement_calibration_status(self, *, endpoint_id: str | None = None) -> dict[str, object]:
        if self._placement_calibration_store is None:
            return {
                "enabled": False,
                "blocked": self._privacy_mode_enabled,
                "blocked_reason": "privacy_mode_enabled" if self._privacy_mode_enabled else None,
                "active_windows": [],
                "recent_windows": [],
                "sample_count": 0,
            }
        status = self._placement_calibration_store.status(endpoint_id=endpoint_id)
        return {
            **status,
            "blocked": self._privacy_mode_enabled,
            "blocked_reason": "privacy_mode_enabled" if self._privacy_mode_enabled else None,
        }

    def passive_placement_report(self, calibration_id: str) -> dict[str, object] | None:
        if self._placement_calibration_store is None:
            return None
        window = self._placement_calibration_store.get_window(calibration_id)
        if window is None:
            return None
        samples = self._placement_calibration_store.list_samples(calibration_id=calibration_id, limit=5000)
        active_reports = self._active_placement_reports_for_window(window)
        return build_long_window_placement_report(
            window=window,
            passive_samples=samples,
            active_reports=active_reports,
        )

    def cleanup_passive_placement_calibrations(self) -> dict[str, object]:
        if self._placement_calibration_store is None:
            return {"enabled": False, "status": "unavailable"}
        before = self._placement_calibration_store.status()
        after_model = self._placement_calibration_store.cleanup()
        after = self._placement_calibration_store.status()
        return {
            "enabled": True,
            "status": "ok",
            "sample_count_before": before.get("sample_count"),
            "sample_count_after": after.get("sample_count"),
            "updated_at": after_model.updated_at,
        }

    def _active_placement_reports_for_window(self, window: dict[str, object]) -> list[dict[str, object]]:
        endpoint_id = str(window.get("endpoint_id") or "")
        room = window.get("room")
        zone = window.get("zone")
        reports: list[dict[str, object]] = []
        for placement in self.list_placement_tests(limit=50, endpoint_id=endpoint_id or None):
            if placement.get("room") != room or placement.get("zone") != zone:
                continue
            report = placement.get("report") if isinstance(placement.get("report"), dict) else None
            if report:
                reports.append(dict(report))
        return reports

    def _expire_placement_test_windows(self) -> None:
        now = datetime.now(UTC)
        expired: list[str] = []
        for endpoint_id, window in self._active_placement_test_windows.items():
            expires_at = str(window.get("expires_at") or "")
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=UTC)
            except ValueError:
                expired.append(endpoint_id)
                continue
            if expires_dt <= now:
                expired.append(endpoint_id)
        for endpoint_id in expired:
            self._active_placement_test_windows.pop(endpoint_id, None)

    def _active_placement_test_window(self, endpoint_id: str) -> dict[str, object] | None:
        self._expire_placement_test_windows()
        window = self._active_placement_test_windows.get(endpoint_id)
        return dict(window) if window else None

    def _related_placement_reports(self, placement_test: dict[str, object]) -> list[dict[str, object]]:
        if self._session_history_store is None:
            return []
        endpoint_id = str(placement_test.get("endpoint_id") or "")
        room = placement_test.get("room")
        zone = placement_test.get("zone")
        expected_phrase = placement_test.get("expected_phrase")
        current_test_id = placement_test.get("test_id")
        reports: list[dict[str, object]] = []
        for session in self._session_history_store.list_sessions(limit=50, endpoint_id=endpoint_id or None):
            placement = session.get("placement_test") if isinstance(session.get("placement_test"), dict) else None
            if not placement or placement.get("test_id") == current_test_id:
                continue
            if placement.get("room") != room or placement.get("zone") != zone:
                continue
            if placement.get("expected_phrase") != expected_phrase:
                continue
            report = placement.get("report") if isinstance(placement.get("report"), dict) else None
            if report:
                reports.append(dict(report))
        return reports

    def get_session_history(self, session_id: str) -> dict[str, Any] | None:
        if self._session_history_store is None:
            return None
        return self._session_history_store.get_session(session_id)

    def wake_recording_path(self, recording_id: str) -> Path | None:
        if self._wake_recorder is None:
            return None
        return self._wake_recorder.recording_path(recording_id)

    def delete_wake_recording(self, recording_id: str) -> dict[str, Any]:
        if self._wake_recorder is None:
            return {"recording_id": recording_id, "deleted_count": 0, "deleted_paths": [], "status": "disabled"}
        return self._wake_recorder.delete_recording(recording_id)

    def preload_wake_detector(self) -> dict | None:
        preload = getattr(self._wake_detector, "preload", None)
        if not callable(preload):
            return None
        return preload()

    def preload_turn_pipeline(self) -> dict | None:
        if self._turn_pipeline is None:
            return None
        preload_stt = getattr(self._turn_pipeline, "preload_stt", None)
        if not callable(preload_stt):
            return None
        return preload_stt()

    def _record_wake_history(self, entry: dict[str, object]) -> None:
        event = {"timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"), **entry}
        self._wake_history.insert(0, event)
        del self._wake_history[10:]

    def _record_wake_confidence(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        model: str | None,
        confidence: float | None,
        detected: bool,
        accepted: bool,
        reason: str | None = None,
        source: str | None = None,
        chunk_index: int | None = None,
        chunk_count: int | None = None,
    ) -> None:
        if confidence is None:
            return
        event = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "endpoint_id": endpoint_id,
            "session_id": session_id,
            "model": model,
            "confidence": confidence,
            "detected": detected,
            "accepted": accepted,
            "reason": reason,
            "source": source,
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
        }
        self._wake_confidence_history.insert(0, event)
        del self._wake_confidence_history[50:]
        record_voice_event("wake.confidence", **event)

    def _record_accepted_wake_session(self, session: VoiceSessionSnapshot) -> dict[str, object] | None:
        if self._wake_recorder is None:
            return None
        recording = self._wake_recorder.record_accepted_session(
            endpoint_id=session.endpoint_id,
            session_id=session.session_id,
            stt_chunks=self._audio_chunks,
            chunk_count=self._chunk_count,
        )
        if recording is None:
            return None
        record_voice_event(
            "wake.recording.saved",
            endpoint_id=session.endpoint_id,
            session_id=session.session_id,
            wav_path=recording.get("wav_path"),
            metadata_path=recording.get("metadata_path"),
            duration_ms=recording.get("duration_ms"),
            model=recording.get("model"),
            confidence=recording.get("confidence"),
            expires_at=recording.get("expires_at"),
        )
        return recording

    def _attach_wake_recording_transcript(
        self,
        wake_recording: dict[str, object] | None,
        *,
        transcript: dict[str, Any],
    ) -> dict[str, object] | None:
        if self._wake_recorder is None or wake_recording is None:
            return wake_recording
        return self._wake_recorder.attach_transcript(wake_recording, transcript)

    def _attach_tts_sidecar_turn_text(
        self,
        tts: TtsSynthesis,
        *,
        transcript: dict[str, Any],
        spoken_text: str,
    ) -> dict[str, Any]:
        cleaned_transcript = {key: value for key, value in transcript.items() if value is not None}
        if not tts.metadata_path:
            return cleaned_transcript
        metadata_path = Path(tts.metadata_path)
        if not metadata_path.is_file():
            return cleaned_transcript
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cleaned_transcript
        if not isinstance(metadata, dict):
            return cleaned_transcript
        metadata["transcript"] = cleaned_transcript
        metadata["spoken_text"] = spoken_text
        try:
            metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            log.warning("Failed to attach transcript to TTS sidecar %s", metadata_path)
        return cleaned_transcript

    def _begin_active_session_history(
        self,
        *,
        session: VoiceSessionSnapshot,
        start_payload: VoiceSessionStartPayload,
    ) -> None:
        self._active_session_history = {
            "session_id": session.session_id,
            "endpoint_id": session.endpoint_id,
            "session_state": session.session_state,
            "started_at": session.started_at.isoformat(),
            "wake_source": start_payload.wake_source,
            "firmware_version": start_payload.firmware_version,
            "audio": {
                "chunk_count": 0,
                "captured_chunk_count": 0,
                "format": start_payload.audio_format.model_dump(mode="json"),
                "raw_audio_persisted": False,
            },
            "replay": {"eligible": False, "reason": "tts_unavailable"},
        }

    def _update_active_session_audio(self, payload: VoiceAudioChunkPayload) -> None:
        if self._active_session_history is None:
            return
        audio = dict(self._active_session_history.get("audio") or {})
        audio.update(
            {
                "chunk_count": self._chunk_count,
                "format": payload.audio_format.model_dump(mode="json"),
                "raw_audio_persisted": False,
            }
        )
        self._active_session_history["audio"] = audio
        self._update_endpoint_audio_metrics(payload)

    def _is_ambient_reference_chunk(self, session: VoiceSessionSnapshot) -> bool:
        if self._active_playback_interrupt(session.endpoint_id) is not None:
            return False
        if session.session_state == "idle":
            return True
        vad = self._active_session_history.get("vad") if self._active_session_history else None
        return session.session_state in {"wake_detected", "listening"} and not (
            isinstance(vad, dict) and vad.get("speech_started_at")
        )

    def _update_endpoint_audio_metrics(self, payload: VoiceAudioChunkPayload) -> None:
        if self._active_session_history is None:
            return
        has_metrics = any(
            value is not None
            for value in (
                payload.frame_level,
                payload.noise_floor_level,
                payload.speech_peak_level,
                payload.pre_roll_duration_ms,
            )
        ) or payload.contains_pre_roll or payload.contains_speech
        if not has_metrics:
            return
        audio = dict(self._active_session_history.get("audio") or {})
        metrics = dict(audio.get("endpoint_audio_metrics") or {})
        metrics["schema_version"] = 1
        metrics["chunk_count"] = int(metrics.get("chunk_count") or 0) + 1
        if payload.frame_level is not None:
            metrics["frame_level_peak"] = max(int(metrics.get("frame_level_peak") or 0), payload.frame_level)
            if payload.contains_pre_roll:
                current_pre_roll_peak = metrics.get("pre_roll_peak")
                metrics["pre_roll_peak"] = max(
                    float(current_pre_roll_peak) if isinstance(current_pre_roll_peak, (int, float)) else 0.0,
                    _level_to_ratio(payload.frame_level),
                )
        if payload.noise_floor_level is not None:
            metrics["noise_floor_level"] = payload.noise_floor_level
            metrics["noise_floor_rms"] = _level_to_ratio(payload.noise_floor_level)
        if payload.speech_peak_level is not None:
            metrics["speech_peak_level"] = max(int(metrics.get("speech_peak_level") or 0), payload.speech_peak_level)
            metrics["speech_peak"] = _level_to_ratio(metrics["speech_peak_level"])
        if payload.pre_roll_duration_ms is not None:
            metrics["pre_roll_duration_ms"] = int(metrics.get("pre_roll_duration_ms") or 0) + payload.pre_roll_duration_ms
        if payload.contains_pre_roll:
            metrics["contains_pre_roll"] = True
            metrics["pre_roll_chunk_count"] = int(metrics.get("pre_roll_chunk_count") or 0) + 1
        if payload.contains_speech:
            metrics["contains_speech"] = True
            metrics["speech_chunk_count"] = int(metrics.get("speech_chunk_count") or 0) + 1
        audio["endpoint_audio_metrics"] = metrics
        self._active_session_history["audio"] = audio

    def _endpoint_audio_metrics(self) -> dict[str, object] | None:
        if self._active_session_history is None:
            return None
        audio = self._active_session_history.get("audio")
        if not isinstance(audio, dict):
            return None
        metrics = audio.get("endpoint_audio_metrics")
        return dict(metrics) if isinstance(metrics, dict) and metrics else None

    def _set_active_session_wake(self, wake: dict[str, Any]) -> None:
        if self._active_session_history is None:
            return
        self._active_session_history["wake"] = wake

    def _set_active_session_vad(self, vad: dict[str, Any]) -> None:
        if self._active_session_history is None:
            return
        current = dict(self._active_session_history.get("vad") or {})
        current.update(vad)
        self._active_session_history["vad"] = current

    def _update_active_session_history(self, **entries: Any) -> None:
        if self._active_session_history is None:
            return
        for key, value in entries.items():
            if value is not None:
                self._active_session_history[key] = value

    def _append_latency_point(self, key: str, label: str, timestamp: datetime) -> None:
        if self._active_session_history is None:
            return
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        points = [
            dict(point)
            for point in self._active_session_history.get("latency_points") or []
            if isinstance(point, dict) and point.get("key") != key
        ]
        points.append({"key": key, "label": label, "timestamp": timestamp.isoformat()})
        vad_started_at = next(
            (
                self._parse_datetime(point.get("timestamp"))
                for point in points
                if point.get("key") == "vad_voice_detected"
            ),
            None,
        )
        points.sort(key=lambda point: LATENCY_POINT_ORDER.get(str(point.get("key")), 100))
        previous_at: datetime | None = None
        if vad_started_at is not None:
            for point in points:
                point_at = self._parse_datetime(point.get("timestamp"))
                if point_at is None:
                    continue
                point["offset_from_vad_ms"] = max(0, int((point_at - vad_started_at).total_seconds() * 1000))
                point["offset_from_previous_ms"] = (
                    0 if previous_at is None else max(0, int((point_at - previous_at).total_seconds() * 1000))
                )
                previous_at = point_at
        self._active_session_history["latency_points"] = points

    def _update_vad_latency(self, marker: str, ended_at: datetime) -> None:
        if self._active_session_history is None:
            return
        latency = self._vad_latency_record(self._active_session_history, marker, ended_at)
        if latency:
            self._update_active_session_history(latency=latency)

    def _vad_latency_record(self, record: dict[str, Any], marker: str, ended_at: datetime) -> dict[str, Any] | None:
        vad = record.get("vad")
        if not isinstance(vad, dict):
            return None
        started_at_raw = vad.get("speech_started_at")
        if not started_at_raw:
            return None
        started_at = self._parse_datetime(started_at_raw)
        if started_at is None:
            return None
        if ended_at.tzinfo is None:
            ended_at = ended_at.replace(tzinfo=UTC)
        existing = dict(record.get("latency") or {})
        existing["vad_speech_started_at"] = started_at.isoformat()
        existing[f"vad_to_{marker}_ms"] = max(0, int((ended_at - started_at).total_seconds() * 1000))
        return existing

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    def _upsert_persisted_session_history(self, session_id: str, **entries: Any) -> None:
        if self._session_history_store is None:
            return
        record = self._session_history_store.get_session(session_id)
        if record is None:
            return
        for key, value in entries.items():
            if value is not None:
                record[key] = value
        try:
            self._session_history_store.upsert_session(record)
        except Exception:
            log.exception("Failed to update persisted voice session history: session_id=%s", session_id)

    def _persist_active_session_history(
        self,
        session: VoiceSessionSnapshot,
        *,
        completion_reason: str,
        error_state: dict[str, Any] | None = None,
        wake_recording: dict[str, object] | None = None,
    ) -> None:
        if self._session_history_store is None or self._active_session_history is None:
            return
        completed_at = datetime.now(UTC)
        self._append_latency_point("session_end", "Session end", completed_at)
        self._update_vad_latency("session_completed", completed_at)
        audio = dict(self._active_session_history.get("audio") or {})
        if self._audio_format is not None:
            audio["format"] = self._audio_format.model_dump(mode="json")
        audio.update(
            {
                "chunk_count": self._chunk_count,
                "captured_chunk_count": len(self._audio_chunks),
                "ambient_reference_chunk_count": len(self._ambient_audio_chunks),
                "raw_audio_persisted": False,
            }
        )
        if error_state is None and session.last_error is not None:
            error_state = session.last_error.model_dump(mode="json")
        tts = self._active_session_history.get("tts")
        replay = self._replay_metadata(tts if isinstance(tts, dict) else None, error_state=error_state)
        record = {
            **self._active_session_history,
            "session_state": session.session_state,
            "completed_at": completed_at.isoformat(),
            "duration_ms": max(0, int((completed_at - session.started_at).total_seconds() * 1000)),
            "completion_reason": completion_reason,
            "cancel_reason": session.cancel_reason,
            "error_state": error_state,
            "audio": audio,
            "replay": replay,
        }
        if wake_recording is not None:
            record["wake_recording"] = wake_recording
        try:
            self._session_history_store.upsert_session(record)
        except Exception:
            log.exception("Failed to persist voice session history: session_id=%s", session.session_id)
        if completion_reason == "turn_completed" and self._quality_observation_log is not None:
            try:
                self._quality_observation_log.write_session_observation(record)
            except Exception:
                log.exception("Failed to write voice quality observation: session_id=%s", session.session_id)

    def _replay_metadata(self, tts: dict[str, Any] | None, *, error_state: dict[str, Any] | None) -> dict[str, Any]:
        if error_state is not None:
            return {"eligible": False, "reason": error_state.get("code") or "session_failed"}
        if not tts:
            return {"eligible": False, "reason": "tts_unavailable"}
        if tts.get("error"):
            return {"eligible": False, "reason": tts.get("error")}
        if not tts.get("stream_id"):
            return {"eligible": False, "reason": "tts_stream_unavailable"}
        endpoint_audio_url = endpoint_tts_audio_url(tts)
        if not endpoint_audio_url:
            return {"eligible": False, "reason": "tts_audio_url_unavailable"}
        return {
            "eligible": True,
            "reason": "cached_tts_available",
            "stream_id": tts.get("stream_id"),
            "content_type": tts.get("content_type"),
            "audio_url": endpoint_audio_url,
        }

    async def _push_session_replay(self, *, session: dict[str, Any], endpoint_id: str | None = None) -> dict:
        target_endpoint_id = endpoint_id or str(session.get("endpoint_id") or "")
        if not target_endpoint_id:
            return {"accepted": False, "reason": "endpoint_id_required", "status": "failed", "endpoint_id": ""}
        replay = session.get("replay") if isinstance(session.get("replay"), dict) else {}
        tts = session.get("tts") if isinstance(session.get("tts"), dict) else {}
        if not replay.get("eligible"):
            return {
                "accepted": False,
                "reason": replay.get("reason") or "replay_not_eligible",
                "status": "failed",
                "endpoint_id": target_endpoint_id,
            }
        endpoint_audio_url = endpoint_tts_audio_url(tts)
        if not tts.get("stream_id") or not endpoint_audio_url:
            return {
                "accepted": False,
                "reason": "tts_stream_unavailable",
                "status": "failed",
                "endpoint_id": target_endpoint_id,
            }
        result = await self._push_endpoint_command(
            endpoint_id=target_endpoint_id,
            event_type="endpoint.replay",
            command_type="endpoint.replay.session",
            request_id=f"session_replay_{uuid4().hex}",
            payload={
                "stream_id": tts.get("stream_id"),
                "content_type": tts.get("content_type"),
                "audio_url": endpoint_audio_url,
                "source_session_id": session.get("session_id"),
            },
        )
        return {"endpoint_id": target_endpoint_id, **result}

    def _clear_active_session_runtime(self) -> None:
        self._cancel_followup_timeout_task()
        if self._active_session is not None and self._micro_vad_chunk_recorder is not None:
            self._micro_vad_chunk_recorder.close_session(
                endpoint_id=self._active_session.endpoint_id,
                session_id=self._active_session.session_id,
            )
        self._active_session = None
        self._chunk_count = 0
        self._audio_chunks = []
        self._ambient_audio_chunks = []
        self._audio_format = None
        self._active_session_history = None
        self._pending_session_followup = None

    def cancel_from_operator(self, *, reason: str = "operator_cancelled") -> dict:
        runtime = self._status_runtime()
        token = self._runtime_context.set(runtime)
        try:
            if self._active_session is None:
                return {"accepted": False, "reason": "no_active_session", "status": self.status()}

            self._set_session_state("cancelled")
            self._active_session.cancel_reason = reason
            self._persist_active_session_history(
                self._active_session,
                completion_reason=reason,
            )
            self._release_active_session_wake_stream()
            self._clear_active_session_runtime()
            return {"accepted": True, "reason": reason, "status": self.status()}
        finally:
            self._runtime_context.reset(token)

    def _handle_session_cancel(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]

        self._set_session_state("cancelled")
        session.cancel_reason = str(event.payload.get("reason") or "endpoint_cancelled")
        cancelled = self._state_event("session.cancelled", session)
        self._persist_active_session_history(
            session,
            completion_reason=session.cancel_reason,
        )
        self._release_active_session_wake_stream()
        self._clear_active_session_runtime()
        return [cancelled]

    def _handle_session_ping(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]
        return [self._state_event("session.state", session)]

    def _handle_vad_speech_started(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]

        try:
            payload = VoiceVadSpeechStartedPayload.model_validate(event.payload)
        except ValidationError as exc:
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="invalid_vad_speech_started",
                    message=str(exc.errors()[0]["msg"]),
                    recoverable=True,
                )
            ]

        record = {
            "speech_started_at": event.timestamp.isoformat(),
            "level": payload.level,
            "source": payload.source or "firmware_vad",
        }
        self._set_active_session_vad(record)
        self._append_latency_point("vad_voice_detected", "VAD voice detected", event.timestamp)
        self._last_event_type = event.event_type
        record_voice_event(
            "vad.speech_started",
            endpoint_id=event.endpoint_id,
            session_id=session.session_id,
            level=payload.level,
            source=record["source"],
            speech_started_at=record["speech_started_at"],
        )
        log.info(
            "Endpoint VAD speech started: endpoint_id=%s session_id=%s level=%s timestamp=%s",
            event.endpoint_id,
            session.session_id,
            payload.level,
            record["speech_started_at"],
        )
        return [self._state_event("session.state", session)]

    def _handle_command_ack(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        try:
            payload = VoiceCommandAckPayload.model_validate(event.payload)
        except ValidationError as exc:
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="invalid_command_ack",
                    message=str(exc.errors()[0]["msg"]),
                    recoverable=True,
                )
            ]

        self._last_command_ack = {
            "event_id": event.event_id,
            "endpoint_id": event.endpoint_id,
            "session_id": event.session_id,
            **payload.model_dump(mode="json"),
            "received_at": datetime.now(UTC).isoformat(),
        }
        self._update_command_from_ack(payload)
        self._last_event_type = "command.ack"
        log.info(
            "Endpoint command acknowledgement: endpoint_id=%s request_id=%s command_type=%s status=%s",
            event.endpoint_id,
            payload.request_id,
            payload.command_type,
            payload.status,
        )
        return [self._state_event("session.state", self._active_session)] if self._active_session else []

    def _handle_command_error(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        try:
            payload = VoiceCommandErrorPayload.model_validate(event.payload)
        except ValidationError as exc:
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="invalid_command_error",
                    message=str(exc.errors()[0]["msg"]),
                    recoverable=True,
                )
            ]

        self._last_command_error = {
            "event_id": event.event_id,
            "endpoint_id": event.endpoint_id,
            "session_id": event.session_id,
            **payload.model_dump(mode="json"),
            "received_at": datetime.now(UTC).isoformat(),
        }
        self._update_command_from_error(payload)
        self._last_event_type = "command.error"
        self._record_event_diagnostic(
            code=payload.code,
            endpoint_id=event.endpoint_id,
            session_id=event.session_id,
            event_type=event.event_type,
            message=payload.message,
        )
        log.warning(
            "Endpoint command error: endpoint_id=%s request_id=%s command_type=%s code=%s message=%s",
            event.endpoint_id,
            payload.request_id,
            payload.command_type,
            payload.code,
            payload.message,
        )
        return [self._state_event("session.state", self._active_session)] if self._active_session else []

    def _handle_tts_playback_event(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        try:
            payload = VoiceTtsPlaybackPayload.model_validate(event.payload)
        except ValidationError as exc:
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="invalid_tts_playback_event",
                    message=str(exc.errors()[0]["msg"]),
                    recoverable=True,
                )
            ]

        record = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "endpoint_id": event.endpoint_id,
            "session_id": event.session_id,
            **payload.model_dump(mode="json", exclude_none=True),
            "received_at": datetime.now(UTC).isoformat(),
        }
        self._last_tts_playback = record
        self._tts_playback_history.insert(0, record)
        del self._tts_playback_history[20:]
        self._track_playback_lifecycle(event.event_type, record)
        self._last_event_type = event.event_type
        self._update_active_session_history(tts_playback=record)
        if event.session_id:
            persisted = self._session_history_store.get_session(event.session_id) if self._session_history_store else None
            if persisted is not None:
                latency_marker = {
                    "tts.playback.first_audio_frame": "first_audio_frame",
                    "tts.playback.completed": "playback_completed",
                    "tts.playback.failed": "playback_failed",
                }.get(event.event_type)
                latency = (
                    self._vad_latency_record(persisted, latency_marker, event.timestamp)
                    if latency_marker is not None
                    else None
                )
                self._upsert_persisted_session_history(
                    event.session_id,
                    tts_playback=record,
                    latency=latency,
                )
        record_voice_event(
            event.event_type,
            **{key: value for key, value in record.items() if key != "event_type"},
        )
        if event.event_type == "tts.playback.failed":
            self._record_event_diagnostic(
                code=payload.reason or "tts_playback_failed",
                endpoint_id=event.endpoint_id,
                session_id=event.session_id,
                event_type=event.event_type,
                message=payload.message or payload.reason or "Endpoint TTS playback failed",
            )
            log.warning(
                "Endpoint TTS playback failed: endpoint_id=%s session_id=%s stream_id=%s reason=%s",
                event.endpoint_id,
                event.session_id,
                payload.stream_id,
                payload.reason,
            )
        else:
            log.info(
                "Endpoint TTS playback event: endpoint_id=%s session_id=%s stream_id=%s event_type=%s",
                event.endpoint_id,
                event.session_id,
                payload.stream_id,
                event.event_type,
            )
        if event.event_type == "tts.playback.completed" and self._should_open_followup_window(event):
            return [self._open_followup_window(event.timestamp)]
        if event.event_type == "tts.playback.failed" and self._pending_session_followup and self._active_session:
            return [self._cancel_active_followup_session(reason="tts_playback_failed")]
        return [self._state_event("session.state", self._active_session)] if self._active_session else []

    def _track_playback_lifecycle(self, event_type: str, record: dict[str, object]) -> None:
        stream_id = str(record.get("stream_id") or "").strip()
        if not stream_id:
            return
        if event_type in {"tts.playback.download_started", "tts.playback.first_audio_frame"}:
            self._active_playbacks[stream_id] = record
            return
        if event_type in {"tts.playback.completed", "tts.playback.failed", "playback.stop"}:
            self._active_playbacks.pop(stream_id, None)

    def _active_playback_interrupt(self, endpoint_id: str) -> dict[str, object] | None:
        for record in self._active_playbacks.values():
            if record.get("endpoint_id") != endpoint_id:
                continue
            stream_id = str(record.get("stream_id") or "")
            audio_url = str(record.get("audio_url") or "")
            session_id = str(record.get("session_id") or "")
            if (
                stream_id.startswith("timer-alarm-")
                or "timer_alarm" in audio_url
                or session_id.startswith("timer-completed-")
            ):
                return record
        return None

    def _playback_interrupt_stop_event(self, session: VoiceSessionSnapshot) -> VoiceEventEnvelope | None:
        active_playback = self._active_playback_interrupt(session.endpoint_id)
        transcribe_audio = getattr(self._turn_pipeline, "transcribe_audio", None)
        if active_playback is None or not callable(transcribe_audio) or not self._audio_chunks:
            return None

        audio = VoiceTurnAudioSummary(
            endpoint_id=session.endpoint_id,
            session_id=session.session_id,
            chunk_count=self._chunk_count,
            sample_rate_hz=self._audio_format.sample_rate_hz if self._audio_format else None,
            encoding=self._audio_format.encoding if self._audio_format else None,
            channels=self._audio_format.channels if self._audio_format else 1,
            audio_bytes=b"".join(self._audio_chunks),
            ambient_audio_bytes=b"".join(self._ambient_audio_chunks) or None,
            endpoint_audio_metrics=self._endpoint_audio_metrics(),
        )
        transcript = transcribe_audio(audio)
        transcript_text = transcript.text or ""
        matched = not transcript.error and _is_playback_stop_phrase(transcript_text)
        interrupt_record = {
            "endpoint_id": session.endpoint_id,
            "session_id": session.session_id,
            "playback_session_id": active_playback.get("session_id"),
            "stream_id": active_playback.get("stream_id"),
            "provider_id": transcript.provider_id,
            "model": transcript.model,
            "confidence": transcript.confidence,
            "duration_ms": transcript.duration_ms,
            "text": transcript_text,
            "text_chars": len(transcript_text),
            "matched": matched,
            "error": transcript.error,
            "received_at": datetime.now(UTC).isoformat(),
        }
        self._last_playback_interrupt = interrupt_record
        record_voice_event(
            "playback.interrupt.transcript",
            **interrupt_record,
        )
        if not matched:
            return None

        request_id = f"playback_interrupt_stop_{uuid4().hex}"
        self._record_command(
            request_id=request_id,
            endpoint_id=session.endpoint_id,
            command_type="playback.stop",
            event_type="playback.stop",
        )
        return VoiceEventEnvelope(
            event_type="playback.stop",
            endpoint_id=session.endpoint_id,
            direction="backend_to_endpoint",
            session_id=str(active_playback.get("session_id") or session.session_id),
            sequence=self._next_sequence(),
            payload={
                "request_id": request_id,
                "reason": "voice_stop",
                "stream_id": active_playback.get("stream_id"),
            },
        )

    def _should_open_followup_window(self, event: VoiceEventEnvelope) -> bool:
        return (
            self._active_session is not None
            and self._pending_session_followup is not None
            and event.session_id == self._active_session.session_id
            and self._active_session.session_state == "responding"
        )

    def _open_followup_window(self, opened_at: datetime) -> VoiceEventEnvelope:
        if self._active_session is None or self._pending_session_followup is None:
            raise RuntimeError("followup_window_requires_active_session")
        self._audio_chunks = []
        self._ambient_audio_chunks = []
        self._chunk_count = 0
        followup = {
            **self._pending_session_followup,
            "state": "listening",
            "opened_at": opened_at.isoformat(),
            "listen_timeout_ms": int(FOLLOWUP_LISTEN_TIMEOUT_S * 1000),
        }
        self._pending_session_followup = followup
        self._update_active_session_history(conversation_followup=followup)
        self._set_session_state("listening")
        self._schedule_followup_timeout(
            endpoint_id=self._active_session.endpoint_id,
            session_id=self._active_session.session_id,
        )
        return self._state_event(
            "session.state",
            self._active_session,
            extra_payload={
                "followup": {
                    "needed": True,
                    "listen_timeout_ms": int(FOLLOWUP_LISTEN_TIMEOUT_S * 1000),
                    "prompt": followup.get("prompt"),
                    "state": "listening",
                }
            },
        )

    def _schedule_followup_timeout(self, *, endpoint_id: str, session_id: str) -> None:
        self._cancel_followup_timeout_task()
        self._followup_timeout_task = asyncio.create_task(self._followup_timeout(endpoint_id, session_id))

    def _cancel_followup_timeout_task(self) -> None:
        task = self._followup_timeout_task
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        if task is not None and task is not current_task and not task.done():
            task.cancel()
        self._followup_timeout_task = None

    async def _followup_timeout(self, endpoint_id: str, session_id: str) -> None:
        try:
            await asyncio.sleep(FOLLOWUP_LISTEN_TIMEOUT_S)
            if (
                self._websocket is None
                or self._active_session is None
                or self._active_session.endpoint_id != endpoint_id
                or self._active_session.session_id != session_id
                or self._pending_session_followup is None
            ):
                return
            cancelled = self._cancel_active_followup_session(reason="followup_timeout")
            await self._websocket.send_json(cancelled.model_dump(mode="json"))
        except asyncio.CancelledError:
            return

    def _cancel_active_followup_session(self, *, reason: str) -> VoiceEventEnvelope:
        if self._active_session is None:
            raise RuntimeError("followup_cancel_requires_active_session")
        self._set_session_state("cancelled")
        self._active_session.cancel_reason = reason
        cancelled = self._state_event(
            "session.cancelled",
            self._active_session,
            extra_payload={"reason": reason, "message": "canceled"},
        )
        self._persist_active_session_history(
            self._active_session,
            completion_reason=reason,
        )
        self._release_active_session_wake_stream()
        self._clear_active_session_runtime()
        return cancelled

    def _require_active_session(self, event: VoiceEventEnvelope) -> VoiceSessionSnapshot | VoiceEventEnvelope:
        if self._active_session is None:
            log.warning(
                "Voice event rejected because no active session exists: endpoint_id=%s session_id=%s event_type=%s",
                event.endpoint_id,
                event.session_id,
                event.event_type,
            )
            return self._error_event(
                endpoint_id=event.endpoint_id,
                session_id=event.session_id,
                code="no_active_session",
                message="Start a voice session before sending session control or audio events.",
                recoverable=True,
            )

        if event.session_id is not None and event.session_id != self._active_session.session_id:
            if self._can_merge_playback_interrupt_event(event):
                return self._active_session
            log.warning(
                "Voice session conflict: endpoint_id=%s active_session_id=%s incoming_session_id=%s event_type=%s",
                event.endpoint_id,
                self._active_session.session_id,
                event.session_id,
                event.event_type,
            )
            return self._error_event(
                endpoint_id=event.endpoint_id,
                session_id=event.session_id,
                code="session_conflict",
                message="The event session_id does not match the active voice session.",
                recoverable=True,
            )

        return self._active_session

    def _set_session_state(self, session_state: VoiceSessionState) -> None:
        if self._active_session is None:
            return
        current_state = self._active_session.session_state
        if current_state != session_state and not is_valid_voice_session_transition(current_state, session_state):
            self._active_session.last_error = VoiceErrorPayload(
                code="invalid_session_transition",
                message=f"Cannot move voice session from {current_state} to {session_state}.",
            )
            self._active_session.session_state = "failed"
            self._active_session.ux_state = "error"
            self._active_session.last_updated_at = datetime.now(UTC)
            return
        self._active_session.session_state = session_state
        self._active_session.ux_state = project_ux_state(session_state)
        self._active_session.last_updated_at = datetime.now(UTC)

    def _release_active_session_wake_stream(self) -> None:
        if self._active_session is None:
            return
        close_session = getattr(self._wake_detector, "close_session", None)
        if callable(close_session):
            try:
                close_session(
                    endpoint_id=self._active_session.endpoint_id,
                    session_id=self._active_session.session_id,
                )
            except Exception as exc:
                log.debug(
                    "Wake detector session cleanup failed: endpoint_id=%s session_id=%s error=%s",
                    self._active_session.endpoint_id,
                    self._active_session.session_id,
                    exc,
                )
        if self._wake_recorder is not None:
            self._wake_recorder.close_session(
                endpoint_id=self._active_session.endpoint_id,
                session_id=self._active_session.session_id,
            )

    def _state_event(
        self,
        event_type: VoiceEventType,
        session: VoiceSessionSnapshot,
        *,
        extra_payload: dict | None = None,
    ) -> VoiceEventEnvelope:
        payload = {"snapshot": session.model_dump(mode="json")}
        if extra_payload:
            payload.update(extra_payload)
        self._last_error = None
        self._last_event_type = event_type
        return VoiceEventEnvelope(
            event_type=event_type,
            endpoint_id=session.endpoint_id,
            direction="backend_to_endpoint",
            session_id=session.session_id,
            sequence=self._next_sequence(),
            payload=payload,
        )

    def _error_event(
        self,
        *,
        endpoint_id: str,
        session_id: str | None,
        code: str,
        message: str,
        recoverable: bool,
    ) -> VoiceEventEnvelope:
        payload = VoiceErrorPayload(code=code, message=message, recoverable=recoverable).model_dump(mode="json")
        self._last_event_type = "session.error"
        self._last_error = payload
        self._record_event_diagnostic(
            code=code,
            endpoint_id=endpoint_id or "unknown",
            session_id=session_id,
            event_type="session.error",
            message=message,
        )
        return VoiceEventEnvelope(
            event_type="session.error",
            endpoint_id=endpoint_id or "unknown",
            direction="backend_to_endpoint",
            session_id=session_id,
            sequence=self._next_sequence(),
            payload=payload,
        )

    def _next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence += 1
        return sequence

    def _record_event_diagnostic(
        self,
        *,
        code: str,
        endpoint_id: str,
        session_id: str | None,
        event_type: str | None,
        message: str,
    ) -> None:
        self._event_diagnostics.insert(
            0,
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "code": code,
                "endpoint_id": endpoint_id or "unknown",
                "session_id": session_id,
                "event_type": event_type,
                "message": message,
            },
        )
        del self._event_diagnostics[10:]

    def _record_command(
        self,
        *,
        request_id: str,
        endpoint_id: str,
        command_type: str,
        event_type: str,
        timeout_s: float | None = None,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        timeout_seconds = timeout_s if timeout_s is not None else self._command_timeout_s
        record: dict[str, object] = {
            "request_id": request_id,
            "endpoint_id": endpoint_id,
            "command_type": command_type,
            "event_type": event_type,
            "status": "pending",
            "terminal": False,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "timeout_at": (now.timestamp() + timeout_seconds),
        }
        self._command_records[request_id] = record
        return record

    def _update_command_from_ack(self, payload: VoiceCommandAckPayload) -> None:
        record = self._command_records.get(payload.request_id)
        if record is None:
            return
        status = "succeeded" if payload.status == "succeeded" else payload.status
        record.update(
            {
                "status": status,
                "terminal": payload.status in {"succeeded", "unsupported"},
                "message": payload.message,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    def _update_command_from_error(self, payload: VoiceCommandErrorPayload) -> None:
        record = self._command_records.get(payload.request_id)
        if record is None:
            return
        record.update(
            {
                "status": "failed",
                "terminal": True,
                "error_code": payload.code,
                "message": payload.message,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    def _expire_commands(self) -> None:
        now_ts = datetime.now(UTC).timestamp()
        for record in self._command_records.values():
            if record.get("terminal"):
                continue
            timeout_at = record.get("timeout_at")
            if isinstance(timeout_at, float) and now_ts > timeout_at:
                record.update(
                    {
                        "status": "timed_out",
                        "terminal": True,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )

    def _latest_command(self, *, endpoint_id: str, command_type: str) -> dict[str, object] | None:
        records = [
            record
            for record in self._command_records.values()
            if record.get("endpoint_id") == endpoint_id and record.get("command_type") == command_type
        ]
        if not records:
            return None
        return max(records, key=lambda record: str(record.get("created_at") or ""))

    @staticmethod
    def _safe_endpoint_id(payload: object) -> str:
        if isinstance(payload, dict):
            endpoint_id = payload.get("endpoint_id")
            if isinstance(endpoint_id, str) and endpoint_id:
                return endpoint_id
        return "unknown"

    @staticmethod
    def _safe_session_id(payload: object) -> str | None:
        if isinstance(payload, dict):
            session_id = payload.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
        return None

    @staticmethod
    def _safe_event_type(payload: object) -> str | None:
        if isinstance(payload, dict):
            event_type = payload.get("event_type")
            if isinstance(event_type, str) and event_type:
                return event_type
        return None


def _endpoint_audio_quality_summary(group: dict[str, Any]) -> dict[str, object]:
    sample_count = int(group.get("sample_count") or 0)
    ok_count = int(group.get("ok_count") or 0)
    warning_count = int(group.get("warning_count") or 0)
    snr_values = [value for value in group.get("snr_values") or [] if isinstance(value, float)]
    warning_counts = dict(sorted((group.get("warning_counts") or {}).items()))
    status_counts = dict(sorted((group.get("status_counts") or {}).items()))
    return {
        "endpoint_id": group.get("endpoint_id"),
        "sample_count": sample_count,
        "ok_count": ok_count,
        "warning_count": warning_count,
        "ok_rate": _ratio(ok_count, sample_count),
        "warning_rate": _ratio(warning_count, sample_count),
        "status_counts": status_counts,
        "warning_counts": warning_counts,
        "snr_db": _numeric_summary(snr_values),
        "latest": {
            "observed_at": group.get("latest_observed_at"),
            "session_id": group.get("latest_session_id"),
            "status": group.get("latest_status"),
            "warnings": list(group.get("latest_warnings") or []),
        },
        "recommendation": _endpoint_audio_quality_recommendation(
            sample_count=sample_count,
            ok_count=ok_count,
            warning_counts=warning_counts,
            latest_status=str(group.get("latest_status") or ""),
            latest_warnings=[str(item) for item in group.get("latest_warnings") or []],
        ),
    }


def _endpoint_audio_quality_recommendation(
    *,
    sample_count: int,
    ok_count: int,
    warning_counts: dict[str, int],
    latest_status: str,
    latest_warnings: list[str],
) -> str:
    if sample_count <= 0:
        return "no_recent_audio"
    warnings = set(warning_counts)
    latest = set(latest_warnings)
    if "clipped" in warnings or latest_status == "clipped":
        return "check_microphone_gain"
    if "low_snr" in warnings or "low_snr" in latest:
        return "reduce_background_noise_or_move_endpoint"
    if warnings.intersection({"silent", "low_level"}) or latest_status in {"silent", "low_level"}:
        return "check_microphone_distance_or_gain"
    if "short_audio" in warnings or "short_audio" in latest:
        return "check_vad_timeout_or_prompt_length"
    if _ratio(ok_count, sample_count) >= 0.8:
        return "audio_path_healthy"
    return "monitor_endpoint_audio"


def _numeric_summary(values: list[float]) -> dict[str, object]:
    if not values:
        return {"available": False, "count": 0, "avg": None, "min": None, "max": None}
    return {
        "available": True,
        "count": len(values),
        "avg": round(sum(values) / len(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 3)


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
