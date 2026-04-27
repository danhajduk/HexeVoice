from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


VOICE_EVENT_SCHEMA_VERSION = "hexevoice.voice.event.v1"

VoiceEndpointConnectionState = Literal["offline", "connecting", "connected", "degraded"]
VoiceEndpointUxState = Literal[
    "idle",
    "wake_armed",
    "wake_detected",
    "listening",
    "thinking",
    "speaking",
    "muted",
    "error",
]
VoiceSessionState = Literal[
    "idle",
    "wake_detected",
    "listening",
    "capturing",
    "transcribing",
    "local_command",
    "routing",
    "responding",
    "completed",
    "cancelled",
    "failed",
]
VoiceEventDirection = Literal["endpoint_to_backend", "backend_to_endpoint", "internal"]
VoiceEventType = Literal[
    "session.start",
    "audio.chunk",
    "audio.end",
    "session.cancel",
    "session.ping",
    "session.state",
    "wake.accepted",
    "capture.started",
    "capture.stopped",
    "transcript.partial",
    "transcript.final",
    "command.result",
    "command.handled",
    "upstream.pending",
    "upstream.response",
    "response.text",
    "tts.ready",
    "playback.start",
    "playback.stop",
    "session.complete",
    "session.completed",
    "session.cancelled",
    "session.error",
    "ota.update",
    "endpoint.volume",
    "endpoint.mute",
    "endpoint.cancel",
    "endpoint.replay",
    "endpoint.media.transfer",
    "command.ack",
    "command.error",
]

ENDPOINT_TO_BACKEND_EVENTS: frozenset[str] = frozenset(
    {
        "session.start",
        "audio.chunk",
        "audio.end",
        "session.cancel",
        "session.ping",
        "command.ack",
        "command.error",
    }
)

BACKEND_TO_ENDPOINT_EVENTS: frozenset[str] = frozenset(
    {
        "session.state",
        "wake.accepted",
        "capture.started",
        "capture.stopped",
        "transcript.partial",
        "transcript.final",
        "command.result",
        "command.handled",
        "upstream.pending",
        "upstream.response",
        "response.text",
        "tts.ready",
        "playback.start",
        "playback.stop",
        "session.complete",
        "session.completed",
        "session.cancelled",
        "session.error",
        "ota.update",
        "endpoint.volume",
        "endpoint.mute",
        "endpoint.cancel",
        "endpoint.replay",
        "endpoint.media.transfer",
    }
)

VOICE_SESSION_ALLOWED_TRANSITIONS: dict[VoiceSessionState, frozenset[VoiceSessionState]] = {
    "idle": frozenset({"wake_detected", "listening", "capturing", "cancelled", "failed"}),
    "wake_detected": frozenset({"listening", "cancelled", "failed"}),
    "listening": frozenset({"capturing", "cancelled", "failed"}),
    "capturing": frozenset({"transcribing", "cancelled", "failed"}),
    "transcribing": frozenset({"local_command", "routing", "cancelled", "failed"}),
    "local_command": frozenset({"responding", "completed", "cancelled", "failed"}),
    "routing": frozenset({"responding", "cancelled", "failed"}),
    "responding": frozenset({"completed", "cancelled", "failed"}),
    "completed": frozenset({"idle"}),
    "cancelled": frozenset({"idle"}),
    "failed": frozenset({"idle"}),
}

