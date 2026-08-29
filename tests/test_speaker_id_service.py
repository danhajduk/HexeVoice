from __future__ import annotations

import base64
import json
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
import hexevoice.speaker_id.adapters as speaker_adapters
from hexevoice.speaker_id.client import SpeakerIdServiceClient
from hexevoice.speaker_id.service import _match_payload
from hexevoice.speaker_id.service import create_app


def wav_base64(path: Path, *, frequency_hz: float = 220.0, duration_ms: int = 1000, amplitude: float = 0.16) -> str:
    sample_rate = 16000
    frame_count = max(1, int(sample_rate * (duration_ms / 1000)))
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


def enrollment_samples(audio_base64: str, *, count: int = 8) -> list[dict]:
    return [{"sample_id": f"sample-{index + 1}", "audio_base64": audio_base64} for index in range(count)]


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
        "samples": enrollment_samples(audio_base64),
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
        profile_export = client.get("/profiles/speaker_dan")
        deleted = client.delete("/profiles/speaker_dan")

    assert health.status_code == 200
    assert health.json()["transport"] == "unix_socket"
    assert enrolled.status_code == 200
    assert enrolled.json()["profile"]["speaker_public_id"] == "speaker_dan"
    assert enrolled.json()["profile"]["sample_count"] == 8
    assert enrolled.json()["profile"]["accepted_sample_count"] == 8
    assert enrolled.json()["profile"]["audio_retained"] is False
    assert enrolled.json()["profile"]["phrase_set_version"] == "speaker-id-phrase-set-v1"
    assert len(enrolled.json()["profile"]["phrase_tracking"]["accepted"]) == 0
    readiness = enrolled.json()["profile"]["enrollment_readiness"]
    assert readiness["can_enroll"] is True
    assert readiness["required_sample_count"] == 8
    assert readiness["production_ready"] is False
    assert readiness["learning_eligible"] is False
    assert identified.status_code == 200
    assert identified.json()["status"] == "identified"
    assert identified.json()["match"]["speaker_public_id"] == "speaker_dan"
    assert identified.json()["match"]["confidence_tier"] == "very_high"
    assert identified.json()["match"]["learning_eligible"] is False
    assert verified.status_code == 200
    assert verified.json()["verified"] is True
    assert profiles.json()["profiles"][0]["audio_retained"] is False
    exported_profile = profile_export.json()["profile"]
    public_payload = json.dumps(exported_profile)
    assert profile_export.status_code == 200
    assert "embeddings" not in exported_profile
    assert "values" not in exported_profile
    assert "audio_base64" not in public_payload
    assert deleted.status_code == 200
    assert client.get("/profiles").json()["profiles"] == []
    stored_payload = json.loads(settings.resolved_voice_speaker_id_profiles_path().read_text(encoding="utf-8"))
    assert stored_payload["profiles"] == []


def test_speaker_id_phrase_sets_and_holdout_selection_are_versioned(tmp_path):
    with TestClient(create_app(Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=True))) as client:
        phrase_sets = client.get("/phrase-sets")
        first_selection = client.get("/phrase-sets/holdout-selection", params={"count": 5, "seed": "profile-a"})
        second_selection = client.get("/phrase-sets/holdout-selection", params={"count": 5, "seed": "profile-a"})

    assert phrase_sets.status_code == 200
    payload = phrase_sets.json()
    assert payload["active_version"] == "speaker-id-phrase-set-v1"
    active = payload["phrase_sets"][0]
    assert len(active["enrollment"]) == 24
    assert len(active["holdout_validation"]) == 24
    assert active["enrollment"][0]["phrase_id"] == "enroll-001"
    assert active["enrollment"][23]["text"] == "A bright yellow scarf was folded inside the small suitcase."
    assert active["holdout_validation"][0]["phrase_id"] == "holdout-001"
    assert first_selection.status_code == 200
    assert first_selection.json() == second_selection.json()
    assert first_selection.json()["phrase_set_version"] == "speaker-id-phrase-set-v1"
    assert len(first_selection.json()["phrases"]) == 5


