from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from hexevoice.config.settings import Settings
from hexevoice.weather_snapshots import WeatherSnapshotService, WeatherSnapshotStore


NOW = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)
SOURCE = "node-interaction-1"
TOPIC = f"hexe/nodes/{SOURCE}/events/weather/snapshot/updated"
RESULT_TOPIC = f"hexe/nodes/{SOURCE}/events/weather/current_succeeded"
PROMOTED_RESULT_TOPIC = "hexe/events/weather/current_succeeded"


def weather_event() -> dict:
    sha = "a" * 64
    return {
        "schema_version": 1,
        "event_id": "weather-snapshot-1",
        "event_type": "weather.snapshot.updated",
        "source": {"node_id": SOURCE, "component": "open-meteo-weather", "node_type": "interaction"},
        "subject": {"family": "weather", "record_id": "weather-home-1", "location_id": "home"},
        "occurred_at": NOW.isoformat(),
        "data": {
            "schema_version": "weather.snapshot.v1",
            "snapshot_id": "weather-home-1",
            "location_id": "home",
            "location": {"label": "Home", "latitude": 45.5, "longitude": -123.0},
            "cache_revision": "weather-revision-1",
            "observed_at": NOW.isoformat(),
            "fetched_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
            "current_conditions": {"temperature": 56, "condition": "Clear"},
            "forecast_summary": {"summary": "Clear", "precip_chance": 0},
            "transcript": "It is 56 degrees and clear.",
            "tts": {
                "status": "ready",
                "asset_key": "interaction/weather/home/current",
                "revision": "ttsrev-1",
                "quality": "high",
                "audio_url": "http://voice.local/api/tts/assets/interaction/weather/home/current/audio/high",
                "transcript": "It is 56 degrees and clear.",
                "variants": {
                    "compact": {
                        "audio_url": "http://voice.local/api/tts/assets/interaction/weather/home/current/audio/compact",
                        "sha256": sha,
                        "sample_rate_hz": 16000,
                    },
                    "high": {
                        "audio_url": "http://voice.local/api/tts/assets/interaction/weather/home/current/audio/high",
                        "sha256": sha,
                        "sample_rate_hz": 48000,
                    },
                },
            },
            "radar": {"status": "absent", "sha256": None, "attribution": []},
            "freshness": {"weather": "fresh", "tts": "ready", "radar": "absent"},
            "attribution": {"weather": ["Weather data: Open-Meteo"], "radar": []},
        },
    }


def service(tmp_path) -> WeatherSnapshotService:
    return WeatherSnapshotService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding.json", runtime_dir=tmp_path),
        snapshot_store=WeatherSnapshotStore(tmp_path / "weather.json"),
    )


def weather_result(*, event_type="weather.current_succeeded") -> dict:
    data = {
        "correlation_id": "voice-intent-1",
        "endpoint_id": "p4",
        "session_id": "session-1",
        "intent_id": "weather.current",
    }
    if event_type == "weather.current_succeeded":
        data.update(snapshot_id="weather-home-1", snapshot_revision="weather-revision-1")
    else:
        data.update(error_code="weather_unavailable", message="Weather is unavailable.", recoverable=True)
    return {
        "schema_version": 1,
        "event_id": f"interaction-{event_type}",
        "event_type": event_type,
        "occurred_at": NOW.isoformat(),
        "source": {"node_id": SOURCE, "component": "hexe.weather"},
        "subject": {"family": "weather", "record_id": "weather-home-1"},
        "data": data,
        "severity": "info" if event_type.endswith("_succeeded") else "warning",
        "priority": "normal",
        "safety_critical": False,
    }


def test_accepts_and_persists_coherent_weather_snapshot(tmp_path):
    snapshots = service(tmp_path)

    assert snapshots.accept(TOPIC, weather_event(), received_at=NOW) is True

    prepared = snapshots.prepared_snapshot()
    assert prepared["snapshot_id"] == "weather-home-1"
    assert prepared["tts"]["quality"] == "high"
    assert snapshots.status()["fallback_available"] is True
    reloaded = service(tmp_path)
    assert reloaded.prepared_snapshot()["cache_revision"] == "weather-revision-1"


def test_accepts_core_promoted_weather_topic(tmp_path):
    snapshots = service(tmp_path)
    event = weather_event()
    event["promoted_event_type"] = "weather.snapshot.updated"
    event["received_at"] = event.pop("occurred_at")
    event["routing"] = {"domain_topic": "hexe/events/weather/snapshot/updated"}
    event["policy"] = {"schema_valid": True, "privacy_valid": True}

    assert snapshots.accept("hexe/events/weather/snapshot/updated", event, received_at=NOW) is True
    assert snapshots.status()["source_node_id"] == SOURCE


def test_accepts_weather_result_and_notifies_listener(tmp_path):
    snapshots = service(tmp_path)
    received = []
    snapshots.add_result_listener(received.append)

    assert snapshots.accept_result(RESULT_TOPIC, weather_result(), received_at=NOW) is True
    assert received[0]["data"]["endpoint_id"] == "p4"
    assert snapshots.status()["last_result"]["event"]["data"]["snapshot_revision"] == "weather-revision-1"
    assert snapshots.accept_result(RESULT_TOPIC, weather_result(), received_at=NOW) is False
    assert snapshots.status()["reason"] == "duplicate_weather_result_ignored"


def test_rejects_weather_result_source_topic_mismatch(tmp_path):
    snapshots = service(tmp_path)
    result = weather_result()
    result["source"]["node_id"] = "node-attacker"

    assert snapshots.accept_result(RESULT_TOPIC, result, received_at=NOW) is False
    assert snapshots.status()["reason"] == "source_topic_mismatch"


def test_accepts_core_promoted_weather_result(tmp_path):
    snapshots = service(tmp_path)
    result = weather_result()
    result["promoted_event_type"] = result["event_type"]
    result["received_at"] = result.pop("occurred_at")
    result["routing"] = {"domain_topic": PROMOTED_RESULT_TOPIC}
    result["policy"] = {"schema_valid": True, "privacy_valid": True}

    assert snapshots.accept_result(PROMOTED_RESULT_TOPIC, result, received_at=NOW) is True
    assert snapshots.status()["last_result"]["event"]["data"]["endpoint_id"] == "p4"


def test_rejects_wrong_source_and_keeps_last_known_good(tmp_path):
    snapshots = service(tmp_path)
    assert snapshots.accept(TOPIC, weather_event(), received_at=NOW) is True
    rejected = weather_event()
    rejected["event_id"] = "weather-snapshot-2"
    rejected["source"]["node_id"] = "node-attacker"

    assert snapshots.accept(TOPIC, rejected, received_at=NOW) is False
    assert snapshots.status()["reason"] == "source_topic_mismatch"
    assert snapshots.prepared_snapshot()["snapshot_id"] == "weather-home-1"


def test_rejects_older_snapshot_replay(tmp_path):
    snapshots = service(tmp_path)
    current = weather_event()
    current["data"]["fetched_at"] = (NOW + timedelta(minutes=1)).isoformat()
    current["data"]["expires_at"] = (NOW + timedelta(minutes=6)).isoformat()
    assert snapshots.accept(TOPIC, current, received_at=NOW + timedelta(minutes=1)) is True

    assert snapshots.accept(TOPIC, weather_event(), received_at=NOW + timedelta(minutes=1)) is False
    assert snapshots.status()["reason"] == "stale_snapshot"


def test_rejects_incoherent_or_unsafe_ready_media(tmp_path):
    snapshots = service(tmp_path)
    mismatch = weather_event()
    mismatch["data"]["freshness"]["tts"] = "failed"
    assert snapshots.accept(TOPIC, mismatch, received_at=NOW) is False
    assert snapshots.status()["reason"] == "component_freshness_mismatch"

    credential_url = weather_event()
    credential_url["data"]["tts"]["audio_url"] += "?token=secret"
    assert snapshots.accept(TOPIC, credential_url, received_at=NOW) is False
    assert snapshots.status()["reason"] == "credential_bearing_tts_url"

    invalid_hash = weather_event()
    invalid_hash["data"]["tts"]["variants"]["high"]["sha256"] = "bad"
    assert snapshots.accept(TOPIC, invalid_hash, received_at=NOW) is False
    assert snapshots.status()["reason"] == "invalid_tts_variant"


def test_accepts_current_attributed_static_radar(tmp_path):
    snapshots = service(tmp_path)
    event = deepcopy(weather_event())
    event["data"]["radar"] = {
        "status": "ready",
        "image_url": "https://interaction.local/assets/weather/radar/current.png",
        "sha256": "b" * 64,
        "observation_time": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
        "attribution": ["Radar: RainViewer", "Map: OpenStreetMap contributors"],
    }
    event["data"]["freshness"]["radar"] = "ready"

    assert snapshots.accept(TOPIC, event, received_at=NOW) is True
    assert snapshots.prepared_snapshot()["radar"]["status"] == "ready"
