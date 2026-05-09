from datetime import UTC, datetime, timedelta
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
