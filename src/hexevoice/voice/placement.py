from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re


def normalize_phrase(value: object) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def phrase_similarity(expected: object, observed: object) -> float:
    expected_text = normalize_phrase(expected)
    observed_text = normalize_phrase(observed)
    if not expected_text or not observed_text:
        return 0.0
    if expected_text == observed_text:
        return 1.0
    if expected_text in observed_text or observed_text in expected_text:
        shorter = min(len(expected_text), len(observed_text))
        longer = max(len(expected_text), len(observed_text))
        return round(max(0.82, shorter / longer), 4)
    return round(SequenceMatcher(None, expected_text, observed_text).ratio(), 4)


@dataclass(frozen=True)
class PlacementReportInput:
    test: dict[str, object]
    transcript: dict[str, object]
    speaker_identity: dict[str, object] | None
    audio_quality: dict[str, object]
    related_reports: list[dict[str, object]]


def build_active_placement_report(payload: PlacementReportInput) -> dict[str, object]:
    expected_phrase = str(payload.test.get("expected_phrase") or "")
    observed_text = str(payload.transcript.get("text") or "")
    similarity = phrase_similarity(expected_phrase, observed_text)
    transcript_error = payload.transcript.get("error")
    transcript_matched = bool(not transcript_error and similarity >= 0.82)
    stt_score = 0 if transcript_error else int(round(similarity * 100))

    expected_speaker = str(payload.test.get("expected_speaker_public_id") or "").strip()
    speaker = payload.speaker_identity or {}
    observed_speaker = str(speaker.get("speaker_public_id") or "").strip()
    speaker_status = str(speaker.get("status") or ("not_expected" if not expected_speaker else "unknown"))
    speaker_confidence = _float_or_none(speaker.get("confidence"))
    if not expected_speaker:
        speaker_match = None
        speaker_score = None
    elif speaker_status not in {"identified", "verified"} or not observed_speaker:
        speaker_match = False
        speaker_score = 35
    elif observed_speaker == expected_speaker:
        speaker_match = True
        speaker_score = max(60, int(round((speaker_confidence or 0.72) * 100)))
    else:
        speaker_match = False
        speaker_score = 25

    quality_warnings = [str(item) for item in payload.audio_quality.get("warnings") or []]
    audio_score = _audio_quality_score(payload.audio_quality, quality_warnings)
    consistency = _consistency_signal(
        expected_phrase=expected_phrase,
        observed_text=observed_text,
        related_reports=payload.related_reports,
    )

    if speaker_score is None:
        overall = round((stt_score * 0.6) + (audio_score * 0.4))
    else:
        overall = round((stt_score * 0.45) + (speaker_score * 0.25) + (audio_score * 0.30))
    warnings = _report_warnings(
        transcript_error=transcript_error,
        transcript_matched=transcript_matched,
        speaker_expected=bool(expected_speaker),
        speaker_match=speaker_match,
        speaker_status=speaker_status,
        quality_warnings=quality_warnings,
        consistency=consistency,
    )

    return {
        "schema_version": 1,
        "score": max(0, min(100, overall)),
        "recommendation": _recommendation(overall, warnings),
        "warnings": warnings,
        "stt": {
            "expected_phrase": expected_phrase,
            "observed_text": observed_text,
            "similarity": similarity,
            "matched": transcript_matched,
            "score": stt_score,
            "provider_id": payload.transcript.get("provider_id"),
            "model": payload.transcript.get("model"),
            "confidence": payload.transcript.get("confidence"),
            "error": transcript_error,
        },
        "speaker_id": {
            "expected_speaker_public_id": expected_speaker or None,
            "observed_speaker_public_id": observed_speaker or None,
            "status": speaker_status,
            "matched": speaker_match,
            "score": speaker_score,
            "confidence": speaker_confidence,
            "reason": speaker.get("reason"),
        },
        "audio_quality": {
            "status": payload.audio_quality.get("status"),
            "score": audio_score,
            "warnings": quality_warnings,
            "snr_db": payload.audio_quality.get("snr_db"),
            "snr_status": payload.audio_quality.get("snr_status"),
            "clipping_count": payload.audio_quality.get("clipping_count"),
            "clipping_ratio": payload.audio_quality.get("clipping_ratio"),
            "rms": payload.audio_quality.get("rms"),
            "peak": payload.audio_quality.get("peak"),
            "duration_ms": payload.audio_quality.get("duration_ms"),
            "source": payload.audio_quality.get("source"),
        },
        "consistency": consistency,
    }


def _audio_quality_score(audio_quality: dict[str, object], warnings: list[str]) -> int:
    score = 100
    penalties = {
        "silent": 80,
        "missing_audio": 80,
        "unsupported_audio": 70,
        "clipped": 40,
        "low_snr": 30,
        "short_audio": 25,
        "low_level": 20,
    }
    for warning in warnings:
        score -= penalties.get(warning, 5)
    if audio_quality.get("status") == "ok" and not warnings:
        return 100
    return max(0, min(100, score))


def _consistency_signal(
    *,
    expected_phrase: str,
    observed_text: str,
    related_reports: list[dict[str, object]],
) -> dict[str, object]:
    similarities = [phrase_similarity(expected_phrase, observed_text)]
    for report in related_reports:
        stt = report.get("stt") if isinstance(report.get("stt"), dict) else {}
        similarities.append(phrase_similarity(expected_phrase, stt.get("observed_text")))
    avg = round(sum(similarities) / len(similarities), 4) if similarities else 0.0
    return {
        "sample_count": len(similarities),
        "average_phrase_similarity": avg,
        "status": "stable" if len(similarities) >= 2 and avg >= 0.82 else "single_sample",
    }


def _report_warnings(
    *,
    transcript_error: object,
    transcript_matched: bool,
    speaker_expected: bool,
    speaker_match: bool | None,
    speaker_status: str,
    quality_warnings: list[str],
    consistency: dict[str, object],
) -> list[str]:
    warnings: list[str] = []
    if transcript_error:
        warnings.append("stt_failed")
    elif not transcript_matched:
        warnings.append("phrase_mismatch")
    if speaker_expected and speaker_match is False:
        warnings.append("unknown_speaker" if speaker_status not in {"identified", "verified"} else "speaker_mismatch")
    warnings.extend(warning for warning in quality_warnings if warning not in warnings)
    if consistency.get("status") == "single_sample":
        warnings.append("needs_more_positions")
    return warnings


def _recommendation(score: int, warnings: list[str]) -> str:
    if "clipped" in warnings:
        return "move_endpoint_farther_or_reduce_input_gain"
    if "low_snr" in warnings:
        return "reduce_background_noise_or_move_closer"
    if score >= 80:
        return "placement_good"
    if score >= 60:
        return "placement_usable_add_more_positions"
    return "move_endpoint_and_repeat_test"


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
