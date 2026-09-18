from __future__ import annotations

from pathlib import Path
import wave

from fastapi.testclient import TestClient

from hexevoice.api.models import TtsDeliveryOptions, TtsSynthesizeRequest
from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.tts.service import TtsAudioService
from hexevoice.voice.pipeline import TtsSynthesis

OWNER_HEADERS = {"X-Hexe-Requester-Node-Id": "interaction"}


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

    first = client.post("/api/tts/synthesize", json=request, headers=OWNER_HEADERS)
    second = client.post("/api/tts/synthesize", json=request, headers=OWNER_HEADERS)

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
    not_modified = client.get(
        high_url.removeprefix("http://voice-node.local:9004"),
        headers={"If-None-Match": audio.headers["etag"]},
    )
    assert not_modified.status_code == 304
    assert not_modified.content == b""


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

    first = client.post("/api/tts/synthesize", json={**base, "text": "Clear skies."}, headers=OWNER_HEADERS).json()
    second = client.post("/api/tts/synthesize", json={**base, "text": "Rain this evening."}, headers=OWNER_HEADERS).json()

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
        headers=OWNER_HEADERS,
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
        headers=OWNER_HEADERS,
    )

    assert missing.status_code == 200
    assert missing.json()["error"] == "asset_key_required"
    assert traversal.status_code == 200
    assert traversal.json()["error"] == "asset_key_required"


def test_named_asset_lifecycle_enforces_owner_and_redacts_inventory(tmp_path):
    client = _client(tmp_path)
    payload = {
        "text": "The current temperature is 18 degrees.",
        "delivery": {"quality_profiles": ["compact", "high"], "retention": "until_replaced"},
    }

    created = client.put(
        "/api/tts/assets/interaction/weather/home/current",
        json=payload,
        headers=OWNER_HEADERS,
    )
    resolved = client.get("/api/tts/assets/resolve/interaction/weather/home/current")
    inventory = client.get("/api/tts/assets", headers=OWNER_HEADERS)
    forbidden_update = client.put(
        "/api/tts/assets/interaction/weather/home/current",
        json={**payload, "text": "Tampered."},
        headers={"X-Hexe-Requester-Node-Id": "other"},
    )
    forbidden_delete = client.delete(
        "/api/tts/assets/interaction/weather/home/current",
        headers={"X-Hexe-Requester-Node-Id": "other"},
    )

    assert created.status_code == 200
    assert created.json()["asset_key"] == "interaction/weather/home/current"
    assert resolved.status_code == 200
    assert resolved.json()["owner_node_id"] == "interaction"
    assert resolved.json()["transcript"] == payload["text"]
    assert all("path" not in variant for variant in resolved.json()["variants"].values())
    assert inventory.status_code == 200
    assert len(inventory.json()["assets"]) == 1
    assert "transcript" not in inventory.json()["assets"][0]
    assert forbidden_update.status_code == 403
    assert forbidden_delete.status_code == 403

    deleted = client.delete(
        "/api/tts/assets/interaction/weather/home/current",
        headers=OWNER_HEADERS,
    )
    missing = client.get("/api/tts/assets/resolve/interaction/weather/home/current")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert missing.status_code == 404


def test_named_asset_lifecycle_requires_governed_requester_and_rejects_extra_metadata(tmp_path):
    client = _client(tmp_path)
    payload = {"text": "hello", "delivery": {"quality_profiles": ["source"]}}

    no_requester = client.put("/api/tts/assets/interaction/test", json=payload)
    wrong_namespace = client.put(
        "/api/tts/assets/interaction/test",
        json=payload,
        headers={"X-Hexe-Requester-Node-Id": "weather"},
    )
    credential_metadata = client.put(
        "/api/tts/assets/interaction/test",
        json={"text": "hello", "delivery": {"quality_profiles": ["source"], "metadata": {"token": "secret"}}},
        headers=OWNER_HEADERS,
    )

    assert no_requester.status_code == 403
    assert wrong_namespace.status_code == 403
    assert credential_metadata.status_code == 422


def test_failed_named_asset_replacement_keeps_previous_alias(tmp_path):
    class Pipeline:
        fail = False

        def synthesize_reply(self, *, stream_id: str, text: str, voice=None, **_kwargs):
            if self.fail:
                return TtsSynthesis(stream_id=stream_id, error="provider_failed")
            output_dir = Path(tmp_path) / "voice_tts"
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / f"{stream_id}.raw.wav"
            with wave.open(str(path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16_000)
                wav_file.writeframes((b"\x01\x00" if "first" in text else b"\x02\x00") * 160)
            return TtsSynthesis(
                stream_id=stream_id,
                audio_variant="raw",
                audio_variants={"raw": str(path)},
                provider_id="test",
                model_id="test-model",
                voice_id=voice,
            )

    settings = Settings(onboarding_state_path=tmp_path / "state.json", runtime_dir=tmp_path)
    pipeline = Pipeline()
    service = TtsAudioService(settings=settings, voice_turn_pipeline=pipeline)
    delivery = TtsDeliveryOptions(
        mode="named_asset",
        asset_key="interaction/weather/home/current",
        retention="until_replaced",
        quality_profiles=["source"],
    )
    first = service.synthesize(
        TtsSynthesizeRequest(text="first forecast", delivery=delivery),
        requester_node_id="interaction",
    )
    pipeline.fail = True
    failed = service.synthesize(
        TtsSynthesizeRequest(text="second forecast", delivery=delivery),
        requester_node_id="interaction",
    )
    current = service.get_named_asset("interaction/weather/home/current")

    assert first.status == "ready"
    assert failed.status == "failed"
    assert failed.error == "provider_failed"
    assert current is not None
    assert current["revision"] == first.revision
    assert current["transcript"] == "first forecast"
