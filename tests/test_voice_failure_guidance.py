from __future__ import annotations

import json

from hexevoice.voice.failure_guidance import voice_failure_guidance


def test_voice_failure_guidance_prefers_low_snr_without_exposing_scores():
    guidance = voice_failure_guidance(
        failure_type="speaker_identity",
        reason="low_confidence",
        speaker_identity={"status": "unknown", "confidence_tier": "medium", "confidence": 0.72},
        audio_quality={"status": "low_snr", "warnings": ["low_snr"], "snr_db": 5.8},
    )

    assert guidance["message"] == "I could not recognize the speaker. The room may be too noisy. If this happens often, retrain the profile."
    assert guidance["operator_diagnostics"]["reason_codes"] == ["low_confidence", "speaker_status_unknown", "audio_quality_low_snr"]
    assert "0.72" not in guidance["message"]
    assert "5.8" not in guidance["message"]


def test_voice_failure_guidance_maps_admin_passcode_failure_safely():
    guidance = voice_failure_guidance(failure_type="admin_maintenance", reason="admin_passcode_wrong")

    assert guidance["message"] == "I could not authorize admin maintenance. The spoken passcode was not accepted."
    assert guidance["operator_diagnostics"]["reason_codes"] == ["admin_passcode_wrong"]
    assert guidance["sensitive_details_redacted"] is True
    assert "1234" not in json.dumps(guidance)


def test_voice_failure_guidance_maps_margin_to_profile_retraining_hint():
    guidance = voice_failure_guidance(
        failure_type="speaker_identity",
        reason="low_margin",
        speaker_identity={"status": "unknown", "confidence_tier": "very_high"},
        audio_quality={"status": "ok", "warnings": []},
    )

    assert "too close to another profile" in guidance["message"]
    assert guidance["user_cause"] == "margin_too_close"
