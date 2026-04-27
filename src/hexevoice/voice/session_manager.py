from __future__ import annotations

import base64
from datetime import UTC, datetime
import json
import logging
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

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
    VoiceTtsReadyPayload,
    is_valid_voice_session_transition,
    project_ux_state,
    project_voice_state,
)
from hexevoice.voice.pipeline import VoiceTurnAudioSummary, VoiceTurnPipeline
from hexevoice.voice.wake import OpenWakeWordWakeDetector, WakeDetectionResult, WakeDetector


log = logging.getLogger(__name__)


class VoiceSessionManager:
    def __init__(
        self,
        *,
        wake_detector: WakeDetector | None = None,
        turn_pipeline: VoiceTurnPipeline | None = None,
    ) -> None:
        self._connection_active = False
        self._websocket: WebSocket | None = None
        self._connected_endpoint_id: str | None = None
        self._active_session: VoiceSessionSnapshot | None = None
        self._chunk_count = 0
        self._audio_chunks: list[bytes] = []
        self._audio_format = None
        self._sequence = 0
        self._wake_detector = wake_detector or OpenWakeWordWakeDetector()
        self._turn_pipeline = turn_pipeline
        self._last_transcript: str | None = None
        self._last_response: str | None = None
        self._last_transcript_metadata: dict | None = None
        self._last_error: dict | None = None
        self._last_tts: dict | None = None
        self._last_assistant: dict | None = None
        self._last_turn_timings: dict | None = None
        self._last_event_type: str | None = None
        self._last_command_ack: dict | None = None
        self._last_command_error: dict | None = None
        self._command_records: dict[str, dict[str, object]] = {}
        self._last_volume_percent_by_endpoint: dict[str, int] = {}
        self._command_timeout_s = 10.0
        self._event_diagnostics: list[dict[str, object]] = []
        self._wake_history: list[dict[str, object]] = []

    async def handle_websocket(self, websocket: WebSocket) -> None:
        await websocket.accept()
        if self._connection_active:
            await websocket.send_json(
                self._error_event(
                    endpoint_id="unknown",
                    session_id=None,
                    code="endpoint_busy",
                    message="Only one voice endpoint connection is supported for the MVP.",
                    recoverable=False,
                ).model_dump(mode="json")
            )
            await websocket.close(code=1008)
            return

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
            self._websocket = None
            self._connection_active = False
            self._connected_endpoint_id = None
            self._active_session = None
            self._chunk_count = 0
            self._audio_chunks = []
            self._audio_format = None
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
        if not self._connection_active or self._websocket is None:
            log.warning("OTA push rejected: endpoint_id=%s reason=endpoint_not_connected", endpoint_id)
            return {"accepted": False, "reason": "endpoint_not_connected"}
        if self._connected_endpoint_id is not None and endpoint_id != self._connected_endpoint_id:
            log.warning(
                "OTA push rejected: endpoint_id=%s connected_endpoint_id=%s reason=endpoint_mismatch",
                endpoint_id,
                self._connected_endpoint_id,
            )
            return {"accepted": False, "reason": "endpoint_mismatch"}

        log.info(
            "OTA push accepted for endpoint: endpoint_id=%s version=%s size_bytes=%s",
            endpoint_id,
            version,
            size_bytes,
        )
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
        await self._websocket.send_json(event.model_dump(mode="json"))
        self._last_event_type = "ota.update"
        record = self._record_command(
            request_id=request_id,
            endpoint_id=endpoint_id,
            command_type="ota.update",
            event_type="ota.update",
        )
        return {"accepted": True, "request_id": request_id, "status": record["status"]}

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

    async def push_cancel_command(self, *, endpoint_id: str, reason: str = "operator_cancelled") -> dict:
        result = await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.cancel",
            command_type="endpoint.cancel",
            payload={"reason": reason},
        )
        if result.get("accepted") and self._active_session is not None:
            self._set_session_state("cancelled")
            self._active_session.cancel_reason = reason
            self._active_session = None
            self._chunk_count = 0
            self._audio_chunks = []
            self._audio_format = None
        return result

    async def push_replay_command(self, *, endpoint_id: str) -> dict:
        if self._last_transcript and self._turn_pipeline is not None:
            replay_text = f"I heard {self._last_transcript}"
            session_id = self._active_session.session_id if self._active_session else f"{endpoint_id}-replay"
            tts = self._turn_pipeline.synthesize_reply(
                endpoint_id=endpoint_id,
                session_id=session_id,
                text=replay_text,
            )
            self._last_response = replay_text
            self._last_tts = {
                "content_type": tts.content_type,
                "stream_id": tts.stream_id,
                "audio_url": tts.audio_url,
                "provider_id": tts.provider_id,
                "error": tts.error,
            }
            if tts.error:
                return {"accepted": False, "reason": tts.error, "status": "failed"}
        if not self._last_tts or not self._last_tts.get("stream_id"):
            return {"accepted": False, "reason": "no_replay_available", "status": "failed"}
        return await self._push_endpoint_command(
            endpoint_id=endpoint_id,
            event_type="endpoint.replay",
            command_type="endpoint.replay",
            payload={
                "stream_id": self._last_tts.get("stream_id"),
                "content_type": self._last_tts.get("content_type"),
                "audio_url": self._last_tts.get("audio_url"),
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
        if not self._connection_active or self._websocket is None:
            log.warning("Endpoint command rejected: endpoint_id=%s command_type=%s reason=endpoint_not_connected", endpoint_id, command_type)
            return {"accepted": False, "reason": "endpoint_not_connected", "status": "failed"}
        if self._connected_endpoint_id is not None and endpoint_id != self._connected_endpoint_id:
            log.warning(
                "Endpoint command rejected: endpoint_id=%s connected_endpoint_id=%s command_type=%s reason=endpoint_mismatch",
                endpoint_id,
                self._connected_endpoint_id,
                command_type,
            )
            return {"accepted": False, "reason": "endpoint_mismatch", "status": "failed"}

        request_id = request_id or f"cmd_{uuid4().hex}"
        event = VoiceEventEnvelope(
            event_type=event_type,
            endpoint_id=endpoint_id,
            direction="backend_to_endpoint",
            session_id=self._active_session.session_id if self._active_session else None,
            sequence=self._next_sequence(),
            payload={"request_id": request_id, **payload},
        )
        await self._websocket.send_json(event.model_dump(mode="json"))
        self._last_event_type = event_type
        record = self._record_command(
            request_id=request_id,
            endpoint_id=endpoint_id,
            command_type=command_type,
            event_type=event_type,
        )
        return {"accepted": True, "request_id": request_id, "status": record["status"]}

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
            "session.cancel": self._handle_session_cancel,
            "session.ping": self._handle_session_ping,
            "command.ack": self._handle_command_ack,
            "command.error": self._handle_command_error,
        }
        return handlers[event.event_type](event)

    def _handle_session_start(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        if self._active_session is not None:
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
        log.info(
            "Voice session started: endpoint_id=%s session_id=%s wake_source=%s sample_rate_hz=%s",
            event.endpoint_id,
            session_id,
            payload.wake_source,
            payload.audio_format.sample_rate_hz,
        )
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
        if wake_accepted:
            self._record_wake_history(
                {
                    "outcome": "accepted",
                    "detected": True,
                    "endpoint_id": event.endpoint_id,
                    "session_id": session.session_id,
                    "model": detection.model,
                    "confidence": detection.confidence,
                    "chunk_index": payload.chunk_index,
                    "chunk_count": self._chunk_count,
                }
            )
            log.info(
                "Wake accepted: endpoint_id=%s session_id=%s model=%s confidence=%s chunk_index=%s",
                event.endpoint_id,
                session.session_id,
                detection.model,
                detection.confidence,
                payload.chunk_index,
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
            log.info(
                "Voice session cancelled before wake: endpoint_id=%s session_id=%s chunks=%s",
                session.endpoint_id,
                session.session_id,
                self._chunk_count,
            )
            cancelled = self._state_event("session.cancelled", session)
            self._active_session = None
            self._chunk_count = 0
            self._audio_chunks = []
            self._audio_format = None
            return [cancelled]

        if session.session_state == "wake_detected":
            self._set_session_state("listening")

        self._set_session_state("transcribing")
        events: list[VoiceEventEnvelope] = []
        if self._turn_pipeline is not None:
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
            self._last_transcript_metadata = {
                "provider_id": turn.transcript.provider_id,
                "model": turn.transcript.model,
                "confidence": turn.transcript.confidence,
                "duration_ms": turn.transcript.duration_ms,
                "text_chars": len(turn.transcript.text or ""),
                "error": turn.transcript.error,
            }
            self._last_turn_timings = {
                "stt_ms": turn.timings.stt_ms,
                "assistant_ms": turn.timings.assistant_ms,
                "tts_ms": turn.timings.tts_ms,
                "total_ms": turn.timings.total_ms,
            }
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
            if turn.transcript.error:
                error = self._error_event(
                    endpoint_id=session.endpoint_id,
                    session_id=session.session_id,
                    code="stt_failed",
                    message=turn.transcript.error,
                    recoverable=True,
                )
                self._set_session_state("failed")
                self._active_session = None
                self._chunk_count = 0
                self._audio_chunks = []
                self._audio_format = None
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
            }
            self._set_session_state("responding")
            self._last_tts = {
                "content_type": turn.tts.content_type,
                "stream_id": turn.tts.stream_id,
                "audio_url": turn.tts.audio_url,
                "provider_id": turn.tts.provider_id,
                "error": turn.tts.error,
            }
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
                self._active_session = None
                self._chunk_count = 0
                self._audio_chunks = []
                self._audio_format = None
                return events
            events.append(
                self._state_event(
                    "tts.ready",
                    session,
                    extra_payload=VoiceTtsReadyPayload(
                        content_type=turn.tts.content_type,
                        stream_id=turn.tts.stream_id,
                        audio_url=turn.tts.audio_url,
                    ).model_dump(mode="json"),
                )
            )
        else:
            self._set_session_state("local_command")
            self._set_session_state("responding")
        self._set_session_state("completed")
        events.append(
            self._state_event(
                "session.completed",
                session,
                extra_payload={"completion_reason": "turn_completed", "chunk_count": self._chunk_count},
            )
        )
        self._active_session = None
        self._chunk_count = 0
        self._audio_chunks = []
        self._audio_format = None
        return events

    def status(self) -> dict:
        self._expire_commands()
        active_snapshot = self._active_session.model_dump(mode="json") if self._active_session else None
        state_projection = project_voice_state(
            connection_active=self._connection_active,
            active_session=self._active_session,
        ).model_dump(mode="json")
        return {
            "endpoint_id": self._connected_endpoint_id,
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
            "last_error": self._last_error,
            "last_command_ack": self._last_command_ack,
            "last_command_error": self._last_command_error,
            "commands": list(self._command_records.values()),
            "event_diagnostics": list(self._event_diagnostics),
            "wake_provider": self._wake_detector.status(),
            "wake_history": list(self._wake_history),
            "turn_pipeline": self._turn_pipeline.status() if self._turn_pipeline else None,
            "supported_actions": {
                "refresh": True,
                "test_assistant_turn": True,
                "stop_session": self._active_session is not None,
                "replay_response": self._connection_active and self._last_tts is not None,
                "mute_endpoint": self._connection_active,
                "set_volume": self._connection_active,
                "send_media": self._connection_active,
                "reconnect": False,
            },
        }

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

    def cancel_from_operator(self, *, reason: str = "operator_cancelled") -> dict:
        if self._active_session is None:
            return {"accepted": False, "reason": "no_active_session", "status": self.status()}

        self._set_session_state("cancelled")
        self._active_session.cancel_reason = reason
        self._active_session = None
        self._chunk_count = 0
        self._audio_chunks = []
        self._audio_format = None
        return {"accepted": True, "reason": reason, "status": self.status()}

    def _handle_session_cancel(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]

        self._set_session_state("cancelled")
        session.cancel_reason = str(event.payload.get("reason") or "endpoint_cancelled")
        cancelled = self._state_event("session.cancelled", session)
        self._active_session = None
        self._chunk_count = 0
        self._audio_chunks = []
        self._audio_format = None
        return [cancelled]

    def _handle_session_ping(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]
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