def test_speaker_id_enrollment_persists_phrase_tracking(tmp_path):
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=True)
    audio = wav_base64(tmp_path / "dan.wav")
    payload = enroll_payload(audio)
    payload["phrase_set_version"] = "speaker-id-phrase-set-v1"
    payload["samples"] = [
        {
            "sample_id": f"sample-{index + 1}",
            "audio_base64": audio,
            "phrase_set_version": "speaker-id-phrase-set-v1",
            "phrase_id": f"enroll-{index + 1:03d}",
            "phrase_text": f"Phrase {index + 1}",
            "phrase_status": "accepted",
        }
        for index in range(8)
    ]
    payload["samples"][3]["phrase_status"] = "skipped"
    payload["samples"][6]["phrase_status"] = "failed_quality"

    with TestClient(create_app(settings)) as client:
        response = client.post("/enroll", json=payload)

    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["phrase_set_version"] == "speaker-id-phrase-set-v1"
    tracking = profile["phrase_tracking"]
    assert tracking["phrase_set_version"] == "speaker-id-phrase-set-v1"
    assert len(tracking["presented"]) == 8
    assert len(tracking["accepted"]) == 6
    assert tracking["skipped"][0]["phrase_id"] == "enroll-004"
    assert tracking["failed_quality"][0]["phrase_id"] == "enroll-007"


def test_speaker_id_enrollment_applies_age_band_policy_defaults(tmp_path):
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=True)
    audio = wav_base64(tmp_path / "dan.wav")

    child = enroll_payload(audio, display_name="Child")
    child["profile"].update({"age_band": "child", "admin_eligible": True})
    teen = enroll_payload(audio, display_name="Teen")
    teen["profile"].update({"age_band": "teen", "admin_eligible": True})
    adult = enroll_payload(audio, display_name="Adult")
    adult["profile"].update({"age_band": "adult", "admin_eligible": True})
    unknown = enroll_payload(audio, display_name="Unknown")

    with TestClient(create_app(settings)) as client:
        child_response = client.post("/enroll", json=child)
        teen_response = client.post("/enroll", json=teen)
        adult_response = client.post("/enroll", json=adult)
        unknown_response = client.post("/enroll", json=unknown)

    child_profile = child_response.json()["profile"]
    teen_profile = teen_response.json()["profile"]
    adult_profile = adult_response.json()["profile"]
    unknown_profile = unknown_response.json()["profile"]

    assert child_profile["age_band"] == "child"
    assert child_profile["guardian_managed"] is True
    assert child_profile["profile_review_interval_days"] == 45
    assert child_profile["admin_eligible"] is False
    assert child_profile["profile_learning_requires_review"] is True
    assert child_profile["speaker_policy"]["age_inferred_from_voice"] is False
    assert child_profile["next_voice_profile_review_at"]
    assert teen_profile["age_band"] == "teen"
    assert teen_profile["guardian_managed"] is True
    assert teen_profile["profile_review_interval_days"] == 75
    assert teen_profile["admin_eligible"] is False
    assert teen_profile["profile_learning_requires_review"] is True
    assert adult_profile["age_band"] == "adult"
    assert adult_profile["profile_review_interval_days"] == 365
    assert adult_profile["admin_eligible"] is True
    assert adult_profile["profile_learning_requires_review"] is False
    assert unknown_profile["age_band"] == "unknown"
    assert unknown_profile["profile_review_interval_days"] is None
    assert unknown_profile["next_voice_profile_review_at"] is None
    assert unknown_profile["admin_eligible"] is False
    assert "birth" not in json.dumps(unknown_profile).lower()


def test_speaker_id_enrollment_rejects_short_or_silent_data(tmp_path):
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=True)
    short_audio = wav_base64(tmp_path / "short.wav", duration_ms=200)
    silent_audio = wav_base64(tmp_path / "silent.wav", amplitude=0.0)

    with TestClient(create_app(settings)) as client:
        short_response = client.post("/enroll", json=enroll_payload(short_audio))
        silent_response = client.post("/enroll", json=enroll_payload(silent_audio, display_name="Silent"))

    assert short_response.status_code == 400
    assert "enrollment_sample_unusable" in short_response.json()["detail"]
    assert silent_response.status_code == 400
    assert "enrollment_sample_unusable" in silent_response.json()["detail"]


def test_speaker_id_enrollment_rejects_insufficient_sample_count(tmp_path):
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=True)
    audio = wav_base64(tmp_path / "dan.wav")
    payload = enroll_payload(audio)
    payload["samples"] = enrollment_samples(audio, count=3)

    with TestClient(create_app(settings)) as client:
        response = client.post("/enroll", json=payload)

    assert response.status_code == 400
    assert "insufficient_sample_count" in response.json()["detail"]


def test_speaker_id_enrollment_records_clipped_quality_warning(tmp_path):
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=True)
    clipped_audio = wav_base64(tmp_path / "clipped.wav", amplitude=1.0)

    with TestClient(create_app(settings)) as client:
        response = client.post("/enroll", json=enroll_payload(clipped_audio))

    assert response.status_code == 200
    readiness = response.json()["profile"]["enrollment_readiness"]
    assert readiness["can_enroll"] is True
    assert readiness["production_ready"] is False
    assert any("clipped" in warning for warning in readiness["warnings"])


