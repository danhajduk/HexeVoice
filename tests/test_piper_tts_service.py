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
