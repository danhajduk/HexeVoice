import base64

from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from stt.adapters import SpeechTranscript
import stt.service as stt_service


def test_stt_service_transcribes_with_external_faster_whisper(monkeypatch, tmp_path):
    captured = {}

    class FakeAdapter:
        def __init__(self, *, model_name, device, compute_type, temp_dir):
            captured["model_name"] = model_name
            captured["device"] = device
            captured["compute_type"] = compute_type
            captured["temp_dir"] = temp_dir

        def status(self):
            return {"healthy": True, "model": captured["model_name"], "loaded": False}

        def preload(self):
            return {"loaded": True, "model": captured["model_name"], "duration_ms": 1.2}

        def transcribe(self, audio):
            captured["audio"] = audio
            return SpeechTranscript(
                text="what time",
                provider_id="faster_whisper",
                model=captured["model_name"],
                duration_ms=5.6,
            )

    monkeypatch.setattr(stt_service, "FasterWhisperSpeechToTextAdapter", FakeAdapter)
    app = stt_service.create_app(
        Settings(
            runtime_dir=tmp_path,
            voice_stt_faster_whisper_model="small.en",
            voice_stt_faster_whisper_device="cpu",
            voice_stt_faster_whisper_compute_type="int8",
        )
    )
    client = TestClient(app)

    health = client.get("/health")
    preload = client.post("/preload")
    response = client.post(
        "/transcribe",
        json={
            "endpoint_id": "esp-pe-1",
            "session_id": "voice-session-1",
            "chunk_count": 2,
            "sample_rate_hz": 16000,
            "encoding": "pcm_s16le",
            "channels": 1,
            "audio_base64": base64.b64encode(b"\x00\x00" * 320).decode("ascii"),
        },
    )

    assert health.status_code == 200
    assert health.json()["provider"] == "external_faster_whisper"
    assert preload.json()["loaded"] is True
    assert response.status_code == 200
    assert response.json()["text"] == "what time"
    assert response.json()["provider_id"] == "external_faster_whisper"
    assert captured["audio"].endpoint_id == "esp-pe-1"
    assert captured["audio"].audio_bytes == b"\x00\x00" * 320


def test_stt_service_preloads_model_on_startup_when_enabled(monkeypatch, tmp_path):
    calls = []

    class FakeAdapter:
        def __init__(self, *, model_name, device, compute_type, temp_dir):
            pass

        def status(self):
            return {"healthy": True, "model": "base.en", "loaded": bool(calls)}

        def preload(self):
            calls.append("preload")
            return {"loaded": True, "model": "base.en", "duration_ms": 1.2}

    monkeypatch.setattr(stt_service, "FasterWhisperSpeechToTextAdapter", FakeAdapter)
    app = stt_service.create_app(Settings(runtime_dir=tmp_path, voice_stt_preload=True))

    with TestClient(app) as client:
        health = client.get("/health")

    assert calls == ["preload"]
    assert health.json()["loaded"] is True


def test_stt_service_skips_startup_preload_when_disabled(monkeypatch, tmp_path):
    calls = []

    class FakeAdapter:
        def __init__(self, *, model_name, device, compute_type, temp_dir):
            pass

        def status(self):
            return {"healthy": True, "model": "base.en", "loaded": bool(calls)}

        def preload(self):
            calls.append("preload")
            return {"loaded": True, "model": "base.en", "duration_ms": 1.2}

    monkeypatch.setattr(stt_service, "FasterWhisperSpeechToTextAdapter", FakeAdapter)
    app = stt_service.create_app(Settings(runtime_dir=tmp_path, voice_stt_preload=False))

    with TestClient(app) as client:
        health = client.get("/health")

    assert calls == []
    assert health.json()["loaded"] is False
