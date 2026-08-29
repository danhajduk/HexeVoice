from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


class PersistedVoicePlacementCalibration(BaseModel):
    schema_version: int = 1
    windows: list[dict[str, Any]] = Field(default_factory=list)
    samples: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)


class VoicePlacementCalibrationStore:
    def __init__(self, *, path: Path, max_windows: int = 100, max_samples: int = 5000) -> None:
        self._path = path
        self._max_windows = max(1, int(max_windows))
        self._max_samples = max(1, int(max_samples))

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> PersistedVoicePlacementCalibration:
        if not self._path.exists():
            return PersistedVoicePlacementCalibration()
        payload = json.loads(self._path.read_text())
        return PersistedVoicePlacementCalibration.model_validate(payload)

    def save(self, calibration: PersistedVoicePlacementCalibration) -> PersistedVoicePlacementCalibration:
        updated = calibration.model_copy(update={"updated_at": utc_now_iso()})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(updated.model_dump_json(indent=2))
        temp_path.replace(self._path)
        return updated

    def start_window(
        self,
        *,
        endpoint_id: str,
        room: str,
        zone: str | None = None,
        duration_hours: float = 24,
        sample_interval_seconds: int = 600,
        retention_days: int = 3,
        debug_record_audio: bool = False,
    ) -> dict[str, Any]:
        endpoint_id = str(endpoint_id or "").strip()
        room = str(room or "").strip()
        if not endpoint_id:
            raise ValueError("endpoint_id_required")
        if not room:
            raise ValueError("room_required")
        duration_hours = max(1.0, min(float(duration_hours or 24), 48.0))
        sample_interval_seconds = max(60, min(int(sample_interval_seconds or 600), 3600))
        retention_days = max(1, min(int(retention_days or 3), 14))
        now = datetime.now(UTC)
        window = {
            "schema_version": 1,
            "calibration_id": f"placement-cal-{uuid4().hex[:12]}",
            "endpoint_id": endpoint_id,
            "room": room,
            "zone": str(zone or "").strip() or None,
            "mode": "passive_ambient",
            "status": "active",
            "started_at": _iso(now),
            "ends_at": _iso(now + timedelta(hours=duration_hours)),
            "duration_hours": duration_hours,
            "sample_interval_seconds": sample_interval_seconds,
            "next_sample_due_at": _iso(now),
            "retention_days": retention_days,
            "sample_count": 0,
            "last_sample_at": None,
            "debug_record_audio": bool(debug_record_audio),
            "raw_audio_policy": "discard_after_metrics" if not debug_record_audio else "debug_retention_one_day",
            "metrics_only": True,
        }
        calibration = self._complete_expired_windows(self.load(), now=now)
        windows = [stored for stored in calibration.windows if stored.get("calibration_id") != window["calibration_id"]]
        windows.insert(0, window)
        del windows[self._max_windows :]
        self.save(calibration.model_copy(update={"windows": windows}))
        return dict(window)

    def cancel_window(self, calibration_id: str) -> dict[str, Any] | None:
        target = str(calibration_id or "").strip()
        if not target:
            return None
        now = datetime.now(UTC)
        calibration = self.load()
        updated_windows: list[dict[str, Any]] = []
        cancelled: dict[str, Any] | None = None
        for window in calibration.windows:
            if window.get("calibration_id") == target:
                cancelled = {
                    **window,
                    "status": "cancelled",
                    "cancelled_at": _iso(now),
                    "next_sample_due_at": None,
                }
                updated_windows.append(cancelled)
            else:
                updated_windows.append(window)
        if cancelled is None:
            return None
        self.save(calibration.model_copy(update={"windows": updated_windows}))
        return dict(cancelled)

    def record_sample(
        self,
        *,
        calibration_id: str,
        metrics: dict[str, object],
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        target = str(calibration_id or "").strip()
        if not target:
            raise ValueError("calibration_id_required")
        now = datetime.now(UTC)
        observed_dt = _parse_dt(observed_at) or now
        calibration = self._complete_expired_windows(self.load(), now=now)
        window = next((stored for stored in calibration.windows if stored.get("calibration_id") == target), None)
        if window is None:
            raise ValueError("calibration_not_found")
        if window.get("status") != "active":
            raise ValueError("calibration_not_active")
        sanitized_metrics = self._sanitize_metrics(metrics)
        sample = {
            "schema_version": 1,
            "sample_id": f"placement-sample-{uuid4().hex[:12]}",
            "calibration_id": target,
            "endpoint_id": window.get("endpoint_id"),
            "room": window.get("room"),
            "zone": window.get("zone"),
            "observed_at": _iso(observed_dt),
            "received_at": _iso(now),
            "metrics": sanitized_metrics,
            "privacy": {
                "metrics_only": True,
                "raw_audio": {"persisted": False, "policy": window.get("raw_audio_policy")},
                "stt": {"called": False},
                "speaker_id": {"called": False},
            },
        }
        interval = int(window.get("sample_interval_seconds") or 600)
        next_due = observed_dt + timedelta(seconds=interval)
        ends_at = _parse_dt(window.get("ends_at"))
        updated_window = {
            **window,
            "sample_count": int(window.get("sample_count") or 0) + 1,
            "last_sample_at": sample["observed_at"],
            "next_sample_due_at": _iso(next_due) if ends_at is None or next_due < ends_at else None,
        }
        windows = [updated_window if stored.get("calibration_id") == target else stored for stored in calibration.windows]
        samples = [sample, *calibration.samples]
        del samples[self._max_samples :]
        calibration = calibration.model_copy(update={"windows": windows, "samples": samples})
        calibration = self.cleanup(calibration=calibration, now=now)
        self.save(calibration)
        return dict(sample)

    def status(self, *, endpoint_id: str | None = None) -> dict[str, Any]:
        calibration = self.cleanup(now=datetime.now(UTC))
        windows = calibration.windows
        samples = calibration.samples
        if endpoint_id:
            windows = [window for window in windows if window.get("endpoint_id") == endpoint_id]
            samples = [sample for sample in samples if sample.get("endpoint_id") == endpoint_id]
        active = [dict(window) for window in windows if window.get("status") == "active"]
        return {
            "enabled": True,
            "path": str(self._path),
            "active_windows": active,
            "recent_windows": [dict(window) for window in windows[:10]],
            "sample_count": len(samples),
            "updated_at": calibration.updated_at,
        }

    def get_window(self, calibration_id: str) -> dict[str, Any] | None:
        target = str(calibration_id or "").strip()
        if not target:
            return None
        calibration = self.cleanup(now=datetime.now(UTC))
        for window in calibration.windows:
            if window.get("calibration_id") == target:
                return dict(window)
        return None

    def list_samples(self, *, calibration_id: str, limit: int = 500) -> list[dict[str, Any]]:
        target = str(calibration_id or "").strip()
        if not target:
            return []
        calibration = self.cleanup(now=datetime.now(UTC))
        bounded = max(1, min(int(limit), 5000))
        return [dict(sample) for sample in calibration.samples if sample.get("calibration_id") == target][:bounded]

    def cleanup(
        self,
        *,
        calibration: PersistedVoicePlacementCalibration | None = None,
        now: datetime | None = None,
    ) -> PersistedVoicePlacementCalibration:
        current = now or datetime.now(UTC)
        loaded = calibration or self.load()
        completed = self._complete_expired_windows(loaded, now=current)
        windows_by_id = {str(window.get("calibration_id")): window for window in completed.windows}
        retained_samples: list[dict[str, Any]] = []
        removed_samples = 0
        for sample in completed.samples:
            observed_at = _parse_dt(sample.get("observed_at")) or current
            window = windows_by_id.get(str(sample.get("calibration_id")))
            retention_days = int(window.get("retention_days") or 3) if window else 3
            if observed_at < current - timedelta(days=max(1, retention_days)):
                removed_samples += 1
                continue
            retained_samples.append(sample)
        updated = completed.model_copy(update={"samples": retained_samples})
        if calibration is None and (updated.windows != loaded.windows or removed_samples):
            return self.save(updated)
        return updated

    @staticmethod
    def _complete_expired_windows(
        calibration: PersistedVoicePlacementCalibration,
        *,
        now: datetime,
    ) -> PersistedVoicePlacementCalibration:
        windows: list[dict[str, Any]] = []
        changed = False
        for window in calibration.windows:
            if window.get("status") == "active":
                ends_at = _parse_dt(window.get("ends_at"))
                if ends_at is not None and ends_at <= now:
                    window = {**window, "status": "completed", "completed_at": _iso(ends_at), "next_sample_due_at": None}
                    changed = True
            windows.append(window)
        return calibration.model_copy(update={"windows": windows}) if changed else calibration

    @staticmethod
    def _sanitize_metrics(metrics: dict[str, object]) -> dict[str, object]:
        if not isinstance(metrics, dict):
            raise ValueError("metrics_required")
        sanitized: dict[str, object] = {}
        for key in (
            "ambient_rms",
            "ambient_level_db",
            "peak",
            "peak_db",
            "clipping_count",
            "clipping_ratio",
            "speech_like_activity_ratio",
            "snr_db",
            "sample_duration_ms",
        ):
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                sanitized[key] = round(float(value), 6)
        value = metrics.get("speech_like_activity")
        if isinstance(value, bool):
            sanitized["speech_like_activity"] = value
        if not sanitized:
            raise ValueError("metrics_required")
        return sanitized
