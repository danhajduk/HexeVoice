import base64

from hexevoice.config.settings import Settings
from hexevoice.voice import DeterministicWakeDetector, OpenWakeWordWakeDetector, build_wake_detector
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


def test_build_wake_detector_uses_deterministic_provider_for_development():
    detector = build_wake_detector(Settings(voice_wake_provider="deterministic"))

    assert isinstance(detector, DeterministicWakeDetector)


def test_build_wake_detector_configures_openwakeword_provider():
    detector = build_wake_detector(
        Settings(
            voice_wake_provider="openwakeword",
            voice_wake_threshold=0.7,
            voice_wake_models="hey_jarvis.onnx, /models/hexe.tflite",
            voice_wake_auto_download_models=True,
            voice_wake_enable_speex_noise_suppression=True,
            voice_wake_vad_threshold=0.3,
        )
    )

    assert isinstance(detector, OpenWakeWordWakeDetector)
    assert detector._threshold == 0.7
    assert detector._wakeword_models == ["hey_jarvis.onnx", "/models/hexe.tflite"]
    assert detector._auto_download_models is True
    assert detector._enable_speex_noise_suppression is True
    assert detector._vad_threshold == 0.3
