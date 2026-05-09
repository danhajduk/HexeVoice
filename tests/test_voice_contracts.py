from datetime import UTC
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hexevoice.voice import (
    BACKEND_TO_ENDPOINT_EVENTS,
    ENDPOINT_TO_BACKEND_EVENTS,
    VOICE_EVENT_SCHEMA_VERSION,
    VoiceCommandAckPayload,
    VoiceCommandErrorPayload,
    VoiceAudioChunkPayload,
    VoiceAudioFormat,
    VoiceEventEnvelope,
    VoiceSessionSnapshot,
    VoiceSessionStartPayload,
    project_ux_state,
    project_voice_state,
    is_valid_voice_session_transition,
)


def test_voice_event_envelope_accepts_session_start():
    payload = VoiceSessionStartPayload(
        audio_format=VoiceAudioFormat(encoding="pcm_s16le", sample_rate_hz=16000, channels=1),
        firmware_version="0.1.0",
        wake_source="openwakeword",
    )

    envelope = VoiceEventEnvelope(
        event_type="session.start",
        endpoint_id="esp-box-1",
        direction="endpoint_to_backend",
        session_id="voice-session-1",
        sequence=0,
        payload=payload.model_dump(),
    )

    assert envelope.event_type == "session.start"
    assert envelope.event_id.startswith("evt_")
    assert envelope.schema_version == VOICE_EVENT_SCHEMA_VERSION
    assert envelope.endpoint_id == "esp-box-1"
    assert envelope.timestamp.tzinfo is UTC
    assert envelope.payload["audio_format"]["sample_rate_hz"] == 16000


def test_voice_event_envelope_rejects_unknown_event_types():
    with pytest.raises(ValidationError):
        VoiceEventEnvelope(
            event_type="audio.blob",
            endpoint_id="esp-box-1",
            direction="endpoint_to_backend",
        )


def test_voice_event_envelope_rejects_unknown_schema_versions():
    with pytest.raises(ValidationError):
        VoiceEventEnvelope(
            event_type="session.start",
            endpoint_id="esp-box-1",
            direction="endpoint_to_backend",
            schema_version="hexevoice.voice.event.v2",
        )


def test_command_acknowledgement_payload_is_structured():
    ack = VoiceCommandAckPayload(
        request_id="cmd-1",
        command_type="endpoint.volume",
        status="succeeded",
    )
    error = VoiceCommandErrorPayload(
        request_id="cmd-2",
        command_type="endpoint.volume",
        code="unsupported_command",
        message="Volume control is not supported",
        recoverable=True,
    )

    assert ack.status == "succeeded"
    assert error.code == "unsupported_command"


def test_audio_chunk_payload_keeps_transport_metadata_separate_from_audio_processing():
    chunk = VoiceAudioChunkPayload(
        chunk_index=4,
        audio_format={"encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1},
        payload_base64="AAECAw==",
    )

    assert chunk.chunk_index == 4
    assert chunk.audio_format.sample_rate_hz == 16000
    assert chunk.payload_base64 == "AAECAw=="


def test_session_snapshot_keeps_backend_endpoint_and_ux_states_separate():
    snapshot = VoiceSessionSnapshot(
        session_id="voice-session-1",
        endpoint_id="esp-box-1",
        session_state="transcribing",
        connection_state="connected",
        ux_state="thinking",
    )

    assert snapshot.session_state == "transcribing"
    assert snapshot.connection_state == "connected"
    assert snapshot.ux_state == "thinking"


def test_single_endpoint_session_transition_contract():
    assert is_valid_voice_session_transition("idle", "wake_detected")
    assert is_valid_voice_session_transition("idle", "listening")
    assert is_valid_voice_session_transition("wake_detected", "listening")
    assert is_valid_voice_session_transition("listening", "capturing")
    assert is_valid_voice_session_transition("capturing", "transcribing")
    assert is_valid_voice_session_transition("transcribing", "routing")
    assert is_valid_voice_session_transition("routing", "responding")
    assert is_valid_voice_session_transition("responding", "completed")

    assert not is_valid_voice_session_transition("idle", "responding")
    assert not is_valid_voice_session_transition("completed", "transcribing")


def test_voice_state_projection_maps_session_connection_and_ux_families():
    snapshot = VoiceSessionSnapshot(
        session_id="voice-session-1",
        endpoint_id="esp-box-1",
        session_state="capturing",
        connection_state="connected",
        ux_state=project_ux_state("capturing"),
    )

    active_projection = project_voice_state(connection_active=True, active_session=snapshot)
    idle_projection = project_voice_state(connection_active=True, active_session=None)
    offline_projection = project_voice_state(connection_active=False, active_session=None)

    assert active_projection.connection_state == "connected"
    assert active_projection.transport_health == "online"
    assert active_projection.session_state == "capturing"
    assert active_projection.ux_state == "listening"
    assert idle_projection.session_state is None
    assert idle_projection.ux_state == "idle"
    assert offline_projection.connection_state == "offline"
    assert offline_projection.transport_health == "offline"


def test_event_vocabularies_cover_endpoint_and_backend_message_families():
    assert ENDPOINT_TO_BACKEND_EVENTS == {
        "session.start",
        "audio.chunk",
        "audio.end",
        "session.cancel",
        "session.ping",
        "command.ack",
        "command.error",
    }
    assert {
        "session.state",
        "transcript.partial",
        "transcript.final",
        "response.text",
        "tts.ready",
        "session.error",
    }.issubset(BACKEND_TO_ENDPOINT_EVENTS)


@pytest.mark.parametrize(
    "filename",
    [
        "endpoint-session-start.example.json",
        "backend-volume-command.example.json",
        "endpoint-command-ack.example.json",
        "endpoint-command-error.example.json",
    ],
)
def test_documented_voice_event_json_examples_match_contract(filename):
    docs_dir = Path(__file__).resolve().parents[1] / "docs" / "voice-event-envelope"
    payload = json.loads((docs_dir / filename).read_text())

    envelope = VoiceEventEnvelope.model_validate(payload)

    assert envelope.schema_version == VOICE_EVENT_SCHEMA_VERSION
    assert envelope.event_id


def test_documented_voice_event_json_schema_is_valid_json():
    docs_dir = Path(__file__).resolve().parents[1] / "docs" / "voice-event-envelope"
    schema = json.loads((docs_dir / "envelope.schema.json").read_text())

    assert schema["properties"]["schema_version"]["const"] == VOICE_EVENT_SCHEMA_VERSION
