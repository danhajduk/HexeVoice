from __future__ import annotations

import asyncio
import base64
import contextvars
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

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
    is_valid_voice_session_transition,
    project_ux_state,
    project_voice_state,
)
from hexevoice.voice.pipeline import TtsSynthesis, VoiceTurnAudioSummary, VoiceTurnPipeline
from hexevoice.voice.records import record_voice_event
from hexevoice.voice.micro_vad_chunks import MicroVadChunkRecordingService
from hexevoice.voice.wake import OpenWakeWordWakeDetector, WakeDetectionResult, WakeDetector
from hexevoice.voice.wake_recordings import WakeRecordingService


log = logging.getLogger(__name__)
FOLLOWUP_LISTEN_TIMEOUT_S = 10.0

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
        self._command_records: dict[str, dict[str, object]] = {}
        self._last_volume_percent_by_endpoint: dict[str, int] = {}
        self._command_timeout_s = 10.0
        self._event_diagnostics: list[dict[str, object]] = []
        self._wake_history: list[dict[str, object]] = []
        self._wake_confidence_history: list[dict[str, object]] = []

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
        active_snapshot = state.active_session.model_dump(mode="json") if state.active_session else None
        projection = project_voice_state(
            connection_active=state.connection_active,
            active_session=state.active_session,
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

    async def handle_websocket(self, websocket: WebSocket) -> None:
        await websocket.accept()
        runtime = EndpointSessionRuntime(connection_active=True, websocket=websocket)
        token = self._runtime_context.set(runtime)
        self._connection_active = True
        self._websocket = websocket
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
        sha256: str | None,
        size_bytes: int | None,
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
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                },
            )
            await runtime.websocket.send_json(event.model_dump(mode="json"))
            self._last_event_type = "ota.update"
            record = self._record_command(
                request_id=request_id,
                endpoint_id=endpoint_id,
                command_type="ota.update",
                event_type="ota.update",
            )
            return {"accepted": True, "request_id": request_id, "status": record["status"]}
        finally:
            self._runtime_context.reset(token)

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

    async def push_micro_vad_command(self, *, endpoint_id: str, pause_ms: int) -> dict:
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.micro_vad",
            command_type="endpoint.micro_vad.set",
            payload={"pause_ms": pause_ms},
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
                )
            finally:
                self._runtime_context.reset(token)
        requested_audio_url = str(audio_url or "").strip()
        spoken_text = str(text or "").strip()
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
            metadata=metadata or {},
        )
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.replay",
            command_type="endpoint.play_sound",
            request_id=f"endpoint_play_sound_{uuid4().hex}",
            payload={
                "stream_id": stream_id or (tts.stream_id if tts else None),
                "content_type": content_type or (tts.content_type if tts else "audio/wav"),
                "audio_url": requested_audio_url,
                "text": spoken_text or None,
                "source_event_id": source_event_id,
                "interaction_id": interaction_id,
                "command": "ui.play_sound",
                "metadata": metadata or {},
            },
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
                session_id=self._active_session.session_id if self._active_session else None,
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

        handlers = {
            "session.start": self._handle_session_start,
            "audio.chunk": self._handle_audio_chunk,
            "audio.end": self._handle_audio_end,
            "vad.speech_started": self._handle_vad_speech_started,
            "session.cancel": self._handle_session_cancel,
            "session.ping": self._handle_session_ping,
            "command.ack": self._handle_command_ack,
            "command.error": self._handle_command_error,
            "tts.playback.download_started": self._handle_tts_playback_event,
            "tts.playback.first_audio_frame": self._handle_tts_playback_event,
            "tts.playback.completed": self._handle_tts_playback_event,
            "tts.playback.failed": self._handle_tts_playback_event,
        }
        return handlers[event.event_type](event)

    def _handle_session_start(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        if self._active_session is not None:
            if (
                self._active_session.endpoint_id == event.endpoint_id
                and self._active_session.session_state in {"idle", "wake_detected", "listening"}
                and self._chunk_count == 0
            ):
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
            wake_source=payload.wake_source,
        )
        self._chunk_count = 0
        self._audio_chunks = []
        self._audio_format = payload.audio_format
        self._begin_active_session_history(
            session=self._active_session,
            start_payload=payload,
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

    def _handle_audio_chunk(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]

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
        if audio_bytes is not None and self._micro_vad_chunk_recorder is not None:
            self._micro_vad_chunk_recorder.capture_audio_chunk(
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                payload=payload,
                audio_bytes=audio_bytes,
                received_at=event.timestamp,
            )
        if audio_bytes is not None and session.session_state == "idle" and self._wake_recorder is not None:
            self._wake_recorder.capture_wake_chunk(
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                audio_format=payload.audio_format,
                audio_bytes=audio_bytes,
            )
        detection = (
            self._wake_detector.inspect_chunk(
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                chunk=payload,
            )
            if session.session_state == "idle"
            else WakeDetectionResult(detected=False, reason="wake_already_detected")
        )
        wake_accepted = detection.detected and session.session_state == "idle"
        self._record_wake_confidence(
            endpoint_id=event.endpoint_id,
            session_id=session.session_id,
            model=detection.model,
            confidence=detection.confidence,
            detected=detection.detected,
            accepted=wake_accepted,
            reason=detection.reason,
            source="backend_openwakeword",
            chunk_index=payload.chunk_index,
            chunk_count=self._chunk_count,
        )
        if wake_accepted:
            if self._wake_recorder is not None:
                self._wake_recorder.mark_accepted_wake(
                    endpoint_id=event.endpoint_id,
                    session_id=session.session_id,
                    model=detection.model,
                    confidence=detection.confidence,
                    source="backend_openwakeword",
                    chunk_index=payload.chunk_index,
                    chunk_count=self._chunk_count,
                )
            if self._micro_vad_chunk_recorder is not None:
                self._micro_vad_chunk_recorder.mark_session_accepted(
                    endpoint_id=event.endpoint_id,
                    session_id=session.session_id,
                )
            self._record_wake_history(
                {
                    "outcome": "accepted",
                    "detected": True,
                    "endpoint_id": event.endpoint_id,
                    "session_id": session.session_id,
                    "model": detection.model,
                    "confidence": detection.confidence,
                    "detected_at": event.timestamp.isoformat(),
                    "chunk_index": payload.chunk_index,
                    "chunk_count": self._chunk_count,
                }
            )
            self._set_active_session_wake(
                {
                    "outcome": "accepted",
                    "detected": True,
                    "model": detection.model,
                    "confidence": detection.confidence,
                    "detected_at": event.timestamp.isoformat(),
                    "source": "backend_openwakeword",
                    "chunk_index": payload.chunk_index,
                    "chunk_count": self._chunk_count,
                }
            )
            self._append_latency_point("wake_word_detected", "Wake word detected", event.timestamp)
            log.info(
                "Wake accepted: endpoint_id=%s session_id=%s model=%s confidence=%s chunk_index=%s",
                event.endpoint_id,
                session.session_id,
                detection.model,
                detection.confidence,
                payload.chunk_index,
            )
            record_voice_event(
                "wake.accepted",
                endpoint_id=event.endpoint_id,
                session_id=session.session_id,
                model=detection.model,
                confidence=detection.confidence,
                chunk_index=payload.chunk_index,
                chunk_count=self._chunk_count,
            )
            self._set_session_state("wake_detected")
            events.append(
                self._state_event(
                    "wake.accepted",
                    session,
                    extra_payload={
                        "wake": {
                            "confidence": detection.confidence,
                            "model": detection.model,
                            "source": "backend_openwakeword",
                        }
                    },
                )
            )
            self._set_session_state("listening")

        if session.session_state in {"listening", "capturing"}:
            self._set_session_state("capturing")
            if audio_bytes is not None and not wake_accepted:
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
                        "reason": detection.reason,
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
            self._set_session_state("cancelled")
            session.cancel_reason = "wake_not_detected"
            wake_status = self._wake_detector.status().get("last_detection") or {}
            self._record_wake_history(
                {
                    "outcome": "not_detected",
                    "detected": False,
                    "endpoint_id": session.endpoint_id,
                    "session_id": session.session_id,
                    "model": wake_status.get("model"),
                    "confidence": wake_status.get("confidence"),
                    "reason": wake_status.get("reason") or "wake_not_detected",
                    "chunk_count": self._chunk_count,
                }
            )
            self._set_active_session_wake(
                {
                    "outcome": "not_detected",
                    "detected": False,
                    "model": wake_status.get("model"),
                    "confidence": wake_status.get("confidence"),
                    "reason": wake_status.get("reason") or "wake_not_detected",
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
                reason=wake_status.get("reason") or "wake_not_detected",
                chunk_count=self._chunk_count,
            )
            cancelled = self._state_event("session.cancelled", session)
            self._persist_active_session_history(
                session,
                completion_reason="wake_not_detected",
            )
            self._release_active_session_wake_stream()
            self._clear_active_session_runtime()
            return [cancelled]

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
        wake_recording = self._record_accepted_wake_session(session)
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

    def status(self) -> dict:
        self._expire_commands()
        selected_runtime = self._status_runtime()
        token = self._runtime_context.set(selected_runtime)
        try:
            active_snapshot = self._active_session.model_dump(mode="json") if self._active_session else None
            state_projection = project_voice_state(
                connection_active=self._connection_active,
                active_session=self._active_session,
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
                "last_error": self._last_error,
                "last_command_ack": self._last_command_ack,
                "last_command_error": self._last_command_error,
                "commands": list(self._command_records.values()),
                "event_diagnostics": list(self._event_diagnostics),
                "wake_provider": self._wake_detector.status(),
                "wake_history": list(self._wake_history),
                "wake_confidence_history": list(self._wake_confidence_history),
                "wake_recordings": self._wake_recorder.status() if self._wake_recorder else {"enabled": False},
                "session_history": session_history,
                "turn_pipeline": self._turn_pipeline.status() if self._turn_pipeline else None,
                "supported_actions": {
                    "refresh": True,
                    "test_assistant_turn": True,
                    "stop_session": self._active_session is not None,
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
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        record: dict[str, object] = {
            "request_id": request_id,
            "endpoint_id": endpoint_id,
            "command_type": command_type,
            "event_type": event_type,
            "status": "pending",
            "terminal": False,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "timeout_at": (now.timestamp() + self._command_timeout_s),
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
