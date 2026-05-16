import base64

from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from stt.adapters import SpeechTranscript
import stt.service as stt_service


def test_stt_service_transcribes_with_external_faster_whisper(monkeypatch, tmp_path):
    captured = {}

    class FakeAdapter:
        def __init__(
            self,
            *,
            model_name,
            device,
            compute_type,
            temp_dir,
            language,
            beam_size,
            best_of,
            without_timestamps,
            word_timestamps,
            max_initial_timestamp,
        ):
            captured["model_name"] = model_name
            captured["device"] = device
            captured["compute_type"] = compute_type
            captured["temp_dir"] = temp_dir
            captured["language"] = language
            captured["beam_size"] = beam_size
            captured["best_of"] = best_of
            captured["without_timestamps"] = without_timestamps
            captured["word_timestamps"] = word_timestamps
            captured["max_initial_timestamp"] = max_initial_timestamp

        def status(self):
            return {
                "healthy": True,
                "model": captured["model_name"],
                "loaded": False,
                "loaded_at": None,
                "load_count": 0,
                "reload_required": False,
            }

        def preload(self):
            return {"loaded": True, "model": captured["model_name"], "duration_ms": 1.2}

        def transcribe(self, audio):
            captured["audio"] = audio
            return SpeechTranscript(
                text="what time",
                provider_id="faster_whisper",
                model=captured["model_name"],
                duration_ms=5.6,
                timing_breakdown_ms={"total_ms": 5.6, "model_inference_ms": 4.0},
            )

    monkeypatch.setattr(stt_service, "FasterWhisperSpeechToTextAdapter", FakeAdapter)
    app = stt_service.create_app(
        Settings(
            runtime_dir=tmp_path,
            voice_stt_faster_whisper_model="small.en",
            voice_stt_faster_whisper_device="cpu",
            voice_stt_faster_whisper_compute_type="int8",
            voice_stt_faster_whisper_language="en",
            voice_stt_faster_whisper_beam_size=2,
            voice_stt_faster_whisper_best_of=3,
            voice_stt_faster_whisper_without_timestamps=True,
            voice_stt_faster_whisper_word_timestamps=True,
            voice_stt_faster_whisper_max_initial_timestamp=0.5,
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
    assert health.json()["loaded"] is False
    assert health.json()["reload_required"] is False
    assert preload.json()["loaded"] is True
    assert response.status_code == 200
    assert response.json()["text"] == "what time"
    assert response.json()["provider_id"] == "external_faster_whisper"
    assert response.json()["timing_breakdown_ms"]["model_inference_ms"] == 4.0
    assert captured["audio"].endpoint_id == "esp-pe-1"
    assert captured["audio"].audio_bytes == b"\x00\x00" * 320
    assert captured["language"] == "en"
    assert captured["beam_size"] == 2
    assert captured["best_of"] == 3
    assert captured["word_timestamps"] is True
    assert captured["max_initial_timestamp"] == 0.5


def test_stt_service_preloads_model_on_startup_when_enabled(monkeypatch, tmp_path):
    calls = []

    class FakeAdapter:
        def __init__(self, **_kwargs):
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
        def __init__(self, **_kwargs):
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


def test_stt_service_config_switches_active_model(monkeypatch, tmp_path):
    created_models = []
    created_configs = []
    preloaded = []

    class FakeAdapter:
        def __init__(self, *, model_name, device, compute_type, **_kwargs):
            self.model_name = model_name
            self.device = device
            self.compute_type = compute_type
            self.loaded = False
            created_models.append(model_name)
            created_configs.append((model_name, device, compute_type))

        def status(self):
            return {
                "healthy": True,
                "model": self.model_name,
                "device": self.device,
                "compute_type": self.compute_type,
                "loaded": self.loaded,
            }

        def preload(self):
            self.loaded = True
            preloaded.append(self.model_name)
            return {"loaded": True, "model": self.model_name, "duration_ms": 1.2}

    monkeypatch.setattr(stt_service, "FasterWhisperSpeechToTextAdapter", FakeAdapter)
    app = stt_service.create_app(Settings(runtime_dir=tmp_path, voice_stt_faster_whisper_model="base.en"))
    client = TestClient(app)

    response = client.put("/config", json={"model": "small.en", "device": "cuda", "compute_type": "float16", "warm_model": True})

    assert response.status_code == 200
    assert response.json()["model"] == "small.en"
    assert response.json()["device"] == "cuda"
    assert response.json()["compute_type"] == "float16"
    assert response.json()["loaded"] is True
    assert created_models == ["base.en", "small.en"]
    assert created_configs == [("base.en", "cpu", "int8"), ("small.en", "cuda", "float16")]
    assert preloaded == ["small.en"]
