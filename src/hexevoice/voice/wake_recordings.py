from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any, Sequence
import wave

from hexevoice.voice.contracts import VoiceAudioFormat


@dataclass
class _WakeCapture:
    audio_format: VoiceAudioFormat
    audio: bytearray = field(default_factory=bytearray)
    accepted: dict[str, Any] | None = None


class WakeRecordingService:
    def __init__(
        self,
        *,
        recording_dir: Path,
        retention_days: int = 7,
        preroll_ms: int = 2000,
    ) -> None:
        self._recording_dir = recording_dir
        self._retention_days = retention_days
        self._preroll_ms = preroll_ms
        self._captures: dict[tuple[str, str], _WakeCapture] = {}
        self._last_recording: dict[str, Any] | None = None
        self._last_cleanup: dict[str, Any] | None = None
        self.cleanup_expired()

    def capture_wake_chunk(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        audio_format: VoiceAudioFormat,
        audio_bytes: bytes,
    ) -> None:
        if not audio_bytes or audio_format.encoding != "pcm_s16le":
            return

        key = (endpoint_id, session_id)
        capture = self._captures.get(key)
        if capture is None or capture.audio_format != audio_format:
            capture = _WakeCapture(audio_format=audio_format)
            self._captures[key] = capture
        capture.audio.extend(audio_bytes)
        self._trim_capture(capture)

    def mark_accepted_wake(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        model: str | None,
        confidence: float | None,
        source: str | None,
        chunk_index: int | None,
        chunk_count: int | None,
    ) -> None:
        capture = self._captures.get((endpoint_id, session_id))
        if capture is None:
            return
        capture.accepted = {
            "model": model,
            "confidence": confidence,
            "source": source,
            "accepted_chunk_index": chunk_index,
            "accepted_chunk_count": chunk_count,
            "accepted_at": datetime.now(UTC).isoformat(),
        }

    def record_accepted_session(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        stt_chunks: Sequence[bytes],
        chunk_count: int,
    ) -> dict[str, Any] | None:
        key = (endpoint_id, session_id)
        capture = self._captures.get(key)
        if capture is None or capture.accepted is None or capture.audio_format.encoding != "pcm_s16le":
            return None

        audio_bytes = bytes(capture.audio) + b"".join(chunk for chunk in stt_chunks if chunk)
        if not audio_bytes:
            return None

        self.cleanup_expired()
        self._recording_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        stem = "_".join(
            [
                now.strftime("%Y%m%dT%H%M%S%fZ"),
                _safe_component(endpoint_id),
                _safe_component(session_id),
                "accepted_wake",
            ]
        )
        wav_path = self._recording_dir / f"{stem}.wav"
        metadata_path = self._recording_dir / f"{stem}.json"
        frame_count = self._write_wav(wav_path, capture.audio_format, audio_bytes)
        duration_ms = round((frame_count / capture.audio_format.sample_rate_hz) * 1000, 2)
        expires_at = now + timedelta(days=self._retention_days)
        metadata: dict[str, Any] = {
            "recording_id": stem,
            "recorded_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "retention_days": self._retention_days,
            "endpoint_id": endpoint_id,
            "session_id": session_id,
            "recording_type": "accepted_wake_session",
            "audio_format": capture.audio_format.model_dump(mode="json"),
            "duration_ms": duration_ms,
            "frame_count": frame_count,
            "byte_count": len(audio_bytes),
            "wake_preroll_byte_count": len(capture.audio),
            "stt_byte_count": sum(len(chunk) for chunk in stt_chunks if chunk),
            "chunk_count": chunk_count,
            "wav_path": str(wav_path),
            "metadata_path": str(metadata_path),
            "audio_url": f"/api/voice/wake-recordings/{stem}",
            **capture.accepted,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._last_recording = metadata
        self.close_session(endpoint_id=endpoint_id, session_id=session_id)
        return metadata

    def close_session(self, *, endpoint_id: str, session_id: str) -> None:
        self._captures.pop((endpoint_id, session_id), None)

    def attach_transcript(self, recording: dict[str, Any], transcript: dict[str, Any]) -> dict[str, Any]:
        metadata_path_value = recording.get("metadata_path")
        if not metadata_path_value:
            return recording
        metadata_path = Path(str(metadata_path_value))
        if not metadata_path.is_file():
            return recording
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return recording
        if not isinstance(metadata, dict):
            return recording

        updated_transcript = {key: value for key, value in transcript.items() if value is not None}
        metadata["transcript"] = updated_transcript
        recording["transcript"] = updated_transcript
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if self._last_recording and self._last_recording.get("metadata_path") == str(metadata_path):
            self._last_recording = dict(recording)
        return recording

    def cleanup_expired(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(days=self._retention_days)
        deleted: list[str] = []
        self._recording_dir.mkdir(parents=True, exist_ok=True)
        for path in self._recording_dir.iterdir():
            if path.suffix not in {".wav", ".json"} or not path.is_file():
                continue
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if modified_at >= cutoff:
                continue
            try:
                path.unlink()
                deleted.append(str(path))
            except OSError:
                continue
        self._last_cleanup = {
            "ran_at": current.isoformat(),
            "retention_days": self._retention_days,
            "deleted_count": len(deleted),
            "deleted_paths": deleted,
        }
        return self._last_cleanup

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "recording_dir": str(self._recording_dir),
            "retention_days": self._retention_days,
            "preroll_ms": self._preroll_ms,
            "buffered_sessions": len(self._captures),
            "last_recording": self._last_recording,
            "last_cleanup": self._last_cleanup,
        }

    def recording_path(self, recording_id: str) -> Path | None:
        safe_recording_id = _safe_component(recording_id)
        if safe_recording_id != recording_id or not safe_recording_id:
            return None
        path = self._recording_dir / f"{safe_recording_id}.wav"
        return path if path.is_file() else None

    def _trim_capture(self, capture: _WakeCapture) -> None:
        max_bytes = self._max_preroll_bytes(capture.audio_format)
        if len(capture.audio) <= max_bytes:
            return
        frame_size = capture.audio_format.channels * 2
        excess = len(capture.audio) - max_bytes
        excess += (frame_size - (excess % frame_size)) % frame_size
        del capture.audio[:excess]

    def _max_preroll_bytes(self, audio_format: VoiceAudioFormat) -> int:
        frame_size = audio_format.channels * 2
        bytes_per_ms = audio_format.sample_rate_hz * frame_size / 1000
        return max(frame_size, int(bytes_per_ms * self._preroll_ms))

    @staticmethod
    def _write_wav(path: Path, audio_format: VoiceAudioFormat, audio_bytes: bytes) -> int:
        frame_size = audio_format.channels * 2
        frame_count = len(audio_bytes) // frame_size
        aligned_audio = audio_bytes[: frame_count * frame_size]
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(audio_format.channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(audio_format.sample_rate_hz)
            wav_file.writeframes(aligned_audio)
        return frame_count


def _safe_component(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe[:80] or "unknown"
