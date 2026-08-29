from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from statistics import mean


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


def build_long_window_placement_report(
    *,
    window: dict[str, object],
    passive_samples: list[dict[str, object]],
    active_reports: list[dict[str, object]],
) -> dict[str, object]:
    metrics = [sample.get("metrics") for sample in passive_samples if isinstance(sample.get("metrics"), dict)]
    ambient_values = _numeric_values(metrics, "ambient_rms")
    peak_values = _numeric_values(metrics, "peak")
    clipping_ratios = _numeric_values(metrics, "clipping_ratio")
    snr_values = _numeric_values(metrics, "snr_db") + _active_snr_values(active_reports)
    speech_like_count = sum(1 for metric in metrics if _speech_like(metric))
    total_samples = len(metrics)
    active_stt = _active_success_rate(active_reports, "stt")
    active_speaker = _active_success_rate(active_reports, "speaker_id")

    warnings: list[str] = []
    score = 100
    average_ambient = mean(ambient_values) if ambient_values else None
    peak_noise = max(peak_values) if peak_values else None
    clipping_frequency = _ratio(sum(1 for value in clipping_ratios if value > 0), len(clipping_ratios))
    speech_like_frequency = _ratio(speech_like_count, total_samples)
    snr_average = mean(snr_values) if snr_values else None

    if average_ambient is None:
        warnings.append("missing_passive_ambient")
        score -= 20
    elif average_ambient >= 0.08:
        warnings.append("high_ambient_noise")
        score -= 20
    elif average_ambient >= 0.04:
        warnings.append("elevated_ambient_noise")
        score -= 10
    if peak_noise is not None and peak_noise >= 0.85:
        warnings.append("peak_noise_events")
        score -= 10
    if clipping_frequency is not None and clipping_frequency >= 0.05:
        warnings.append("passive_clipping")
        score -= 15
    if speech_like_frequency is not None and speech_like_frequency >= 0.3:
        warnings.append("frequent_background_speech_like_activity")
        score -= 10
    if snr_average is not None and snr_average < 15:
        warnings.append("low_snr_distribution")
        score -= 15
    if active_stt["rate"] is not None and active_stt["rate"] < 0.8:
        warnings.append("active_stt_inconsistent")
        score -= 15
    if active_speaker["rate"] is not None and active_speaker["rate"] < 0.8:
        warnings.append("active_speaker_id_inconsistent")
        score -= 10

    score = max(0, min(100, int(round(score))))
    recommendation = "placement_good"
    if "missing_passive_ambient" in warnings:
        recommendation = "collect_more_passive_samples"
    elif any(warning in warnings for warning in ("high_ambient_noise", "frequent_background_speech_like_activity", "low_snr_distribution")):
        recommendation = "move_endpoint_or_reduce_background_noise"
    elif "passive_clipping" in warnings or "peak_noise_events" in warnings:
        recommendation = "move_endpoint_farther_or_reduce_input_gain"
    elif any(warning.startswith("active_") for warning in warnings):
        recommendation = "rerun_active_tests_from_normal_positions"

    return {
        "schema_version": 1,
        "mode": "long_window",
        "calibration_id": window.get("calibration_id"),
        "endpoint_id": window.get("endpoint_id"),
        "room": window.get("room"),
        "zone": window.get("zone"),
        "sample_count": total_samples,
        "active_test_count": len(active_reports),
        "ambient": {
            "average_rms": _round_or_none(average_ambient),
            "peak": _round_or_none(peak_noise),
            "hourly_average_rms": _hourly_ambient(passive_samples),
            "peak_noise_periods": _peak_noise_periods(passive_samples),
        },
        "activity": {
            "speech_like_count": speech_like_count,
            "speech_like_frequency": _round_or_none(speech_like_frequency),
            "clipping_frequency": _round_or_none(clipping_frequency),
        },
        "snr": {
            "count": len(snr_values),
            "average_db": round(snr_average, 2) if snr_average is not None else None,
            "minimum_db": round(min(snr_values), 2) if snr_values else None,
            "maximum_db": round(max(snr_values), 2) if snr_values else None,
        },
        "active_tests": {
            "stt_success": active_stt,
            "speaker_id_reliability": active_speaker,
        },
        "score": score,
        "recommendation": recommendation,
        "warnings": warnings,
        "privacy": {
            "metrics_only": True,
            "stt_called_for_passive_samples": False,
            "speaker_id_called_for_passive_samples": False,
            "raw_audio_persisted": False,
        },
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


def _round_or_none(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _numeric_values(metrics: list[object], key: str) -> list[float]:
    return [
        float(metric[key])
        for metric in metrics
        if isinstance(metric, dict) and isinstance(metric.get(key), (int, float))
    ]


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _speech_like(metric: object) -> bool:
    if not isinstance(metric, dict):
        return False
    if isinstance(metric.get("speech_like_activity"), bool):
        return bool(metric["speech_like_activity"])
    ratio = metric.get("speech_like_activity_ratio")
    return isinstance(ratio, (int, float)) and float(ratio) > 0.1


def _hourly_ambient(samples: list[dict[str, object]]) -> list[dict[str, object]]:
    buckets: dict[str, list[float]] = {}
    for sample in samples:
        metrics = sample.get("metrics")
        value = metrics.get("ambient_rms") if isinstance(metrics, dict) else None
        observed_at = str(sample.get("observed_at") or "")
        if not isinstance(value, (int, float)) or len(observed_at) < 13:
            continue
        hour = observed_at[11:13]
        if not hour.isdigit():
            continue
        buckets.setdefault(hour, []).append(float(value))
    return [
        {"hour": hour, "average_rms": round(mean(values), 6), "sample_count": len(values)}
        for hour, values in sorted(buckets.items())
    ]


def _peak_noise_periods(samples: list[dict[str, object]]) -> list[dict[str, object]]:
    periods: list[dict[str, object]] = []
    for sample in samples:
        metrics = sample.get("metrics")
        if not isinstance(metrics, dict):
            continue
        peak = metrics.get("peak")
        ambient = metrics.get("ambient_rms")
        value = peak if isinstance(peak, (int, float)) else ambient
        if not isinstance(value, (int, float)):
            continue
        periods.append(
            {
                "observed_at": sample.get("observed_at"),
                "peak": round(float(value), 6),
                "ambient_rms": _round_or_none(float(ambient)) if isinstance(ambient, (int, float)) else None,
            }
        )
    return sorted(periods, key=lambda item: float(item.get("peak") or 0), reverse=True)[:3]


def _active_snr_values(active_reports: list[dict[str, object]]) -> list[float]:
    values: list[float] = []
    for report in active_reports:
        audio_quality = report.get("audio_quality") if isinstance(report.get("audio_quality"), dict) else None
        value = audio_quality.get("snr_db") if audio_quality else None
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _active_success_rate(active_reports: list[dict[str, object]], key: str) -> dict[str, object]:
    total = 0
    matched = 0
    for report in active_reports:
        details = report.get(key)
        if not isinstance(details, dict):
            continue
        value = details.get("matched")
        if not isinstance(value, bool):
            continue
        total += 1
        if value:
            matched += 1
    return {"matched_count": matched, "total_count": total, "rate": round(matched / total, 3) if total else None}
