from __future__ import annotations

import pytest

from hexevoice.voice.audio_quality import analyze_pcm_s16le_audio
from hexevoice.voice.metric_schemas import AmbientSnrMetric
from hexevoice.voice.metric_schemas import AudioQualityMetric
from hexevoice.voice.metric_schemas import EnrollmentReadinessMetric
from hexevoice.voice.metric_schemas import IdentityPolicyDecisionMetric
from hexevoice.voice.metric_schemas import PlacementMetric
from hexevoice.voice.metric_schemas import SpeakerIdentityMetric
from hexevoice.voice.metric_schemas import ValidationPhraseScoreMetric
from hexevoice.voice.metric_schemas import VoiceQualityObservationRecord
from hexevoice.voice.metric_schemas import assert_metric_payload_redacted
from hexevoice.voice.metric_schemas import speaker_confidence_tier
from hexevoice.voice.pipeline import SpeakerIdentityResult


def test_audio_quality_result_matches_metric_schema():
    result = analyze_pcm_s16le_audio((300).to_bytes(2, "little", signed=True) * 16000, sample_rate_hz=16000)

    metric = AudioQualityMetric.model_validate(result.as_context())

    assert metric.schema_version == 1
    assert metric.status == "low_level"
    assert metric.ambient_rms is None
    assert metric.ambient_duration_ms == 0
    assert metric.snr_db is None
    assert metric.snr_status == "unavailable"
    assert metric.source == "backend"


def test_speaker_identity_context_matches_metric_schema_and_confidence_tiers():
    context = SpeakerIdentityResult(
        status="identified",
        policy="required",
        active=True,
        speaker_public_id="speaker_dan",
        display_name="Dan",
        confidence=0.96,
        score=0.96,
        score_margin=0.2,
        provider="deterministic_signal",
        model_id="deterministic-signal-v1",
    ).as_context()

    metric = SpeakerIdentityMetric.model_validate(context)

    assert metric.schema_version == 1
    assert metric.confidence_tier == "very_high"
    assert speaker_confidence_tier(0.86) == "high"
    assert speaker_confidence_tier(0.72) == "medium"
    assert speaker_confidence_tier(0.4) == "low"
    assert speaker_confidence_tier(None) == "none"


def test_metric_schema_shapes_accept_planned_track_payloads():
    ambient = AmbientSnrMetric(
        status="ok",
        ambient_duration_ms=1000,
        ambient_rms=0.01,
        speech_rms=0.12,
        snr_db=21.5,
        noise_floor_rms=0.008,
        classification="quiet_room",
        classifier="local_energy_v1",
    )
    decision = IdentityPolicyDecisionMetric(
        intent_id="admin.debug.start",
        policy="admin_maintenance",
        decision="reject",
        required_confidence_tier="very_high",
        observed_confidence_tier="high",
        reason="speaker_confidence_below_admin_threshold",
    )
    readiness = EnrollmentReadinessMetric(
        status="needs_more_samples",
        sample_count=3,
        accepted_sample_count=2,
        required_sample_count=6,
        total_speech_ms=7200,
        audio_quality_status="ok",
    )
    phrase = ValidationPhraseScoreMetric(
        phrase_id="holdout-001",
        expected_text="Hexe, what time is it?",
        stt_text="Hexe what time is it",
        stt_score=0.95,
        speaker_status="identified",
        speaker_score=0.91,
        speaker_confidence_tier="high",
        audio_quality_status="ok",
    )
    placement = PlacementMetric(
        endpoint_id="esp-kitchen",
        status="ok",
        window_seconds=172800,
        wake_success_rate=0.94,
        stt_score_mean=0.9,
        speaker_score_mean=0.88,
        audio_quality_status="ok",
        ambient_status="quiet_room",
        recommendation="keep",
    )
    observation = VoiceQualityObservationRecord(
        recorded_at="2026-08-27T03:55:00Z",
        endpoint_id="esp-kitchen",
        session_id="voice-session-1",
        transcript_chars=24,
        stt_provider="faster_whisper",
        stt_score=0.93,
        speaker_identity=SpeakerIdentityMetric(status="identified", policy="required", confidence=0.9, confidence_tier="high"),
        audio_quality=AudioQualityMetric(
            status="ok",
            duration_ms=1000,
            sample_rate_hz=16000,
            channels=1,
            encoding="pcm_s16le",
            frame_count=16000,
            rms=0.12,
            peak=0.24,
            clipping_count=0,
            clipping_ratio=0,
            active_audio_ratio=0.9,
            silence_ratio=0.1,
            speech_rms=0.13,
        ),
        ambient_snr=ambient,
        placement=placement,
    )

    for metric in (ambient, decision, readiness, phrase, placement, observation):
        assert metric.schema_version == 1
        assert_metric_payload_redacted(metric)


def test_metric_redaction_guard_rejects_sensitive_fields():
    with pytest.raises(ValueError, match="audio_base64"):
        assert_metric_payload_redacted({"audio_quality": {"status": "ok"}, "audio_base64": "abc"})
    with pytest.raises(ValueError, match="speaker.embeddings"):
        assert_metric_payload_redacted({"speaker": {"embeddings": [0.1, 0.2]}})
    with pytest.raises(ValueError, match="admin.passcode"):
        assert_metric_payload_redacted({"admin": {"passcode": "1234"}})
