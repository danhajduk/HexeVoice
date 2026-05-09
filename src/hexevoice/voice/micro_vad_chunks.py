from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any
import wave

from hexevoice.voice.contracts import VoiceAudioChunkPayload, VoiceAudioFormat


@dataclass
class _MicroVadCapture:
    audio_format: VoiceAudioFormat
    started_at: datetime
    audio: bytearray = field(default_factory=bytearray)
    chunk_count: int = 0
    first_audio_chunk_index: int | None = None
    last_audio_chunk_index: int | None = None
    final_pause_ms: int | None = None


class MicroVadChunkRecordingService:
    def __init__(self, *, recording_dir: Path, retention_days: int = 1) -> None:
        self._recording_dir = recording_dir
        self._retention_days = retention_days
        self._captures: dict[tuple[str, str, int], _MicroVadCapture] = {}
        self._last_recording: dict[str, Any] | None = None
        self._last_cleanup: dict[str, Any] | None = None
        self.cleanup_expired()

    def capture_audio_chunk(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        payload: VoiceAudioChunkPayload,
        audio_bytes: bytes,
        received_at: datetime,
    ) -> dict[str, Any] | None:
        micro_chunk_index = payload.micro_vad_chunk_index
        if micro_chunk_index is None or not audio_bytes or payload.audio_format.encoding != "pcm_s16le":
            return None

        key = (endpoint_id, session_id, micro_chunk_index)
        capture = self._captures.get(key)
        if capture is None or capture.audio_format != payload.audio_format or payload.micro_vad_chunk_started:
            capture = _MicroVadCapture(audio_format=payload.audio_format, started_at=received_at)
            self._captures[key] = capture

        capture.audio.extend(audio_bytes)
        capture.chunk_count += 1
        capture.first_audio_chunk_index = (
            payload.chunk_index if capture.first_audio_chunk_index is None else capture.first_audio_chunk_index
        )
        capture.last_audio_chunk_index = payload.chunk_index
        capture.final_pause_ms = payload.micro_vad_pause_ms

        if payload.micro_vad_chunk_final:
            return self._write_capture(
                endpoint_id=endpoint_id,
                session_id=session_id,
                micro_chunk_index=micro_chunk_index,
                capture=capture,
                completed_at=received_at,
                final=True,
            )
        return None

    def close_session(self, *, endpoint_id: str, session_id: str) -> None:
        keys = [key for key in self._captures if key[0] == endpoint_id and key[1] == session_id]
        now = datetime.now(UTC)
        for key in keys:
            capture = self._captures.get(key)
            if capture is None or not capture.audio:
                self._captures.pop(key, None)
                continue
            self._write_capture(
                endpoint_id=endpoint_id,
                session_id=session_id,
                micro_chunk_index=key[2],
                capture=capture,
                completed_at=now,
                final=False,
            )

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
            "buffered_chunks": len(self._captures),
            "last_recording": self._last_recording,
            "last_cleanup": self._last_cleanup,
        }

    def _write_capture(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        micro_chunk_index: int,
        capture: _MicroVadCapture,
        completed_at: datetime,
        final: bool,
    ) -> dict[str, Any]:
        self.cleanup_expired()
        self._recording_dir.mkdir(parents=True, exist_ok=True)
        stem = "_".join(
            [
                completed_at.strftime("%Y%m%dT%H%M%S%fZ"),
                _safe_component(endpoint_id),
                _safe_component(session_id),
                f"micro_vad_{micro_chunk_index:04d}",
            ]
        )
        wav_path = self._recording_dir / f"{stem}.wav"
        metadata_path = self._recording_dir / f"{stem}.json"
        audio_bytes = bytes(capture.audio)
        frame_count = _write_wav(wav_path, capture.audio_format, audio_bytes)
        duration_ms = round((frame_count / capture.audio_format.sample_rate_hz) * 1000, 2)
        metadata: dict[str, Any] = {
            "recording_id": stem,
            "recorded_at": capture.started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "expires_at": (completed_at + timedelta(days=self._retention_days)).isoformat(),
            "retention_days": self._retention_days,
            "endpoint_id": endpoint_id,
            "session_id": session_id,
            "recording_type": "firmware_micro_vad_chunk",
            "micro_vad_chunk_index": micro_chunk_index,
            "micro_vad_chunk_final": final,
            "micro_vad_pause_ms": capture.final_pause_ms,
            "audio_format": capture.audio_format.model_dump(mode="json"),
            "duration_ms": duration_ms,
            "frame_count": frame_count,
            "byte_count": len(audio_bytes),
            "audio_chunk_count": capture.chunk_count,
            "first_audio_chunk_index": capture.first_audio_chunk_index,
            "last_audio_chunk_index": capture.last_audio_chunk_index,
            "wav_path": str(wav_path),
            "metadata_path": str(metadata_path),
        }
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._last_recording = metadata
        self._captures.pop((endpoint_id, session_id, micro_chunk_index), None)
        return metadata


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
