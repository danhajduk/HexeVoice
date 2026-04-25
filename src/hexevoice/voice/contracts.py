from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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
    "routing_upstream",
    "waiting_response",
    "synthesizing",
    "playing",
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
]

ENDPOINT_TO_BACKEND_EVENTS: frozenset[str] = frozenset(
    {
        "session.start",
        "audio.chunk",
        "audio.end",
        "session.cancel",
        "session.ping",
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
    }
)

VOICE_SESSION_ALLOWED_TRANSITIONS: dict[VoiceSessionState, frozenset[VoiceSessionState]] = {
    "idle": frozenset({"wake_detected", "listening", "capturing", "cancelled", "failed"}),
    "wake_detected": frozenset({"listening", "cancelled", "failed"}),
    "listening": frozenset({"capturing", "cancelled", "failed"}),
    "capturing": frozenset({"transcribing", "cancelled", "failed"}),
    "transcribing": frozenset({"local_command", "routing_upstream", "cancelled", "failed"}),
    "local_command": frozenset({"synthesizing", "completed", "cancelled", "failed"}),
    "routing_upstream": frozenset({"waiting_response", "cancelled", "failed"}),
    "waiting_response": frozenset({"synthesizing", "cancelled", "failed"}),
    "synthesizing": frozenset({"playing", "cancelled", "failed"}),
    "playing": frozenset({"completed", "cancelled", "failed"}),
    "completed": frozenset({"idle"}),
    "cancelled": frozenset({"idle"}),
    "failed": frozenset({"idle"}),
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


class VoiceEventEnvelope(BaseModel):
    event_type: VoiceEventType
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


def is_valid_voice_session_transition(
    current: VoiceSessionState,
    next_state: VoiceSessionState,
) -> bool:
    return next_state in VOICE_SESSION_ALLOWED_TRANSITIONS[current]
