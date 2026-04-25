from datetime import UTC

import pytest
from pydantic import ValidationError

from hexevoice.voice import (
    BACKEND_TO_ENDPOINT_EVENTS,
    ENDPOINT_TO_BACKEND_EVENTS,
    VoiceAudioChunkPayload,
    VoiceAudioFormat,
    VoiceEventEnvelope,
    VoiceSessionSnapshot,
    VoiceSessionStartPayload,
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
    assert is_valid_voice_session_transition("wake_detected", "listening")
    assert is_valid_voice_session_transition("capturing", "transcribing")
    assert is_valid_voice_session_transition("transcribing", "routing_upstream")
    assert is_valid_voice_session_transition("waiting_response", "synthesizing")
    assert is_valid_voice_session_transition("playing", "completed")

    assert not is_valid_voice_session_transition("idle", "playing")
    assert not is_valid_voice_session_transition("completed", "transcribing")


def test_event_vocabularies_cover_endpoint_and_backend_message_families():
    assert ENDPOINT_TO_BACKEND_EVENTS == {
        "session.start",
        "audio.chunk",
        "audio.end",
        "session.cancel",
        "session.ping",
    }
    assert {
        "session.state",
        "transcript.partial",
        "transcript.final",
        "response.text",
        "tts.ready",
        "session.error",
    }.issubset(BACKEND_TO_ENDPOINT_EVENTS)
