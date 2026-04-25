import base64

from hexevoice.voice import DeterministicWakeDetector
from hexevoice.voice.contracts import VoiceAudioChunkPayload


def test_deterministic_wake_detector_can_trigger_by_chunk_index():
    detector = DeterministicWakeDetector(detect_on_chunk_index=2)
    chunk = VoiceAudioChunkPayload(chunk_index=2, audio_format={"sample_rate_hz": 16000})

    result = detector.inspect_chunk(endpoint_id="esp-box-1", session_id="voice-session-1", chunk=chunk)

    assert result.detected is True
    assert result.confidence == 1.0
    assert result.model == "deterministic"


def test_deterministic_wake_detector_can_trigger_by_audio_marker_without_persisting_audio():
    detector = DeterministicWakeDetector()
    chunk = VoiceAudioChunkPayload(
        chunk_index=0,
        audio_format={"sample_rate_hz": 16000},
        payload_base64=base64.b64encode(b"noise WAKE noise").decode("ascii"),
    )

    result = detector.inspect_chunk(endpoint_id="esp-box-1", session_id="voice-session-1", chunk=chunk)

    assert result.detected is True
    assert result.model == "deterministic"
