from __future__ import annotations

import base64
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

import httpx
from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.speaker_id.client import SpeakerIdServiceClient
from hexevoice.speaker_id.service import create_app


def wav_base64(path: Path, *, frequency_hz: float = 220.0) -> str:
    sample_rate = 16000
    frame_count = sample_rate
    amplitude = 0.16
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            sample = int(math.sin(2 * math.pi * frequency_hz * (index / sample_rate)) * amplitude * 32767)
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))
    return base64.b64encode(path.read_bytes()).decode("ascii")


def enroll_payload(audio_base64: str, *, display_name: str = "Dan") -> dict:
    return {
        "schema_version": 1,
        "request_id": "speaker-enroll-test",
        "profile": {
            "display_name": display_name,
            "speaker_public_id": f"speaker_{display_name.lower()}",
            "labels": ["test"],
        },
        "consent": {
            "consent_id": f"consent-{display_name.lower()}",
            "consent_version": "speaker-id-consent-v1",
            "consented_at": "2026-08-24T10:00:00Z",
            "consented_by": "operator",
            "retention_policy": "embeddings_only",
        },
        "samples": [{"sample_id": "sample-1", "audio_base64": audio_base64}],
    }


def identify_payload(audio_base64: str) -> dict:
    return {
        "schema_version": 1,
        "request_id": "speaker-identify-test",
        "audio": {"sample_id": "candidate-1", "audio_base64": audio_base64},
    }


def test_speaker_id_settings_defaults_to_unix_socket():
    settings = Settings()

    assert settings.voice_speaker_id_enabled is False
    assert settings.voice_speaker_id_provider == "deterministic_signal"
    assert settings.resolved_voice_speaker_id_base_url() == "http://hexevoice-speaker-id"
    assert settings.resolved_voice_speaker_id_socket_path().as_posix() == "runtime/sockets/speaker-id.sock"
    assert settings.resolved_voice_speaker_id_profiles_path().as_posix() == "runtime/speaker_id/profiles.json"


def test_speaker_id_service_enroll_identify_verify_and_delete(tmp_path):
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=True)
    app = create_app(settings)
    audio = wav_base64(tmp_path / "dan.wav")

    with TestClient(app) as client:
        health = client.get("/health")
        enrolled = client.post("/enroll", json=enroll_payload(audio))
        identified = client.post("/identify", json=identify_payload(audio))
        verified = client.post(
            "/verify",
            json={
                "schema_version": 1,
                "speaker_public_id": "speaker_dan",
                "audio": {"sample_id": "candidate-1", "audio_base64": audio},
            },
        )
        profiles = client.get("/profiles")
        deleted = client.delete("/profiles/speaker_dan")

    assert health.status_code == 200
    assert health.json()["transport"] == "unix_socket"
    assert enrolled.status_code == 200
    assert enrolled.json()["profile"]["speaker_public_id"] == "speaker_dan"
    assert enrolled.json()["profile"]["sample_count"] == 1
    assert identified.status_code == 200
    assert identified.json()["status"] == "identified"
    assert identified.json()["match"]["speaker_public_id"] == "speaker_dan"
    assert verified.status_code == 200
    assert verified.json()["verified"] is True
    assert profiles.json()["profiles"][0]["audio_retained"] is False
    assert deleted.status_code == 200
    assert client.get("/profiles").json()["profiles"] == []


def test_speaker_id_service_returns_unknown_without_profiles(tmp_path):
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=True)
    audio = wav_base64(tmp_path / "unknown.wav")

    with TestClient(create_app(settings)) as client:
        response = client.post("/identify", json=identify_payload(audio))

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    assert response.json()["reason"] == "no_profiles"


def test_speaker_id_service_config_and_recent_outcomes(tmp_path):
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=False)
    audio = wav_base64(tmp_path / "unknown.wav")

    with TestClient(create_app(settings)) as client:
        config = client.put(
            "/config",
            json={
                "enabled": True,
                "provider": "deterministic_signal",
                "identify_min_confidence": 0.5,
                "identify_min_margin": 0.01,
                "verify_min_score": 0.5,
            },
        )
        identified = client.post("/identify", json=identify_payload(audio))
        status = client.get("/status")

    assert config.status_code == 200
    assert config.json()["enabled"] is True
    assert config.json()["thresholds"]["identify_min_confidence"] == 0.5
    assert identified.status_code == 200
    assert identified.json()["status"] == "unknown"
    outcomes = status.json()["recent_identification_outcomes"]
    assert outcomes[0]["kind"] == "identify"
    assert outcomes[0]["status"] == "unknown"
    assert "audio_base64" not in outcomes[0]


def test_speaker_id_service_client_can_call_unix_socket(tmp_path):
    socket_path = tmp_path / "speaker-id.sock"
    runtime_dir = tmp_path / "runtime"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "RUNTIME_DIR": str(runtime_dir),
        "VOICE_SPEAKER_ID_ENABLED": "true",
        "VOICE_SPEAKER_ID_SOCKET_PATH": str(socket_path),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "hexevoice.speaker_id.service"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        client = SpeakerIdServiceClient(socket_path=socket_path, timeout_s=2.0)
        deadline = time.monotonic() + 10
        last_error = None
        while time.monotonic() < deadline:
            try:
                health = client.health()
                break
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                time.sleep(0.1)
        else:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(f"speaker-id service did not start: {last_error}\nstdout={stdout}\nstderr={stderr}")

        assert health["ready"] is True
        assert health["socket_path"] == str(socket_path)
        assert client.status()["transport"]["mode"] == "unix_socket"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
