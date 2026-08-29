import json
from datetime import date

from hexevoice.persistence import VoiceQualityObservationLog, subtract_one_calendar_month


def sample_session():
    return {
        "session_id": "voice-session-1",
        "endpoint_id": "esp-box-1",
        "completed_at": "2026-08-29T12:34:56+00:00",
        "transcript": {
            "text": "Hexe turn on the kitchen lights",
            "provider_id": "deterministic",
            "model": "deterministic",
            "confidence": 0.91,
            "speaker_identity": {
                "schema_version": 1,
                "status": "identified",
                "policy": "use_if_ready",
                "speaker_public_id": "speaker_dan",
                "display_name": "Dan",
                "confidence": 0.9,
                "score": 0.88,
                "score_margin": 0.12,
                "reason": None,
            },
            "audio_quality": {
                "schema_version": 1,
                "status": "ok",
                "warnings": [],
                "duration_ms": 1200,
                "sample_rate_hz": 16000,
                "channels": 1,
                "encoding": "pcm_s16le",
                "frame_count": 19200,
                "rms": 0.04,
                "peak": 0.2,
                "clipping_count": 0,
                "clipping_ratio": 0,
                "active_audio_ratio": 0.7,
                "silence_ratio": 0.3,
                "speech_rms": 0.06,
                "ambient_rms": 0.01,
                "ambient_peak": 0.03,
                "ambient_duration_ms": 500,
                "speech_peak": 0.2,
                "speech_duration_ms": 1200,
                "snr_db": 20,
                "snr_status": "ok",
                "snr_reason": None,
                "source": "backend",
            },
        },
    }


def test_voice_quality_observation_log_writes_full_record(tmp_path):
    log = VoiceQualityObservationLog(directory=tmp_path / "observations", enabled=True, transcript_mode="full")
    result = log.write_session_observation(sample_session())

    assert result is not None
    path = tmp_path / "observations" / "2026-08-29.jsonl"
    record = json.loads(path.read_text().strip())
    assert record["stt"]["text"] == "Hexe turn on the kitchen lights"
    assert record["speaker_identity"]["speaker_public_id"] == "speaker_dan"
    assert record["audio_quality"]["snr_db"] == 20
    assert record["privacy"]["raw_audio_persisted"] is False
    assert '"embedding":' not in path.read_text()
    assert '"speaker_embedding":' not in path.read_text()


def test_voice_quality_observation_log_disabled_writes_nothing(tmp_path):
    log = VoiceQualityObservationLog(directory=tmp_path / "observations", enabled=False)

    assert log.write_session_observation(sample_session()) is None
    assert not (tmp_path / "observations").exists()


def test_voice_quality_observation_log_redacts_transcript_text(tmp_path):
    log = VoiceQualityObservationLog(directory=tmp_path / "observations", enabled=True, transcript_mode="redacted")
    log.write_session_observation(sample_session())

    record = json.loads((tmp_path / "observations" / "2026-08-29.jsonl").read_text().strip())
    assert record["stt"]["text"] is None
    assert record["stt"]["transcript_chars"] == len("Hexe turn on the kitchen lights")
    assert record["stt"]["transcript_mode"] == "redacted"


def test_voice_quality_observation_log_redacts_forbidden_speaker_identity(tmp_path):
    session = sample_session()
    session["transcript"]["speaker_identity"]["policy"] = "forbidden"
    log = VoiceQualityObservationLog(directory=tmp_path / "observations", enabled=True, transcript_mode="full")
    log.write_session_observation(session)

    record = json.loads((tmp_path / "observations" / "2026-08-29.jsonl").read_text().strip())
    assert record["speaker_identity"]["speaker_public_id"] is None
    assert record["speaker_identity"]["display_name"] is None


def test_voice_quality_observation_calendar_month_cleanup(tmp_path):
    directory = tmp_path / "observations"
    directory.mkdir()
    old_file = directory / "2026-07-25.jsonl"
    cutoff_file = directory / "2026-07-26.jsonl"
    current_file = directory / "2026-08-26.jsonl"
    old_file.write_text("{}\n")
    cutoff_file.write_text("{}\n")
    current_file.write_text("{}\n")
    log = VoiceQualityObservationLog(directory=directory, enabled=True)

    cleanup = log.cleanup(current_date=date(2026, 8, 26))

    assert cleanup["cutoff_date"] == "2026-07-26"
    assert cleanup["removed_count"] == 1
    assert not old_file.exists()
    assert cutoff_file.exists()
    assert current_file.exists()


def test_subtract_one_calendar_month_clamps_month_end():
    assert subtract_one_calendar_month(date(2026, 8, 26)) == date(2026, 7, 26)
    assert subtract_one_calendar_month(date(2026, 3, 31)) == date(2026, 2, 28)
