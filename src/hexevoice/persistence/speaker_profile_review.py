from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


REVIEWABLE_STATUSES = {"pending"}
TERMINAL_STATUSES = {"approved", "rejected", "discarded"}
MIN_REVIEW_CONFIDENCE = 0.85
MIN_REVIEW_SCORE_MARGIN = 0.08
SENSITIVE_KEYS = {
    "audio",
    "audio_base64",
    "audio_bytes",
    "embedding",
    "embeddings",
    "biometric_template",
    "biometric_templates",
    "passcode",
    "raw_audio",
    "values",
    "voiceprint",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SpeakerProfileLearningCandidate(BaseModel):
    candidate_id: str = Field(default_factory=lambda: f"profile-learning-{uuid4().hex}")
    status: str = "pending"
    profile_id: str | None = None
    speaker_public_id: str
    display_name: str | None = None
    session_id: str | None = None
    endpoint_id: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    evidence: dict[str, Any] = Field(default_factory=dict)
    sample: dict[str, Any] | None = None
    review: dict[str, Any] | None = None


class SpeakerProfileReviewState(BaseModel):
    schema_version: int = 1
    candidates: list[SpeakerProfileLearningCandidate] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)


class SpeakerProfileReviewStore:
    def __init__(self, *, path: Path, max_candidates: int = 200) -> None:
        self._path = path
        self._max_candidates = max(1, max_candidates)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> SpeakerProfileReviewState:
        if not self._path.exists():
            return SpeakerProfileReviewState()
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        return SpeakerProfileReviewState.model_validate(payload)

    def save(self, state: SpeakerProfileReviewState) -> SpeakerProfileReviewState:
        updated = state.model_copy(update={"updated_at": utc_now_iso()})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
        temp_path.replace(self._path)
        return updated

    def list_candidates(self, *, status: str | None = None) -> list[dict[str, Any]]:
        candidates = [candidate.model_dump(mode="json") for candidate in self.load().candidates]
        if status:
            normalized = status.strip().lower()
            candidates = [candidate for candidate in candidates if candidate.get("status") == normalized]
        return candidates

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._find(candidate_id)
        if candidate is None:
            raise ValueError("profile_learning_candidate_not_found")
        return candidate.model_dump(mode="json")

    def add_candidate(
        self,
        *,
        speaker_public_id: str,
        display_name: str | None = None,
        profile_id: str | None = None,
        session_id: str | None = None,
        endpoint_id: str | None = None,
        evidence: dict[str, Any] | None = None,
        sample: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_speaker_id = str(speaker_public_id or "").strip()
        if not normalized_speaker_id:
            raise ValueError("speaker_public_id_required")
        sanitized_evidence = _sanitize_mapping(evidence or {})
        if not _candidate_passes_guardrails(sanitized_evidence):
            raise ValueError("profile_learning_candidate_guardrail_failed")
        state = self.load()
        dedupe_key = (session_id, normalized_speaker_id)
        for existing in state.candidates:
            if existing.status == "pending" and (existing.session_id, existing.speaker_public_id) == dedupe_key:
                return existing.model_dump(mode="json")
        candidate = SpeakerProfileLearningCandidate(
            speaker_public_id=normalized_speaker_id,
            display_name=display_name,
            profile_id=profile_id,
            session_id=session_id,
            endpoint_id=endpoint_id,
            evidence=sanitized_evidence,
            sample=_sanitized_sample(sample),
        )
        state.candidates.insert(0, candidate)
        state.candidates = state.candidates[: self._max_candidates]
        self.save(state)
        return candidate.model_dump(mode="json")

    def mark_reviewed(
        self,
        *,
        candidate_id: str,
        decision: str,
        reviewed_by: str | None = None,
        reason: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = str(decision or "").strip().lower()
        if normalized not in TERMINAL_STATUSES:
            raise ValueError("unsupported_profile_learning_review_decision")
        state = self.load()
        next_candidates: list[SpeakerProfileLearningCandidate] = []
        reviewed: SpeakerProfileLearningCandidate | None = None
        for candidate in state.candidates:
            if candidate.candidate_id != candidate_id:
                next_candidates.append(candidate)
                continue
            if candidate.status not in REVIEWABLE_STATUSES:
                raise ValueError("profile_learning_candidate_already_reviewed")
            review = {
                "decision": normalized,
                "reviewed_by": str(reviewed_by or "").strip() or None,
                "reason": str(reason or "").strip() or None,
                "reviewed_at": utc_now_iso(),
                "result": _sanitize_mapping(result or {}),
            }
            reviewed = candidate.model_copy(update={"status": normalized, "updated_at": utc_now_iso(), "review": review})
            next_candidates.append(reviewed)
        if reviewed is None:
            raise ValueError("profile_learning_candidate_not_found")
        state.candidates = next_candidates
        self.save(state)
        return reviewed.model_dump(mode="json")


    def _find(self, candidate_id: str) -> SpeakerProfileLearningCandidate | None:
        normalized = str(candidate_id or "").strip()
        for candidate in self.load().candidates:
            if candidate.candidate_id == normalized:
                return candidate
        return None


def _candidate_passes_guardrails(evidence: dict[str, Any]) -> bool:
    confidence = _float_or_none(evidence.get("confidence"))
    margin = _float_or_none(evidence.get("score_margin"))
    if confidence is None or confidence < MIN_REVIEW_CONFIDENCE:
        return False
    if margin is None or margin < MIN_REVIEW_SCORE_MARGIN:
        return False
    return bool(evidence.get("learning_eligible"))


def _sanitized_sample(sample: dict[str, Any] | None) -> dict[str, Any] | None:
    if not sample:
        return None
    raw_policy = str(sample.get("raw_audio_policy") or "").strip()
    if raw_policy != "debug_retention_one_day":
        return None
    audio_base64 = str(sample.get("audio_base64") or "").strip()
    if not audio_base64:
        return None
    return {
        "sample_id": str(sample.get("sample_id") or f"profile-learning-{uuid4().hex}"),
        "audio_base64": audio_base64,
        "sample_rate_hz": sample.get("sample_rate_hz"),
        "encoding": sample.get("encoding"),
        "phrase_set_version": sample.get("phrase_set_version"),
        "phrase_id": sample.get("phrase_id"),
        "phrase_text": sample.get("phrase_text"),
        "phrase_status": "accepted",
        "raw_audio_policy": raw_policy,
        "expires_at": sample.get("expires_at"),
    }


def _sanitize_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = str(key)
        if normalized_key.lower() in SENSITIVE_KEYS:
            continue
        sanitized[normalized_key] = _sanitize_value(item)
    return sanitized


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _sanitize_mapping(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
