import base64
import json
import socket
import struct
import threading
import numpy as np

from hexevoice.config.settings import Settings
from hexevoice.voice import (
    DeterministicWakeDetector,
    OpenWakeWordWakeDetector,
    WyomingOpenWakeWordWakeDetector,
    build_wake_detector,
)
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
    assert detector._buffer_ms == 1280
    assert detector._prediction_frame_ms == 80


def test_build_wake_detector_configures_supervised_openwakeword_provider():
    detector = build_wake_detector(
        Settings(
            voice_wake_provider="supervised_openwakeword",
            voice_wake_service_host="10.0.0.5",
            voice_wake_service_port=10400,
            voice_wake_threshold=0.7,
            voice_wake_models="Hexa",
        )
    )

    assert isinstance(detector, WyomingOpenWakeWordWakeDetector)
    assert detector.status()["host"] == "10.0.0.5"
    assert detector.status()["port"] == 10400
    assert detector.status()["models"] == ["Hexa"]


def test_supervised_openwakeword_detector_streams_wyoming_audio_and_accepts_detection():
    received_events = []
    ready = threading.Event()

    def read_event(connection):
        header = b""
        while not header.endswith(b"\n"):
            header += connection.recv(1)
        event = json.loads(header.decode("utf-8"))
        payload_length = int(event.get("payload_length") or 0)
        payload = b""
        while len(payload) < payload_length:
            payload += connection.recv(payload_length - len(payload))
        event["payload"] = payload
        return event

    def server(sock):
        sock.listen(1)
        ready.set()
        connection, _ = sock.accept()
        with connection:
            for _ in range(3):
                received_events.append(read_event(connection))
            connection.sendall(
                json.dumps({"type": "detection", "data": {"name": "Hexa", "confidence": 0.91}}).encode("utf-8")
                + b"\n"
            )

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    host, port = listener.getsockname()
    thread = threading.Thread(target=server, args=(listener,), daemon=True)
    thread.start()
    ready.wait(timeout=1)

    detector = WyomingOpenWakeWordWakeDetector(host=host, port=port, threshold=0.5, wake_names=["Hexa"])
    result = detector.inspect_chunk(
        endpoint_id="esp-box-1",
        session_id="voice-session-1",
        chunk=VoiceAudioChunkPayload(
            chunk_index=4,
            audio_format={"encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1},
            payload_base64=base64.b64encode(b"\x01\x00\x02\x00").decode("ascii"),
        ),
    )
    listener.close()

    assert result.detected is True
    assert result.confidence == 0.91
    assert result.model == "Hexa"
    assert [event["type"] for event in received_events] == ["detect", "audio-start", "audio-chunk"]
    assert received_events[0]["data"]["names"] == ["Hexa"]
    assert received_events[2]["payload"] == b"\x01\x00\x02\x00"


def test_openwakeword_detector_buffers_short_audio_chunks_before_prediction():
    class FakeModel:
        def __init__(self) -> None:
            self.calls = []

        def predict(self, samples):
            self.calls.append(samples)
            return {"hey_jarvis": 0.8}

    model = FakeModel()
    detector = OpenWakeWordWakeDetector(threshold=0.5, buffer_ms=160, prediction_frame_ms=80)
    detector._model = model
    frame_20ms = struct.pack("<320h", *([1] * 320))

    for chunk_index in range(3):
        result = detector.inspect_chunk(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk=VoiceAudioChunkPayload(
                chunk_index=chunk_index,
                audio_format={"encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1},
                payload_base64=base64.b64encode(frame_20ms).decode("ascii"),
            ),
        )
        assert result.detected is False
        assert result.reason == "insufficient_audio"

    result = detector.inspect_chunk(
        endpoint_id="esp-box-1",
        session_id="voice-session-1",
        chunk=VoiceAudioChunkPayload(
            chunk_index=3,
            audio_format={"encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1},
            payload_base64=base64.b64encode(frame_20ms).decode("ascii"),
        ),
    )

    assert result.detected is True
    assert result.model == "hey_jarvis"
    assert len(model.calls) == 1
    assert len(model.calls[0]) == 1280
    assert detector.status()["active_buffers"] == 1
    assert detector.status()["last_detection"]["confidence"] == 0.8


def test_openwakeword_detector_normalizes_numpy_prediction_scalars():
    class FakeModel:
        def predict(self, samples):
            return {"Hexa": np.float32(0.8)}

    detector = OpenWakeWordWakeDetector(threshold=0.5, buffer_ms=80, prediction_frame_ms=80)
    detector._model = FakeModel()
    frame_80ms = struct.pack("<1280h", *([1] * 1280))

    result = detector.inspect_chunk(
        endpoint_id="esp-box-1",
        session_id="voice-session-1",
        chunk=VoiceAudioChunkPayload(
            chunk_index=0,
            audio_format={"encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1},
            payload_base64=base64.b64encode(frame_80ms).decode("ascii"),
        ),
    )

    assert result.detected is True
    assert isinstance(result.detected, bool)
    assert result.confidence == float(np.float32(0.8))
    assert isinstance(result.confidence, float)
