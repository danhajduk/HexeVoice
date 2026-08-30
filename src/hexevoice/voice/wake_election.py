from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4


WAKE_ELECTION_SCHEMA_VERSION = 1
DEFAULT_WAKE_ELECTION_WINDOW_MS = 250

SOURCE_PRIORITIES = {
    "button": 100,
    "manual": 95,
    "endpoint_micro_wake_word": 80,
    "endpoint_wake_word": 75,
    "backend_openwakeword": 60,
    "openwakeword": 55,
    "unknown": 0,
}


@dataclass(frozen=True)
class WakeCandidate:
    endpoint_id: str
    session_id: str
    source: str
    model: str | None = None
    confidence: float | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    detected_at: datetime | None = None
    chunk_index: int | None = None
    chunk_count: int | None = None
    frame_level: int | None = None
    speech_peak_level: int | None = None
    noise_floor_level: int | None = None
    ambient_level: int | None = None
    snr_db: float | None = None
    endpoint_audio_profile_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    candidate_id: str | None = None

    def __post_init__(self) -> None:
        source = str(self.source or "unknown").strip() or "unknown"
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "received_at", _with_utc(self.received_at))
        if self.detected_at is not None:
            object.__setattr__(self, "detected_at", _with_utc(self.detected_at))
        if self.confidence is not None:
            confidence = max(0.0, min(1.0, float(self.confidence)))
            object.__setattr__(self, "confidence", confidence)
        if self.candidate_id is None:
            object.__setattr__(self, "candidate_id", f"wake_candidate_{uuid4().hex}")

    def rank_key(self) -> tuple[float, int, float, str]:
        timestamp = self.detected_at or self.received_at
        return (
            self.score(),
            SOURCE_PRIORITIES.get(self.source, 10),
            -timestamp.timestamp(),
            self.endpoint_id,
        )

    def score(self) -> float:
        breakdown = self.score_breakdown()
        return float(breakdown["score"])

    def score_breakdown(self) -> dict[str, Any]:
        confidence = float(self.confidence or 0.0)
        snr_bonus = 0.0
        if self.snr_db is not None:
            snr_bonus = min(0.08, max(0.0, float(self.snr_db)) / 40.0 * 0.08)

        separation_bonus = 0.0
        speech = self.speech_peak_level if self.speech_peak_level is not None else self.frame_level
        noise = self.noise_floor_level if self.noise_floor_level is not None else self.ambient_level
        if speech is not None and noise is not None and speech > noise:
            separation_bonus = min(0.05, (float(speech) - float(noise)) / 32768.0 * 0.05)

        level_bonus = 0.0
        if speech is not None:
            level_bonus = min(0.02, max(0.0, float(speech)) / 32768.0 * 0.02)

        score = min(1.0, confidence + snr_bonus + separation_bonus + level_bonus)
        return {
            "score": round(score, 6),
            "confidence": confidence,
            "snr_bonus": round(snr_bonus, 6),
            "speech_noise_separation_bonus": round(separation_bonus, 6),
            "level_bonus": round(level_bonus, 6),
            "metrics_present": {
                "confidence": self.confidence is not None,
                "frame_level": self.frame_level is not None,
                "speech_peak_level": self.speech_peak_level is not None,
                "noise_floor_level": self.noise_floor_level is not None,
                "ambient_level": self.ambient_level is not None,
                "snr_db": self.snr_db is not None,
            },
        }

    def model_dump(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "endpoint_id": self.endpoint_id,
            "session_id": self.session_id,
            "source": self.source,
            "model": self.model,
            "confidence": self.confidence,
            "received_at": self.received_at.isoformat(),
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "chunk_index": self.chunk_index,
            "chunk_count": self.chunk_count,
            "frame_level": self.frame_level,
            "speech_peak_level": self.speech_peak_level,
            "noise_floor_level": self.noise_floor_level,
            "ambient_level": self.ambient_level,
            "snr_db": self.snr_db,
            "endpoint_audio_profile_version": self.endpoint_audio_profile_version,
            "score": self.score(),
            "score_breakdown": self.score_breakdown(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class WakeElectionDecision:
    election_id: str
    accepted: bool
    reason: str
    window_ms: int
    candidate: WakeCandidate | None
    winner: WakeCandidate | None
    candidates: tuple[WakeCandidate, ...]
    decided_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def model_dump(self) -> dict[str, Any]:
        return {
            "schema_version": WAKE_ELECTION_SCHEMA_VERSION,
            "election_id": self.election_id,
            "accepted": self.accepted,
            "reason": self.reason,
            "window_ms": self.window_ms,
            "decided_at": _with_utc(self.decided_at).isoformat(),
            "candidate": self.candidate.model_dump() if self.candidate else None,
            "winner": self.winner.model_dump() if self.winner else None,
            "candidates": [candidate.model_dump() for candidate in self.candidates],
        }


class WakeCandidateElection:
    def __init__(
        self,
        *,
        window_ms: int = DEFAULT_WAKE_ELECTION_WINDOW_MS,
        max_recent_candidates: int = 50,
        max_recent_decisions: int = 20,
    ) -> None:
        self.window_ms = max(0, int(window_ms))
        self._max_recent_candidates = max(1, int(max_recent_candidates))
        self._max_recent_decisions = max(1, int(max_recent_decisions))
        self._pending_candidates: list[WakeCandidate] = []
        self._recent_candidates: list[dict[str, Any]] = []
        self._recent_decisions: list[dict[str, Any]] = []
        self._active_decision: WakeElectionDecision | None = None

    def submit_candidate(self, candidate: WakeCandidate) -> WakeElectionDecision:
        self._expire_pending(reference_at=candidate.received_at)
        self._record_candidate(candidate)

        if self._active_decision is not None and self._decision_is_active(self._active_decision, candidate.received_at):
            accepted = _same_candidate(candidate, self._active_decision.winner)
            decision = WakeElectionDecision(
                election_id=self._active_decision.election_id,
                accepted=accepted,
                reason="winner_confirmed" if accepted else "stand_down_existing_winner",
                window_ms=self.window_ms,
                candidate=candidate,
                winner=self._active_decision.winner,
                candidates=(*self._active_decision.candidates, candidate),
                decided_at=candidate.received_at,
            )
            self._record_decision(decision)
            return decision

        self._pending_candidates.append(candidate)
        candidates = tuple(self._pending_candidates)
        winner = choose_wake_election_winner(candidates)
        accepted = _same_candidate(candidate, winner)
        decision = WakeElectionDecision(
            election_id=f"wake_election_{uuid4().hex}",
            accepted=accepted,
            reason="winner_selected" if accepted else "stand_down_lower_rank",
            window_ms=self.window_ms,
            candidate=candidate,
            winner=winner,
            candidates=candidates,
            decided_at=candidate.received_at,
        )
        if accepted:
            self._active_decision = decision
        self._record_decision(decision)
        return decision

    def decide_candidates(self, candidates: list[WakeCandidate] | tuple[WakeCandidate, ...]) -> WakeElectionDecision:
        ordered_candidates = tuple(sorted(candidates, key=lambda candidate: candidate.received_at))
        winner = choose_wake_election_winner(ordered_candidates)
        decided_at = ordered_candidates[-1].received_at if ordered_candidates else datetime.now(UTC)
        decision = WakeElectionDecision(
            election_id=f"wake_election_{uuid4().hex}",
            accepted=winner is not None,
            reason="winner_selected" if winner is not None else "no_candidates",
            window_ms=self.window_ms,
            candidate=winner,
            winner=winner,
            candidates=ordered_candidates,
            decided_at=decided_at,
        )
        self._record_decision(decision)
        return decision

    def status(self) -> dict[str, Any]:
        self._expire_pending(reference_at=datetime.now(UTC))
        active = self._active_decision
        return {
            "enabled": True,
            "window_ms": self.window_ms,
            "active_decision": active.model_dump() if active else None,
            "pending_candidate_count": len(self._pending_candidates),
            "recent_candidates": list(self._recent_candidates),
            "recent_decisions": list(self._recent_decisions),
        }

    def _expire_pending(self, *, reference_at: datetime) -> None:
        reference_at = _with_utc(reference_at)
        window = timedelta(milliseconds=self.window_ms)
        self._pending_candidates = [
            candidate for candidate in self._pending_candidates if reference_at - candidate.received_at <= window
        ]
        if self._active_decision is not None and not self._decision_is_active(self._active_decision, reference_at):
            self._active_decision = None

    def _decision_is_active(self, decision: WakeElectionDecision, reference_at: datetime) -> bool:
        if decision.winner is None:
            return False
        return _with_utc(reference_at) - decision.winner.received_at <= timedelta(milliseconds=self.window_ms)

    def _record_candidate(self, candidate: WakeCandidate) -> None:
        self._recent_candidates.insert(0, candidate.model_dump())
        del self._recent_candidates[self._max_recent_candidates :]

    def _record_decision(self, decision: WakeElectionDecision) -> None:
        self._recent_decisions.insert(0, decision.model_dump())
        del self._recent_decisions[self._max_recent_decisions :]


def choose_wake_election_winner(candidates: list[WakeCandidate] | tuple[WakeCandidate, ...]) -> WakeCandidate | None:
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.rank_key())


def _same_candidate(left: WakeCandidate | None, right: WakeCandidate | None) -> bool:
    return left is not None and right is not None and left.candidate_id == right.candidate_id


def _with_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
