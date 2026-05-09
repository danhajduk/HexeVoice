from datetime import UTC, datetime, timedelta
import json
import os

from hexevoice.voice import WakeRecordingService


def test_wake_recording_cleanup_removes_files_older_than_retention(tmp_path):
    recording_dir = tmp_path / "wake-recordings"
    recording_dir.mkdir()
    old_wav = recording_dir / "old.wav"
    old_json = recording_dir / "old.json"
    current_wav = recording_dir / "current.wav"
    old_wav.write_bytes(b"old")
    old_json.write_text("{}\n")
    current_wav.write_bytes(b"current")

    old_timestamp = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    os.utime(old_wav, (old_timestamp, old_timestamp))
    os.utime(old_json, (old_timestamp, old_timestamp))

    service = WakeRecordingService(recording_dir=recording_dir, retention_days=7)

    assert not old_wav.exists()
    assert not old_json.exists()
    assert current_wav.exists()
    assert service.status()["last_cleanup"]["deleted_count"] == 2


def test_wake_recording_attach_transcript_migrates_legacy_sidecar(tmp_path):
    recording_dir = tmp_path / "wake-recordings"
    recording_dir.mkdir()
    wav_path = recording_dir / "legacy-wake.wav"
    metadata_path = recording_dir / "legacy-wake.json"
    wav_path.write_bytes(b"RIFFlegacy")
    metadata_path.write_text('{"session_id":"voice-session-1"}\n', encoding="utf-8")
    service = WakeRecordingService(recording_dir=recording_dir, retention_days=7)

    recording = service.attach_transcript(
        {"recording_id": "legacy-wake"},
        {"text": "turn on the light", "provider_id": "stt-test", "confidence": None},
    )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert recording["recording_id"] == "legacy-wake"
    assert recording["audio_url"] == "/api/voice/wake-recordings/legacy-wake"
    assert recording["metadata_path"] == str(metadata_path)
    assert recording["wav_path"] == str(wav_path)
    assert recording["recording_type"] == "accepted_wake_session"
    assert recording["retention_days"] == 7
    assert recording["transcript"] == {"text": "turn on the light", "provider_id": "stt-test"}
    assert metadata["expires_at"]
    assert metadata["transcript"]["text"] == "turn on the light"
