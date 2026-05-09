from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PersistedVoiceSessionHistory(BaseModel):
    schema_version: int = 1
    sessions: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)


class VoiceSessionHistoryStore:
    def __init__(self, *, path: Path, max_records: int = 100) -> None:
        self._path = path
        self._max_records = max(1, max_records)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def max_records(self) -> int:
        return self._max_records

    def load(self) -> PersistedVoiceSessionHistory:
        if not self._path.exists():
            return PersistedVoiceSessionHistory()

        payload = json.loads(self._path.read_text())
        return PersistedVoiceSessionHistory.model_validate(payload)

    def save(self, history: PersistedVoiceSessionHistory) -> PersistedVoiceSessionHistory:
        updated = history.model_copy(update={"updated_at": utc_now_iso()})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(updated.model_dump_json(indent=2))
        temp_path.replace(self._path)
        return updated

    def upsert_session(self, record: dict[str, Any]) -> dict[str, Any]:
        session_id = str(record.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")

        now = utc_now_iso()
        normalized = {**record, "session_id": session_id, "updated_at": now}
        history = self.load()
        sessions = [session for session in history.sessions if session.get("session_id") != session_id]
        sessions.insert(0, normalized)
        del sessions[self._max_records :]
        self.save(history.model_copy(update={"sessions": sessions}))
        return normalized

    def list_sessions(self, *, limit: int = 20, endpoint_id: str | None = None) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, self._max_records))
        sessions = self.load().sessions
        if endpoint_id:
            sessions = [session for session in sessions if session.get("endpoint_id") == endpoint_id]
        return [self._summary(session) for session in sessions[:bounded_limit]]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        for session in self.load().sessions:
            if session.get("session_id") == session_id:
                return dict(session)
        return None

    def latest_replay_eligible(self, *, endpoint_id: str | None = None) -> dict[str, Any] | None:
        for session in self.load().sessions:
            if endpoint_id and session.get("endpoint_id") != endpoint_id:
                continue
            replay = session.get("replay")
            tts = session.get("tts")
            if not isinstance(replay, dict) or not isinstance(tts, dict):
                continue
            if replay.get("eligible") and tts.get("stream_id") and tts.get("audio_url"):
                return dict(session)
        return None

    def status(self) -> dict[str, Any]:
        history = self.load()
        return {
            "enabled": True,
            "path": str(self._path),
            "max_records": self._max_records,
            "stored_count": len(history.sessions),
            "updated_at": history.updated_at,
        }

    @staticmethod
    def _summary(session: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": session.get("session_id"),
            "endpoint_id": session.get("endpoint_id"),
            "session_state": session.get("session_state"),
            "started_at": session.get("started_at"),
            "completed_at": session.get("completed_at"),
            "updated_at": session.get("updated_at"),
            "duration_ms": session.get("duration_ms"),
            "completion_reason": session.get("completion_reason"),
            "cancel_reason": session.get("cancel_reason"),
            "error_state": session.get("error_state"),
            "wake": session.get("wake"),
            "vad": session.get("vad"),
            "audio": session.get("audio"),
            "transcript": session.get("transcript"),
            "assistant": session.get("assistant"),
            "turn_timings": session.get("turn_timings"),
            "latency": session.get("latency"),
            "latency_points": session.get("latency_points"),
            "tts": session.get("tts"),
            "tts_playback": session.get("tts_playback"),
            "replay": session.get("replay"),
            "wake_recording": session.get("wake_recording"),
        }
