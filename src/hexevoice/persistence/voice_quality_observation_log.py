from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any


class VoiceQualityObservationLog:
    def __init__(
        self,
        *,
        directory: Path,
        enabled: bool = False,
        transcript_mode: str = "redacted",
    ) -> None:
        self._directory = directory
        self._enabled = bool(enabled)
        self._transcript_mode = "full" if str(transcript_mode).strip().lower() == "full" else "redacted"
        self._latest_cleanup: dict[str, object] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def transcript_mode(self) -> str:
        return self._transcript_mode

    def write_session_observation(self, session: dict[str, Any]) -> dict[str, object] | None:
        if not self._enabled:
            return None
        record = self._record_from_session(session)
        assert_metric_payload_redacted(record)
        observed_at = _parse_datetime(record.get("observed_at")) or datetime.now().astimezone()
        path = self._path_for_date(observed_at.date())
        self._directory.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")
        return {"record": record, "path": str(path)}

    def cleanup(self, *, current_date: date | None = None) -> dict[str, object]:
        today = current_date or datetime.now().astimezone().date()
        cutoff = subtract_one_calendar_month(today)
        removed_files: list[str] = []
        if self._directory.exists():
            for path in sorted(self._directory.glob("*.jsonl")):
                file_date = _date_from_filename(path)
                if file_date is not None and file_date < cutoff:
                    path.unlink()
                    removed_files.append(str(path))
        result = {
            "policy": "one_calendar_month",
            "current_date": today.isoformat(),
            "cutoff_date": cutoff.isoformat(),
            "removed_files": removed_files,
            "removed_count": len(removed_files),
        }
        self._latest_cleanup = result
        return result

    def status(self) -> dict[str, object]:
        files = sorted(self._directory.glob("*.jsonl")) if self._directory.exists() else []
        latest_file = str(files[-1]) if files else None
        record_count = 0
        for path in files:
            try:
                record_count += sum(1 for line in path.read_text().splitlines() if line.strip())
            except OSError:
                continue
        return {
            "enabled": self._enabled,
            "directory": str(self._directory),
            "transcript_mode": self._transcript_mode,
            "retention_policy": "one_calendar_month",
            "latest_file": latest_file,
            "file_count": len(files),
            "record_count": record_count,
            "latest_cleanup": self._latest_cleanup,
        }

    def _record_from_session(self, session: dict[str, Any]) -> dict[str, object]:
        transcript = session.get("transcript") if isinstance(session.get("transcript"), dict) else {}
        audio_quality = transcript.get("audio_quality") if isinstance(transcript.get("audio_quality"), dict) else None
        speaker_identity = (
            transcript.get("speaker_identity") if isinstance(transcript.get("speaker_identity"), dict) else None
        )
        text = str(transcript.get("text") or "")
        observed_at = (
            session.get("completed_at")
            or session.get("updated_at")
            or session.get("started_at")
            or datetime.now().astimezone().isoformat()
        )
        return {
            "schema_version": 1,
            "feature": "voice_quality_observation_log",
            "observed_at": observed_at,
            "recorded_at": datetime.now().astimezone().isoformat(),
            "endpoint_id": session.get("endpoint_id"),
            "session_id": session.get("session_id"),
            "stt": {
                "text": text if self._transcript_mode == "full" else None,
                "transcript_mode": self._transcript_mode,
                "transcript_chars": len(text),
                "provider_id": transcript.get("provider_id"),
                "model": transcript.get("model"),
                "confidence": transcript.get("confidence"),
                "error": transcript.get("error"),
            },
            "speaker_identity": self._speaker_identity(speaker_identity),
            "ambient_noise": self._ambient_noise(audio_quality),
            "audio_quality": audio_quality,
            "source_versions": {
                "observation_schema": 1,
                "audio_quality_schema": audio_quality.get("schema_version") if audio_quality else None,
                "speaker_identity_schema": speaker_identity.get("schema_version") if speaker_identity else None,
            },
            "privacy": {
                "derived_data_only": True,
                "transcript_mode": self._transcript_mode,
                "raw_audio_persisted": False,
                "embeddings_persisted": False,
                "biometric_templates_persisted": False,
            },
        }

    @staticmethod
    def _speaker_identity(speaker: dict[str, object] | None) -> dict[str, object] | None:
        if not speaker:
            return None
        policy = str(speaker.get("policy") or "")
        identity_allowed = policy != "forbidden"
        confidence = _float_or_none(speaker.get("confidence"))
        return {
            "schema_version": 1,
            "status": speaker.get("status"),
            "policy": policy or None,
            "speaker_public_id": speaker.get("speaker_public_id") if identity_allowed else None,
            "display_name": speaker.get("display_name") if identity_allowed else None,
            "score": speaker.get("score"),
            "confidence": confidence,
            "confidence_tier": speaker.get("confidence_tier") or speaker_confidence_tier(confidence),
            "score_margin": speaker.get("score_margin"),
            "reason": speaker.get("reason"),
            "provider": speaker.get("provider"),
            "model_id": speaker.get("model_id"),
        }

    @staticmethod
    def _ambient_noise(audio_quality: dict[str, object] | None) -> dict[str, object]:
        if not audio_quality:
            return {"status": "unavailable", "reason": "missing_audio_quality"}
        return {
            "status": audio_quality.get("snr_status") or "unavailable",
            "reason": audio_quality.get("snr_reason"),
            "ambient_rms": audio_quality.get("ambient_rms"),
            "ambient_peak": audio_quality.get("ambient_peak"),
            "ambient_duration_ms": audio_quality.get("ambient_duration_ms"),
            "snr_db": audio_quality.get("snr_db"),
            "source": audio_quality.get("source"),
            "classification": "unavailable",
        }

    def _path_for_date(self, observed_date: date) -> Path:
        return self._directory / f"{observed_date.isoformat()}.jsonl"


def subtract_one_calendar_month(value: date) -> date:
    year = value.year
    month = value.month - 1
    if month == 0:
        month = 12
        year -= 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone() if parsed.tzinfo is not None else parsed.astimezone()


def _date_from_filename(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def speaker_confidence_tier(confidence: float | None) -> str:
    if confidence is None:
        return "none"
    if confidence >= 0.95:
        return "very_high"
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.7:
        return "medium"
    if confidence > 0:
        return "low"
    return "none"


FORBIDDEN_OBSERVATION_FIELDS = {
    "audio_base64",
    "audio_bytes",
    "audio_payload",
    "biometric_template",
    "embedding",
    "embeddings",
    "passcode",
    "pin",
    "raw_audio",
    "speaker_embedding",
    "voiceprint",
}


def assert_metric_payload_redacted(payload: Any, *, prefix: str = "") -> None:
    matches: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if str(key).lower() in FORBIDDEN_OBSERVATION_FIELDS:
                matches.append(path)
            try:
                assert_metric_payload_redacted(value, prefix=path)
            except ValueError as exc:
                matches.extend(str(exc).split(": ", 1)[-1].split(", "))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            try:
                assert_metric_payload_redacted(value, prefix=f"{prefix}[{index}]")
            except ValueError as exc:
                matches.extend(str(exc).split(": ", 1)[-1].split(", "))
    if matches:
        raise ValueError(f"metric_payload_contains_sensitive_fields: {', '.join(matches)}")