def test_speaker_id_confidence_tiers_and_low_margin_metadata_are_redacted(tmp_path):
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=True)
    audio = wav_base64(tmp_path / "dan.wav")
    first = enroll_payload(audio, display_name="Dan")
    second = enroll_payload(audio, display_name="Dana")

    with TestClient(create_app(settings)) as client:
        assert client.post("/enroll", json=first).status_code == 200
        assert client.post("/enroll", json=second).status_code == 200
        low_margin = client.post(
            "/identify",
            json={
                **identify_payload(audio),
                "thresholds": {"identify_min_confidence": 0.5, "identify_min_margin": 0.2},
            },
        )

    assert low_margin.status_code == 200
    payload = low_margin.json()
    assert payload["status"] == "unknown"
    assert payload["reason"] == "low_margin"
    assert payload["match"]["confidence_tier"] == "very_high"
    assert payload["match"]["learning_eligible"] is False
    public_payload = json.dumps(payload)
    assert "audio_base64" not in public_payload
    assert "values" not in public_payload

    medium = _match_payload({"score": 0.72}, 0.1)
    low = _match_payload({"score": 0.4}, 0.1)
    assert medium["confidence_tier"] == "medium"
    assert medium["learning_eligible"] is False
    assert low["confidence_tier"] == "low"
    assert low["learning_eligible"] is False


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


def test_speaker_id_service_config_persists_across_restart(tmp_path):
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=False)

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

    assert config.status_code == 200
    assert (tmp_path / "speaker_id" / "config.json").exists()

    with TestClient(create_app(settings)) as client:
        status = client.get("/status")

    assert status.status_code == 200
    payload = status.json()
    assert payload["enabled"] is True
    assert payload["provider"] == "deterministic_signal"
    assert payload["thresholds"]["identify_min_confidence"] == 0.5
    assert payload["thresholds"]["identify_min_margin"] == 0.01
    assert payload["thresholds"]["verify_min_score"] == 0.5


def test_speaker_id_service_uses_speechbrain_provider_when_available(monkeypatch, tmp_path):
    class FakeClassifier:
        @classmethod
        def from_hparams(cls, **_kwargs):
            return cls()

    monkeypatch.setattr(
        speaker_adapters,
        "_speechbrain_dependency_status",
        lambda: {"speechbrain": True, "torch": True},
    )
    monkeypatch.setattr(speaker_adapters, "_speechbrain_classifier_class", lambda: FakeClassifier)
    monkeypatch.setattr(
        speaker_adapters,
        "_speechbrain_embedding_values",
        lambda classifier, audio, *, device: (0.1, 0.2, 0.3),
    )
    settings = Settings(
        runtime_dir=tmp_path,
        voice_speaker_id_enabled=True,
        voice_speaker_id_provider="speechbrain_ecapa_tdnn",
    )
    audio = wav_base64(tmp_path / "dan.wav")

    with TestClient(create_app(settings)) as client:
        before = client.get("/status")
        enrolled = client.post("/enroll", json=enroll_payload(audio))
        identified = client.post("/identify", json=identify_payload(audio))
        after = client.get("/status")

    assert before.status_code == 200
    assert before.json()["provider_status"]["reason"] == "model_not_loaded"
    assert before.json()["model"]["loaded"] is False
    assert enrolled.status_code == 200
    assert enrolled.json()["profile"]["provider_id"] == "speechbrain_ecapa_tdnn"
    assert identified.status_code == 200
    assert identified.json()["status"] == "identified"
    assert identified.json()["match"]["provider"] == "speechbrain_ecapa_tdnn"
    assert after.json()["provider_status"]["loaded"] is True
    assert after.json()["model"]["loaded"] is True


def test_speaker_id_config_rejects_missing_provider_dependencies(monkeypatch, tmp_path):
    monkeypatch.setattr(
        speaker_adapters,
        "_speechbrain_dependency_status",
        lambda: {"speechbrain": False, "torch": True},
    )
    settings = Settings(runtime_dir=tmp_path, voice_speaker_id_enabled=True)

    with TestClient(create_app(settings)) as client:
        response = client.put("/config", json={"provider": "speechbrain_ecapa_tdnn"})
        status = client.get("/status")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason"] == "missing_optional_dependency"
    assert detail["provider"] == "speechbrain_ecapa_tdnn"
    assert detail["dependencies"] == {"speechbrain": False, "torch": True}
    assert "Missing: speechbrain" in detail["message"]
    assert status.json()["provider"] == "deterministic_signal"


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
