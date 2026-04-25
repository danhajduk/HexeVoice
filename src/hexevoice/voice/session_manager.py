from __future__ import annotations

from datetime import UTC, datetime
import json
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from hexevoice.voice.contracts import (
    ENDPOINT_TO_BACKEND_EVENTS,
    VoiceAudioChunkPayload,
    VoiceErrorPayload,
    VoiceEventEnvelope,
    VoiceEventType,
    VoiceSessionSnapshot,
    VoiceSessionStartPayload,
    VoiceSessionState,
    is_valid_voice_session_transition,
)
from hexevoice.voice.wake import OpenWakeWordWakeDetector, WakeDetector


class VoiceSessionManager:
    def __init__(self, *, wake_detector: WakeDetector | None = None) -> None:
        self._connection_active = False
        self._connected_endpoint_id: str | None = None
        self._active_session: VoiceSessionSnapshot | None = None
        self._chunk_count = 0
        self._sequence = 0
        self._wake_detector = wake_detector or OpenWakeWordWakeDetector()

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
        try:
            while True:
                raw_message = await websocket.receive_text()
                for event in self._handle_raw_message(raw_message):
                    await websocket.send_json(event.model_dump(mode="json"))
        except WebSocketDisconnect:
            pass
        finally:
            self._connection_active = False
            self._connected_endpoint_id = None
            self._active_session = None
            self._chunk_count = 0

    def _handle_raw_message(self, raw_message: str) -> list[VoiceEventEnvelope]:
        try:
            raw_payload = json.loads(raw_message)
        except json.JSONDecodeError:
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
            return [
                self._error_event(
                    endpoint_id=self._safe_endpoint_id(raw_payload),
                    session_id=self._safe_session_id(raw_payload),
                    code="invalid_event_envelope",
                    message=str(exc.errors()[0]["msg"]),
                    recoverable=True,
                )
            ]

        if event.direction != "endpoint_to_backend":
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
            return [
                self._error_event(
                    endpoint_id=event.endpoint_id,
                    session_id=event.session_id,
                    code="endpoint_conflict",
                    message="This WebSocket is already bound to another endpoint.",
                    recoverable=False,
                )
            ]

        self._connected_endpoint_id = event.endpoint_id

        handlers = {
            "session.start": self._handle_session_start,
            "audio.chunk": self._handle_audio_chunk,
            "audio.end": self._handle_audio_end,
            "session.cancel": self._handle_session_cancel,
            "session.ping": self._handle_session_ping,
        }
        return handlers[event.event_type](event)

    def _handle_session_start(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        if self._active_session is not None:
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
        return [self._state_event("session.state", self._active_session)]

    def _handle_audio_chunk(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]

        try:
            payload = VoiceAudioChunkPayload.model_validate(event.payload)
        except ValidationError as exc:
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
        events: list[VoiceEventEnvelope] = []
        detection = self._wake_detector.inspect_chunk(
            endpoint_id=event.endpoint_id,
            session_id=session.session_id,
            chunk=payload,
        )
        if detection.detected and session.session_state == "idle":
            self._set_session_state("wake_detected", ux_state="wake_detected")
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
            self._set_session_state("listening", ux_state="listening")

        if session.session_state in {"listening", "capturing"}:
            self._set_session_state("capturing", ux_state="listening")

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
            self._set_session_state("cancelled", ux_state="idle")
            session.cancel_reason = "wake_not_detected"
            cancelled = self._state_event("session.cancelled", session)
            self._active_session = None
            self._chunk_count = 0
            return [cancelled]

        if session.session_state == "wake_detected":
            self._set_session_state("listening", ux_state="listening")

        self._set_session_state("transcribing", ux_state="thinking")
        self._set_session_state("local_command", ux_state="thinking")
        self._set_session_state("completed", ux_state="idle")
        event = self._state_event(
            "session.completed",
            session,
            extra_payload={"completion_reason": "audio_received", "chunk_count": self._chunk_count},
        )
        self._active_session = None
        self._chunk_count = 0
        return [event]

    def _handle_session_cancel(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]

        self._set_session_state("cancelled", ux_state="idle")
        session.cancel_reason = str(event.payload.get("reason") or "endpoint_cancelled")
        cancelled = self._state_event("session.cancelled", session)
        self._active_session = None
        self._chunk_count = 0
        return [cancelled]

    def _handle_session_ping(self, event: VoiceEventEnvelope) -> list[VoiceEventEnvelope]:
        session = self._require_active_session(event)
        if isinstance(session, VoiceEventEnvelope):
            return [session]
        return [self._state_event("session.state", session)]

    def _require_active_session(self, event: VoiceEventEnvelope) -> VoiceSessionSnapshot | VoiceEventEnvelope:
        if self._active_session is None:
            return self._error_event(
                endpoint_id=event.endpoint_id,
                session_id=event.session_id,
                code="no_active_session",
                message="Start a voice session before sending session control or audio events.",
                recoverable=True,
            )

        if event.session_id is not None and event.session_id != self._active_session.session_id:
            return self._error_event(
                endpoint_id=event.endpoint_id,
                session_id=event.session_id,
                code="session_conflict",
                message="The event session_id does not match the active voice session.",
                recoverable=True,
            )

        return self._active_session

    def _set_session_state(self, session_state: VoiceSessionState, *, ux_state: str) -> None:
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
        self._active_session.ux_state = ux_state
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
        return VoiceEventEnvelope(
            event_type="session.error",
            endpoint_id=endpoint_id or "unknown",
            direction="backend_to_endpoint",
            session_id=session_id,
            sequence=self._next_sequence(),
            payload=VoiceErrorPayload(code=code, message=message, recoverable=recoverable).model_dump(mode="json"),
        )

    def _next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence += 1
        return sequence

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
