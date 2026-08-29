from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


VOICE_METRIC_SCHEMA_VERSION = 1

FORBIDDEN_METRIC_FIELDS = {
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


class VoiceMetricModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    schema_version: Literal[1] = VOICE_METRIC_SCHEMA_VERSION


class AudioQualityMetric(VoiceMetricModel):
    status: str
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    sample_rate_hz: int | None = Field(default=None, ge=1)
    channels: int = Field(default=1, ge=1)
    encoding: str | None = None
    frame_count: int = Field(ge=0)
    rms: float | None = Field(default=None, ge=0)
    peak: float | None = Field(default=None, ge=0)
    clipping_count: int = Field(default=0, ge=0)
    clipping_ratio: float = Field(default=0, ge=0, le=1)
    active_audio_ratio: float | None = Field(default=None, ge=0, le=1)
    silence_ratio: float | None = Field(default=None, ge=0, le=1)
    speech_rms: float | None = Field(default=None, ge=0)
    ambient_rms: float | None = Field(default=None, ge=0)
    ambient_peak: float | None = Field(default=None, ge=0)
    ambient_duration_ms: int = Field(default=0, ge=0)
    speech_peak: float | None = Field(default=None, ge=0)
    speech_duration_ms: int = Field(default=0, ge=0)
    snr_db: float | None = None
    snr_status: str = "unavailable"
    snr_reason: str | None = None
    source: Literal["backend", "endpoint"] = "backend"


class AmbientSnrMetric(VoiceMetricModel):
    status: str
    warnings: list[str] = Field(default_factory=list)
    ambient_duration_ms: int = Field(ge=0)
    ambient_rms: float | None = Field(default=None, ge=0)
    speech_rms: float | None = Field(default=None, ge=0)
    snr_db: float | None = None
    noise_floor_rms: float | None = Field(default=None, ge=0)
    classification: str | None = None
    classifier: str | None = None


class SpeakerIdentityMetric(VoiceMetricModel):
    status: str
    policy: str
    active: bool = False
    speaker_public_id: str | None = None
    display_name: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_tier: Literal["none", "low", "medium", "high", "very_high"] = "none"
    score: float | None = None
    score_margin: float | None = None
    provider: str | None = None
    model_id: str | None = None
    age_band: Literal["child", "teen", "adult", "unknown"] | None = None
    age_restriction_class: str | None = None
    admin_eligible: bool = False
    reason: str | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    error: str | None = None


class IdentityPolicyDecisionMetric(VoiceMetricModel):
    intent_id: str | None = None
    policy: Literal["not_required", "use_if_ready", "required", "forbidden", "household", "sensitive", "admin_maintenance"]
    decision: Literal["allow", "allow_without_identity", "follow_up", "reject", "skip"]
    required_confidence_tier: Literal["none", "low", "medium", "high", "very_high"] = "none"
    observed_confidence_tier: Literal["none", "low", "medium", "high", "very_high"] = "none"
    reason: str | None = None


class EnrollmentReadinessMetric(VoiceMetricModel):
    status: Literal["ready", "not_ready", "needs_more_samples", "poor_audio"]
    sample_count: int = Field(ge=0)
    accepted_sample_count: int = Field(ge=0)
    required_sample_count: int = Field(ge=1)
    total_speech_ms: int = Field(ge=0)
    audio_quality_status: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ValidationPhraseScoreMetric(VoiceMetricModel):
    phrase_id: str
    expected_text: str
    stt_text: str | None = None
    stt_score: float | None = Field(default=None, ge=0, le=1)
    speaker_status: str | None = None
    speaker_score: float | None = None
    speaker_confidence_tier: Literal["none", "low", "medium", "high", "very_high"] = "none"
    audio_quality_status: str | None = None
    accepted_for_training: bool = False
    warnings: list[str] = Field(default_factory=list)


class PlacementMetric(VoiceMetricModel):
    endpoint_id: str
    status: str
    window_seconds: int = Field(ge=0)
    wake_success_rate: float | None = Field(default=None, ge=0, le=1)
    stt_score_mean: float | None = Field(default=None, ge=0, le=1)
    speaker_score_mean: float | None = None
    audio_quality_status: str | None = None
    ambient_status: str | None = None
    recommendation: str | None = None
    warnings: list[str] = Field(default_factory=list)


class VoiceQualityObservationRecord(VoiceMetricModel):
    recorded_at: str
    endpoint_id: str | None = None
    session_id: str | None = None
    transcript_text: str | None = None
    transcript_chars: int | None = Field(default=None, ge=0)
    stt_provider: str | None = None
    stt_score: float | None = Field(default=None, ge=0, le=1)
    speaker_identity: SpeakerIdentityMetric | None = None
    audio_quality: AudioQualityMetric | None = None
    ambient_snr: AmbientSnrMetric | None = None
    placement: PlacementMetric | None = None


def speaker_confidence_tier(confidence: float | None) -> Literal["none", "low", "medium", "high", "very_high"]:
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


def assert_metric_payload_redacted(payload: Any) -> None:
    forbidden = _find_forbidden_metric_paths(payload)
    if forbidden:
        raise ValueError(f"metric_payload_contains_sensitive_fields: {', '.join(forbidden)}")


def _find_forbidden_metric_paths(payload: Any, *, prefix: str = "") -> list[str]:
    matches: list[str] = []
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).lower()
            path = f"{prefix}.{key}" if prefix else str(key)
            if normalized in FORBIDDEN_METRIC_FIELDS:
                matches.append(path)
            matches.extend(_find_forbidden_metric_paths(value, prefix=path))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            path = f"{prefix}[{index}]"
            matches.extend(_find_forbidden_metric_paths(value, prefix=path))
    return matches
