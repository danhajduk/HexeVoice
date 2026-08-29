from __future__ import annotations

from typing import Any


def voice_failure_guidance(
    *,
    failure_type: str,
    reason: str | None = None,
    speaker_identity: dict[str, Any] | None = None,
    audio_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reason_codes = _reason_codes(reason=reason, speaker_identity=speaker_identity, audio_quality=audio_quality)
    cause = _primary_cause(reason_codes)
    return {
        "schema_version": 1,
        "failure_type": failure_type,
        "reason": reason,
        "reason_codes": reason_codes,
        "user_cause": cause,
        "message": _message(failure_type=failure_type, cause=cause),
        "operator_diagnostics": _operator_diagnostics(
            reason=reason,
            reason_codes=reason_codes,
            speaker_identity=speaker_identity,
            audio_quality=audio_quality,
        ),
        "sensitive_details_redacted": True,
    }


def _message(*, failure_type: str, cause: str) -> str:
    prefix = "I could not authorize admin maintenance." if failure_type == "admin_maintenance" else "I could not recognize the speaker."
    if cause == "room_noisy":
        return f"{prefix} The room may be too noisy. If this happens often, retrain the profile."
    if cause == "audio_clipped":
        return f"{prefix} The audio sounded clipped. Try speaking a little farther from the microphone."
    if cause == "speech_too_short":
        return f"{prefix} The speech was too short. Please try the phrase again."
    if cause == "confidence_too_low":
        return f"{prefix} The voice match was not confident enough. If this happens often, retrain the profile."
    if cause == "margin_too_close":
        return f"{prefix} The voice match was too close to another profile. If this happens often, retrain the profile."
    if cause == "speaker_not_admin":
        return f"{prefix} The speaker is not configured as an admin."
    if cause == "passcode_failed":
        return f"{prefix} The spoken passcode was not accepted."
    return f"{prefix} Please try again."


def _reason_codes(
    *,
    reason: str | None,
    speaker_identity: dict[str, Any] | None,
    audio_quality: dict[str, Any] | None,
) -> list[str]:
    codes: list[str] = []
    if reason:
        codes.append(str(reason))
    speaker_status = str((speaker_identity or {}).get("status") or "").strip()
    if speaker_status and speaker_status not in {"identified", "verified"}:
        codes.append(f"speaker_status_{speaker_status}")
    warnings = (audio_quality or {}).get("warnings")
    if isinstance(warnings, list):
        codes.extend(f"audio_quality_{warning}" for warning in warnings if warning)
    audio_status = str((audio_quality or {}).get("status") or "").strip()
    if audio_status and audio_status != "ok":
        codes.append(f"audio_quality_{audio_status}")
    return _dedupe(codes)


def _primary_cause(reason_codes: list[str]) -> str:
    joined = " ".join(reason_codes)
    if "low_snr" in joined or "noisy" in joined:
        return "room_noisy"
    if "clipped" in joined:
        return "audio_clipped"
    if "short_audio" in joined or "speech_too_short" in joined:
        return "speech_too_short"
    if "confidence" in joined or "low_confidence" in joined:
        return "confidence_too_low"
    if "margin" in joined:
        return "margin_too_close"
    if "not_eligible" in joined or "not_configured" in joined:
        return "speaker_not_admin"
    if "passcode" in joined or "locked" in joined:
        return "passcode_failed"
    return "speaker_not_recognized"


def _operator_diagnostics(
    *,
    reason: str | None,
    reason_codes: list[str],
    speaker_identity: dict[str, Any] | None,
    audio_quality: dict[str, Any] | None,
) -> dict[str, Any]:
    speaker = speaker_identity or {}
    quality = audio_quality or {}
    return {
        "reason": reason,
        "reason_codes": reason_codes,
        "speaker_status": speaker.get("status"),
        "speaker_public_id": speaker.get("speaker_public_id"),
        "confidence_tier": speaker.get("confidence_tier"),
        "audio_quality_status": quality.get("status"),
        "audio_quality_warnings": quality.get("warnings") if isinstance(quality.get("warnings"), list) else [],
        "snr_status": quality.get("snr_status"),
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped
