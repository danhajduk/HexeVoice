import json
from pathlib import Path

from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.persistence import VoiceSessionHistoryStore
from hexevoice.voice import VoiceSessionManager


def _session(session_id: str, endpoint_id: str, status: str, warnings=None, snr_db=None) -> dict[str, object]:
    return {
        "session_id": session_id,
        "endpoint_id": endpoint_id,
        "completed_at": f"2026-08-29T12:0{session_id[-1]}:00+00:00",
        "transcript": {
            "text": "test",
            "audio_quality": {
                "schema_version": 1,
                "status": status,
                "warnings": warnings or [],
                "snr_db": snr_db,
            },
        },
    }


def test_endpoint_audio_quality_stats_roll_up_recent_session_history(tmp_path):
    history_store = VoiceSessionHistoryStore(path=tmp_path / "voice_session_history.json", max_records=20)
    history_store.upsert_session(_session("voice-session-1", "esp-box-1", "ok", snr_db=22.0))
    history_store.upsert_session(_session("voice-session-2", "esp-box-1", "low_snr", ["low_snr"], 7.0))
    history_store.upsert_session(_session("voice-session-3", "esp-box-2", "clipped", ["clipped"], 18.0))
    manager = VoiceSessionManager(session_history_store=history_store)

    stats = manager.endpoint_audio_quality_stats(limit=10)

    assert stats["schema_version"] == 1
    assert stats["enabled"] is True
    assert stats["window"]["observed_session_count"] == 3
    by_endpoint = {item["endpoint_id"]: item for item in stats["endpoints"]}
    assert by_endpoint["esp-box-1"]["sample_count"] == 2
    assert by_endpoint["esp-box-1"]["ok_count"] == 1
    assert by_endpoint["esp-box-1"]["warning_rate"] == 0.5
    assert by_endpoint["esp-box-1"]["snr_db"]["avg"] == 14.5
    assert by_endpoint["esp-box-1"]["recommendation"] == "reduce_background_noise_or_move_endpoint"
    assert by_endpoint["esp-box-2"]["recommendation"] == "check_microphone_gain"


def test_endpoint_audio_quality_stats_api_can_scope_to_endpoint(tmp_path):
    history_path = tmp_path / "voice_session_history.json"
    history_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-08-29T12:00:00+00:00",
                "sessions": [
                    _session("voice-session-1", "esp-box-1", "ok", snr_db=24.0),
                    _session("voice-session-2", "esp-box-2", "low_snr", ["low_snr"], 6.0),
                ],
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                runtime_dir=tmp_path,
                voice_session_history_path=history_path,
            )
        )
    )

    response = client.get("/api/voice/audio-quality/endpoint-stats", params={"endpoint_id": "esp-box-1", "limit": 10})

    assert response.status_code == 200
    stats = response.json()["stats"]
    assert stats["source"] == "voice_session_history"
    assert stats["window"]["observed_session_count"] == 1
    assert [item["endpoint_id"] for item in stats["endpoints"]] == ["esp-box-1"]
    assert stats["endpoints"][0]["latest"]["status"] == "ok"
    assert "audio_bytes" not in json.dumps(stats)


def test_endpoint_dashboard_sources_include_rolling_audio_quality_summary():
    source = Path("frontend/src/features/dashboard/VoiceEndpointDashboardSection.jsx").read_text(encoding="utf-8")

    assert "getVoiceEndpointAudioQualityStats" in source
    assert "endpointAudioQualitySummary" in source
    assert "Audio window" in source
    assert "Audio recommendation" in source
