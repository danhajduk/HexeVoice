from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
from typing import Any

from pydantic import BaseModel, Field


ADMIN_MAINTENANCE_INTENT_IDS = (
    "admin.debug.start",
    "admin.debug.stop",
    "admin.enrollment.start",
    "admin.enrollment.cancel",
    "admin.placement.start_active",
    "admin.placement.start_passive_48h",
    "admin.placement.status",
    "admin.placement.stop",
    "admin.privacy.status",
    "admin.privacy.purge_debug_audio",
    "admin.voice.quality.status",
    "admin.speaker.enrollment.status",
    "admin.passcode.rotate",
)

PASSCODE_ITERATIONS = 210_000
MIN_ADMIN_CONFIDENCE = 0.95
MIN_ADMIN_SCORE_MARGIN = 0.15
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_MINUTES = 5
PASSCODE_WORDS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "for": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "ate": "8",
    "nine": "9",
}
BAD_AUDIO_WARNINGS = {"silent", "missing_audio", "unsupported_audio", "clipped", "short_audio", "low_level", "low_snr"}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class VoiceAdminMaintenanceState(BaseModel):
    schema_version: int = 1
    enabled: bool = False
    admin_speaker_public_ids: list[str] = Field(default_factory=list)
    enabled_intents: dict[str, bool] = Field(default_factory=dict)
    passcode_hash: dict[str, Any] | None = None
    failed_attempts: int = 0
    locked_until: str | None = None
    updated_at: str = Field(default_factory=utc_now_iso)


@dataclass(frozen=True)
class AdminMaintenanceDecision:
    allowed: bool
    reason: str
    intent_id: str | None
    locked_until: str | None = None

    def as_metadata(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "allowed": self.allowed,
            "reason": self.reason,
            "intent_id": self.intent_id,
            "locked_until": self.locked_until,
        }


