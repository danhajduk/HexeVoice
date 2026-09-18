from pathlib import Path
import io
import wave

from fastapi.testclient import TestClient

import tts.service as piper_app


def test_piper_tts_health_reports_configured_model(tmp_path, monkeypatch):
    model_path = tmp_path / "voice.onnx"
    model_path.write_bytes(b"model")
    monkeypatch.setenv("PIPER_TTS_MODEL_PATH", str(model_path))

    client = TestClient(piper_app.app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["provider"] == "piper"
    assert response.json()["model_path"] == str(model_path)
    assert response.json()["model_exists"] is True
    assert response.json()["prosody"] == {
        "profile": "natural_assistant",
        "leading_silence_ms": 80,
        "length_scale": 1.08,
        "sentence_silence_ms": 260,
    }


def test_piper_command_uses_natural_assistant_prosody_defaults(tmp_path, monkeypatch):
    model_path = tmp_path / "voice.onnx"
    model_path.write_bytes(b"model")
    monkeypatch.delenv("PIPER_TTS_LENGTH_SCALE", raising=False)
    monkeypatch.delenv("PIPER_TTS_SENTENCE_SILENCE_S", raising=False)

    command = piper_app._piper_command_for_model(model_path)

    assert command[command.index("--length-scale") + 1] == "1.08"
    assert command[command.index("--sentence-silence") + 1] == "0.26"


def test_piper_output_starts_with_default_leading_silence():
    source = io.BytesIO()
    with wave.open(source, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(1000)
        wav_file.writeframes(b"\x01\x00" * 20)

    audio = piper_app._prepend_wav_silence(source.getvalue(), silence_s=0.08)

    with wave.open(io.BytesIO(audio), "rb") as wav_file:
        frames = wav_file.readframes(wav_file.getnframes())
    assert frames[:160] == bytes(160)
    assert frames[160:] == b"\x01\x00" * 20


def test_piper_tts_route_returns_wav(monkeypatch):
    captured = {}

    def fake_synthesize_wav(*, text, voice=None):
        captured["text"] = text
        captured["voice"] = voice
        return b"RIFFtest-wav"

    monkeypatch.setattr(piper_app, "synthesize_wav", fake_synthesize_wav)

    client = TestClient(piper_app.app)
    response = client.post("/api/tts", json={"text": "hello", "voice": "en_US-test"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFFtest-wav"
    assert captured == {"text": "hello", "voice": "en_US-test"}


def test_piper_tts_synthesis_uses_isolated_output_file_even_when_warm_worker_exists(tmp_path, monkeypatch):
    model_path = tmp_path / "voice.onnx"
    model_path.write_bytes(b"model")
    monkeypatch.setenv("PIPER_TTS_MODEL_PATH", str(model_path))
    piper_app._WARM_WORKERS.clear()
    piper_app._WARM_WORKERS[model_path] = piper_app.WarmPiperWorker(model_path)

    def fail_warm_synthesis(self, text):
        raise AssertionError("warm worker should not serve request audio")

    captured = {}

    def fake_synthesize_once(*, text, model_path):
        captured["text"] = text
        captured["model_path"] = model_path
        return b"RIFFisolated-wav"

    monkeypatch.setattr(piper_app.WarmPiperWorker, "synthesize_wav", fail_warm_synthesis)
    monkeypatch.setattr(piper_app, "_synthesize_wav_once", fake_synthesize_once)

    assert piper_app.synthesize_wav(text="hello", voice=None) == b"RIFFisolated-wav"
    assert captured == {"text": "hello", "model_path": model_path}
    piper_app._WARM_WORKERS.clear()


def test_piper_tts_voice_lookup_accepts_core_normalized_model_ids(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    fallback = model_dir / "fallback.onnx"
    fallback.write_bytes(b"fallback")
    requested = model_dir / "en_US-lessac-medium.onnx"
    requested.write_bytes(b"model")
    monkeypatch.setenv("PIPER_TTS_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("PIPER_TTS_MODEL_PATH", str(fallback))

    assert piper_app._model_path_for_voice("en_us-lessac-medium") == requested


def test_piper_tts_config_updates_default_and_warm_voices(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    fallback = model_dir / "fallback.onnx"
    fallback.write_bytes(b"fallback")
    requested = model_dir / "en_US-jenny-high.onnx"
    requested.write_bytes(b"model")
    monkeypatch.setenv("PIPER_TTS_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("PIPER_TTS_MODEL_PATH", str(fallback))
    monkeypatch.setattr(piper_app.WarmPiperWorker, "start", lambda self: None)
    monkeypatch.setattr(piper_app.WarmPiperWorker, "stop", lambda self: None)
    piper_app._WARM_WORKERS.clear()

    client = TestClient(piper_app.app)
    response = client.put(
        "/config",
        json={"default_voice": "en_us-jenny-high", "warm_voices": ["en_US-jenny-high"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["config_applied"] is True
    assert payload["model_path"] == str(requested)
    assert payload["warm_voices"] == ["en_US-jenny-high"]
    assert piper_app._model_path_for_voice(None) == requested
    piper_app._WARM_WORKERS.clear()


def test_piper_tts_dockerfile_launches_tts_service_module():
    dockerfile = Path(__file__).resolve().parents[1] / "services/piper_tts/Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")

    assert "COPY src/tts /app/tts" in content
    assert '"python", "-m", "tts.service"' in content
    assert "app:app" not in content
