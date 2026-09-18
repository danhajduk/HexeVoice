from __future__ import annotations

from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )


def test_named_tts_asset_is_idempotent_and_exposes_quality_metadata(tmp_path):
    client = _client(tmp_path)
    request = {
        "text": "Rain is expected this afternoon.",
        "voice": "default",
        "delivery": {
            "mode": "named_asset",
            "asset_key": "interaction/weather/home/current",
            "retention": "until_replaced",
            "quality_profiles": ["compact", "high", "source"],
            "source_version": "forecast-17",
        },
    }

    first = client.post("/api/tts/synthesize", json=request)
    second = client.post("/api/tts/synthesize", json=request)

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["delivery_mode"] == "named_asset"
    assert first_payload["asset_key"] == "interaction/weather/home/current"
    assert first_payload["changed"] is True
    assert first_payload["cache_hit"] is False
    assert second_payload["revision"] == first_payload["revision"]
    assert second_payload["changed"] is False
    assert second_payload["cache_hit"] is True
    assert set(first_payload["variants"]) == {"compact", "high", "source"}
    assert first_payload["variants"]["compact"]["sample_rate_hz"] == 16_000
    assert first_payload["variants"]["high"]["sample_rate_hz"] == 48_000
    assert len(first_payload["variants"]["high"]["sha256"]) == 64

    high_url = first_payload["variants"]["high"]["audio_url"]
    audio = client.get(high_url.removeprefix("http://voice-node.local:9004"))
    assert audio.status_code == 200
    assert audio.content.startswith(b"RIFF")
    assert audio.headers["etag"] == f'"{first_payload["variants"]["high"]["sha256"]}"'
    assert audio.headers["cache-control"] == "no-cache"


def test_named_tts_replacement_keeps_urls_stable_and_updates_transcript(tmp_path):
    client = _client(tmp_path)
    base = {
        "voice": "default",
        "delivery": {
            "mode": "named_asset",
            "asset_key": "interaction/weather/home/current",
            "quality_profiles": ["standard"],
        },
    }

    first = client.post("/api/tts/synthesize", json={**base, "text": "Clear skies."}).json()
    second = client.post("/api/tts/synthesize", json={**base, "text": "Rain this evening."}).json()

    assert second["changed"] is True
    assert second["cache_hit"] is False
    assert second["revision"].startswith("ttsrev-")
    assert second["variants"]["standard"]["audio_url"] == first["variants"]["standard"]["audio_url"]
    assert second["transcript"] == "Rain this evening."


def test_cached_and_persistent_delivery_have_expected_retention(tmp_path):
    client = _client(tmp_path)

    cached = client.post(
        "/api/tts/synthesize",
        json={"text": "Timer complete.", "ttl_seconds": 30, "delivery": {"mode": "cached", "quality_profiles": ["source"]}},
    ).json()
    persistent = client.post(
        "/api/tts/synthesize",
        json={
            "text": "Welcome home.",
            "delivery": {
                "mode": "persistent",
                "asset_key": "interaction/greetings/home",
                "quality_profiles": ["source"],
            },
        },
    ).json()

    assert cached["delivery_mode"] == "cached"
    assert cached["asset_key"].startswith("cache/")
    assert cached["expires_at"] is not None
    assert persistent["delivery_mode"] == "persistent"
    assert persistent["expires_at"] is None


def test_named_delivery_requires_safe_asset_key(tmp_path):
    client = _client(tmp_path)

    missing = client.post(
        "/api/tts/synthesize",
        json={"text": "hello", "delivery": {"mode": "named_asset", "quality_profiles": ["source"]}},
    )
    traversal = client.post(
        "/api/tts/synthesize",
        json={
            "text": "hello",
            "delivery": {"mode": "named_asset", "asset_key": "interaction/../secret", "quality_profiles": ["source"]},
        },
    )

    assert missing.status_code == 200
    assert missing.json()["error"] == "asset_key_required"
    assert traversal.status_code == 200
    assert traversal.json()["error"] == "asset_key_required"