class VoiceAdminMaintenanceStore:
    def __init__(self, *, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> VoiceAdminMaintenanceState:
        if not self._path.exists():
            return VoiceAdminMaintenanceState()
        payload = json.loads(self._path.read_text())
        return VoiceAdminMaintenanceState.model_validate(payload)

    def save(self, state: VoiceAdminMaintenanceState) -> VoiceAdminMaintenanceState:
        updated = state.model_copy(update={"updated_at": utc_now_iso()})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(updated.model_dump_json(indent=2))
        temp_path.replace(self._path)
        return updated

    def status(self) -> dict[str, object]:
        state = self._clear_expired_lockout(self.load())
        return {
            "schema_version": state.schema_version,
            "enabled": state.enabled,
            "admin_speaker_public_ids": list(state.admin_speaker_public_ids),
            "enabled_intents": _normalized_enabled_intents(state.enabled_intents),
            "passcode_configured": bool(state.passcode_hash),
            "passcode_storage": "pbkdf2_sha256" if state.passcode_hash else None,
            "failed_attempts": state.failed_attempts,
            "locked": bool(state.locked_until),
            "locked_until": state.locked_until,
            "path": str(self._path),
            "updated_at": state.updated_at,
        }

    def update_settings(
        self,
        *,
        enabled: bool | None = None,
        admin_speaker_public_ids: list[str] | None = None,
        enabled_intents: dict[str, bool] | None = None,
    ) -> dict[str, object]:
        state = self._clear_expired_lockout(self.load())
        update: dict[str, object] = {}
        if enabled is not None:
            update["enabled"] = bool(enabled)
        if admin_speaker_public_ids is not None:
            update["admin_speaker_public_ids"] = sorted(
                {str(item).strip() for item in admin_speaker_public_ids if str(item).strip()}
            )
        if enabled_intents is not None:
            update["enabled_intents"] = _normalized_enabled_intents(enabled_intents)
        self.save(state.model_copy(update=update))
        return self.status()

    def set_passcode(self, passcode: str) -> dict[str, object]:
        normalized = _normalize_passcode(passcode)
        if normalized is None:
            raise ValueError("passcode_must_be_four_digits")
        salt = secrets.token_hex(16)
        digest = _hash_passcode(normalized, salt=salt, iterations=PASSCODE_ITERATIONS)
        state = self._clear_expired_lockout(self.load())
        updated = state.model_copy(
            update={
                "passcode_hash": {
                    "algorithm": "pbkdf2_sha256",
                    "iterations": PASSCODE_ITERATIONS,
                    "salt": salt,
                    "digest": digest,
                },
                "failed_attempts": 0,
                "locked_until": None,
            }
        )
        self.save(updated)
        return self.status()

    def evaluate(
        self,
        *,
        text: str,
        intent_id: str | None,
        speaker_identity: dict[str, object] | None,
        audio_quality: dict[str, object] | None,
        now: datetime | None = None,
    ) -> AdminMaintenanceDecision:
        state = self._clear_expired_lockout(self.load(), now=now)
        now_dt = now or datetime.now(UTC)
        intent = str(intent_id or "").strip()
        if not state.enabled:
            return AdminMaintenanceDecision(False, "admin_maintenance_disabled", intent)
        if intent not in ADMIN_MAINTENANCE_INTENT_IDS:
            return AdminMaintenanceDecision(False, "admin_intent_unknown", intent)
        if not _normalized_enabled_intents(state.enabled_intents).get(intent):
            return AdminMaintenanceDecision(False, "admin_intent_disabled", intent)
        if state.locked_until:
            return AdminMaintenanceDecision(False, "admin_maintenance_locked", intent, state.locked_until)
        if not state.passcode_hash:
            return AdminMaintenanceDecision(False, "admin_passcode_not_configured", intent)
        speaker_reason = _speaker_rejection_reason(speaker_identity, state.admin_speaker_public_ids)
        if speaker_reason:
            return AdminMaintenanceDecision(False, speaker_reason, intent)
        audio_reason = _audio_rejection_reason(audio_quality)
        if audio_reason:
            return AdminMaintenanceDecision(False, audio_reason, intent)
        passcode = extract_spoken_passcode(text)
        if passcode is None:
            return self._failed_passcode(state, intent=intent, reason="admin_passcode_missing", now=now_dt)
        if not _verify_passcode(passcode, state.passcode_hash):
            return self._failed_passcode(state, intent=intent, reason="admin_passcode_wrong", now=now_dt)
        if state.failed_attempts:
            self.save(state.model_copy(update={"failed_attempts": 0, "locked_until": None}))
        return AdminMaintenanceDecision(True, "admin_maintenance_allowed", intent)

    def _failed_passcode(
        self,
        state: VoiceAdminMaintenanceState,
        *,
        intent: str,
        reason: str,
        now: datetime,
    ) -> AdminMaintenanceDecision:
        failed_attempts = int(state.failed_attempts or 0) + 1
        locked_until = None
        if failed_attempts >= MAX_FAILED_ATTEMPTS:
            locked_until = (now + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
        self.save(state.model_copy(update={"failed_attempts": failed_attempts, "locked_until": locked_until}))
        return AdminMaintenanceDecision(False, "admin_maintenance_locked" if locked_until else reason, intent, locked_until)

    def _clear_expired_lockout(
        self,
        state: VoiceAdminMaintenanceState,
        *,
        now: datetime | None = None,
    ) -> VoiceAdminMaintenanceState:
        if not state.locked_until:
            return state
        locked_until = _parse_dt(state.locked_until)
        if locked_until is not None and locked_until > (now or datetime.now(UTC)):
            return state
        updated = state.model_copy(update={"locked_until": None, "failed_attempts": 0})
        return self.save(updated)


def extract_spoken_passcode(text: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    if not normalized:
        return None
    digits = re.findall(r"\b\d{4}\b", normalized)
    if digits:
        return digits[-1]
    tokens = normalized.split()
    for index in range(0, max(0, len(tokens) - 3)):
        code = "".join(PASSCODE_WORDS.get(token, token if token.isdigit() and len(token) == 1 else "") for token in tokens[index : index + 4])
        if len(code) == 4 and code.isdigit():
            return code
    return None


def redact_spoken_passcodes(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\b\d(?:[\s-]*\d){3}\b", "[passcode]", value)
    pattern = r"\b(?:" + "|".join(sorted(PASSCODE_WORDS, key=len, reverse=True)) + r")(?:\s+(?:" + "|".join(sorted(PASSCODE_WORDS, key=len, reverse=True)) + r")){3}\b"
    return re.sub(pattern, "[passcode]", value, flags=re.IGNORECASE)


def _normalize_passcode(value: str) -> str | None:
    digits = re.sub(r"\D+", "", str(value or ""))
    return digits if len(digits) == 4 else None


def _hash_passcode(passcode: str, *, salt: str, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", passcode.encode(), salt.encode(), iterations)
    return digest.hex()


def _verify_passcode(passcode: str, hashed: dict[str, Any]) -> bool:
    if hashed.get("algorithm") != "pbkdf2_sha256":
        return False
    salt = str(hashed.get("salt") or "")
    digest = str(hashed.get("digest") or "")
    iterations = int(hashed.get("iterations") or PASSCODE_ITERATIONS)
    return hmac.compare_digest(_hash_passcode(passcode, salt=salt, iterations=iterations), digest)


def _normalized_enabled_intents(value: dict[str, bool]) -> dict[str, bool]:
    raw = value if isinstance(value, dict) else {}
    return {intent_id: bool(raw.get(intent_id)) for intent_id in ADMIN_MAINTENANCE_INTENT_IDS}


def _speaker_rejection_reason(speaker: dict[str, object] | None, admin_ids: list[str]) -> str | None:
    if not speaker:
        return "admin_speaker_missing"
    if speaker.get("status") not in {"identified", "verified"}:
        return "admin_speaker_not_identified"
    speaker_id = str(speaker.get("speaker_public_id") or "").strip()
    if not speaker_id or speaker_id not in set(admin_ids):
        return "admin_speaker_not_configured"
    if speaker.get("admin_eligible") is not True:
        return "admin_speaker_not_eligible"
    confidence = _float_or_none(speaker.get("confidence"))
    if confidence is None or confidence < MIN_ADMIN_CONFIDENCE:
        return "admin_speaker_confidence_too_low"
    margin = _float_or_none(speaker.get("score_margin"))
    if margin is None or margin < MIN_ADMIN_SCORE_MARGIN:
        return "admin_speaker_margin_too_low"
    return None


def _audio_rejection_reason(audio_quality: dict[str, object] | None) -> str | None:
    if not audio_quality:
        return "admin_audio_quality_missing"
    warnings = {str(item) for item in audio_quality.get("warnings") or []}
    blocked = sorted(warnings & BAD_AUDIO_WARNINGS)
    if blocked:
        return f"admin_audio_quality_{blocked[0]}"
    return None


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
