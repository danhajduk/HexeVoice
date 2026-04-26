import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


def load_piper_app_module():
    module_path = Path(__file__).resolve().parents[1] / "services/piper_tts/app.py"
    spec = importlib.util.spec_from_file_location("hexevoice_piper_tts_service", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


piper_app = load_piper_app_module()


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