VOICE_SESSION_UX_PROJECTION: dict[VoiceSessionState, VoiceEndpointUxState] = {
    "idle": "wake_armed",
    "wake_detected": "wake_detected",
    "listening": "listening",
    "capturing": "listening",
    "transcribing": "thinking",
    "local_command": "thinking",
    "routing": "thinking",
    "responding": "speaking",
    "completed": "idle",
    "cancelled": "idle",
    "failed": "error",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class VoiceAudioFormat(BaseModel):
    encoding: Literal["pcm_s16le", "opus", "wav"] = "pcm_s16le"
    sample_rate_hz: int = Field(default=16000, ge=8000)
    channels: int = Field(default=1, ge=1, le=2)


class VoiceSessionStartPayload(BaseModel):
    audio_format: VoiceAudioFormat = Field(default_factory=VoiceAudioFormat)
    firmware_version: str | None = None
    wake_source: Literal["openwakeword", "button", "manual", "unknown"] = "unknown"


class VoiceAudioChunkPayload(BaseModel):
    chunk_index: int = Field(ge=0)
    audio_format: VoiceAudioFormat = Field(default_factory=VoiceAudioFormat)
    payload_base64: str | None = None
    is_final: bool = False


class VoiceTranscriptPayload(BaseModel):
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)


class VoiceResponseTextPayload(BaseModel):
    text: str


class VoiceTtsReadyPayload(BaseModel):
    content_type: str = "audio/wav"
    stream_id: str | None = None
    audio_url: str | None = None


class VoiceErrorPayload(BaseModel):
    code: str
    message: str
    recoverable: bool = False


class VoiceCommandAckPayload(BaseModel):
    request_id: str = Field(min_length=1)
    command_type: str = Field(min_length=1)
    status: Literal["accepted", "started", "succeeded", "unsupported"]
    message: str | None = None


class VoiceCommandErrorPayload(BaseModel):
    request_id: str = Field(min_length=1)
    command_type: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str
    recoverable: bool = False


def _event_id() -> str:
    return f"evt_{uuid4().hex}"


class VoiceEventEnvelope(BaseModel):
    event_type: VoiceEventType
    event_id: str = Field(default_factory=_event_id, min_length=1)
    schema_version: Literal["hexevoice.voice.event.v1"] = VOICE_EVENT_SCHEMA_VERSION
    endpoint_id: str = Field(min_length=1)
    direction: VoiceEventDirection
    session_id: str | None = None
    sequence: int | None = Field(default=None, ge=0)
    timestamp: datetime = Field(default_factory=_utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)


class VoiceSessionSnapshot(BaseModel):
    session_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    session_state: VoiceSessionState = "idle"
    connection_state: VoiceEndpointConnectionState = "offline"
    ux_state: VoiceEndpointUxState = "idle"
    started_at: datetime = Field(default_factory=_utcnow)
    last_updated_at: datetime = Field(default_factory=_utcnow)
    wake_source: Literal["openwakeword", "button", "manual", "unknown"] = "unknown"
    cancel_reason: str | None = None
    completion_reason: str | None = None
    last_error: VoiceErrorPayload | None = None


class VoiceStateProjection(BaseModel):
    connection_state: VoiceEndpointConnectionState
    ux_state: VoiceEndpointUxState
    session_state: VoiceSessionState | None = None
    transport_health: Literal["online", "offline", "degraded"]


def project_ux_state(session_state: VoiceSessionState) -> VoiceEndpointUxState:
    return VOICE_SESSION_UX_PROJECTION[session_state]


def project_voice_state(
    *,
    connection_active: bool,
    active_session: VoiceSessionSnapshot | None,
) -> VoiceStateProjection:
    connection_state: VoiceEndpointConnectionState = "connected" if connection_active else "offline"
    transport_health: Literal["online", "offline", "degraded"] = "online" if connection_active else "offline"
    if active_session is None:
        return VoiceStateProjection(
            connection_state=connection_state,
            ux_state="idle",
            session_state=None,
            transport_health=transport_health,
        )
    return VoiceStateProjection(
        connection_state=connection_state,
        ux_state=active_session.ux_state,
        session_state=active_session.session_state,
        transport_health=transport_health,
    )


def is_valid_voice_session_transition(
    current: VoiceSessionState,
    next_state: VoiceSessionState,
) -> bool:
    return next_state in VOICE_SESSION_ALLOWED_TRANSITIONS[current]
