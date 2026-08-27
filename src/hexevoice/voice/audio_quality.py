from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import math
import struct


@dataclass(frozen=True)
class AudioQualityResult:
    schema_version: int
    status: str
    warnings: list[str]
    duration_ms: int
    sample_rate_hz: int | None
    channels: int
    encoding: str | None
    frame_count: int
    rms: float | None
    peak: float | None
    clipping_count: int
    clipping_ratio: float
    active_audio_ratio: float | None
    silence_ratio: float | None
    speech_rms: float | None
    ambient_rms: float | None = None
    ambient_peak: float | None = None
    ambient_duration_ms: int = 0
    speech_peak: float | None = None
    speech_duration_ms: int = 0
    snr_db: float | None = None
    snr_status: str = "unavailable"
    snr_reason: str | None = "missing_ambient"
    source: str = "backend"

    def as_context(self) -> dict[str, object]:
        return asdict(self)


def analyze_pcm_s16le_audio(
    audio_bytes: bytes | None,
    *,
    sample_rate_hz: int | None,
    channels: int = 1,
    encoding: str | None = "pcm_s16le",
    ambient_audio_bytes: bytes | None = None,
    endpoint_audio_metrics: dict[str, object] | None = None,
) -> AudioQualityResult:
    sample_rate = int(sample_rate_hz or 0)
    channel_count = max(1, int(channels or 1))
    raw = audio_bytes or b""
    if not raw:
        return _empty_result(
            status="missing_audio",
            warning="missing_audio",
            sample_rate_hz=sample_rate_hz,
            channels=channel_count,
            encoding=encoding,
        )
    if encoding != "pcm_s16le":
        return _empty_result(
            status="unsupported_audio",
            warning="unsupported_audio",
            sample_rate_hz=sample_rate_hz,
            channels=channel_count,
            encoding=encoding,
        )

    usable_length = len(raw) - (len(raw) % 2)
    samples = [sample / 32768.0 for (sample,) in struct.iter_unpack("<h", raw[:usable_length])]
    if not samples:
        return _empty_result(
            status="short_audio",
            warning="short_audio",
            sample_rate_hz=sample_rate_hz,
            channels=channel_count,
            encoding=encoding,
        )

    frame_count = len(samples) // channel_count
    duration_ms = int(round((frame_count / sample_rate) * 1000)) if sample_rate > 0 else 0
    abs_samples = [abs(sample) for sample in samples]
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    peak = max(abs_samples)
    clipping_count = sum(1 for sample in abs_samples if sample >= 0.999)
    clipping_ratio = clipping_count / len(samples)
    active_samples = [sample for sample in samples if abs(sample) >= 0.01]
    active_audio_ratio = len(active_samples) / len(samples)
    silence_ratio = 1.0 - active_audio_ratio
    speech_rms = math.sqrt(sum(sample * sample for sample in active_samples) / len(active_samples)) if active_samples else None
    warnings: list[str] = []

    if duration_ms < 300:
        warnings.append("short_audio")
    if peak <= 0.001 or rms <= 0.0005:
        warnings.append("silent")
    elif rms < 0.015:
        warnings.append("low_level")
    if clipping_ratio >= 0.01:
        warnings.append("clipped")

    ambient_metrics = _ambient_snr_metrics(
        ambient_audio_bytes,
        sample_rate_hz=sample_rate_hz,
        channels=channel_count,
        encoding=encoding,
        speech_rms=speech_rms,
        speech_peak=peak,
        speech_duration_ms=duration_ms,
        endpoint_audio_metrics=endpoint_audio_metrics,
    )
    if ambient_metrics["snr_status"] == "low_snr":
        warnings.append("low_snr")

    status = _status_from_warnings(warnings)
    return AudioQualityResult(
        schema_version=1,
        status=status,
        warnings=warnings,
        duration_ms=duration_ms,
        sample_rate_hz=sample_rate_hz,
        channels=channel_count,
        encoding=encoding,
        frame_count=frame_count,
        rms=round(rms, 6),
        peak=round(peak, 6),
        clipping_count=clipping_count,
        clipping_ratio=round(clipping_ratio, 6),
        active_audio_ratio=round(active_audio_ratio, 6),
        silence_ratio=round(silence_ratio, 6),
        speech_rms=round(speech_rms, 6) if speech_rms is not None else None,
        **ambient_metrics,
    )


def _ambient_snr_metrics(
    ambient_audio_bytes: bytes | None,
    *,
    sample_rate_hz: int | None,
    channels: int,
    encoding: str | None,
    speech_rms: float | None,
    speech_peak: float | None,
    speech_duration_ms: int,
    endpoint_audio_metrics: dict[str, object] | None,
) -> dict[str, object]:
    base = {
        "ambient_rms": None,
        "ambient_peak": None,
        "ambient_duration_ms": 0,
        "speech_peak": round(speech_peak, 6) if speech_peak is not None else None,
        "speech_duration_ms": speech_duration_ms,
        "snr_db": None,
        "snr_status": "unavailable",
        "snr_reason": "missing_ambient",
        "source": "backend",
    }
    endpoint_metrics = _endpoint_ambient_snr_metrics(
        endpoint_audio_metrics,
        speech_rms=speech_rms,
        speech_peak=speech_peak,
        speech_duration_ms=speech_duration_ms,
    )
    if endpoint_metrics is not None:
        return endpoint_metrics
    if not ambient_audio_bytes:
        return base
    if encoding != "pcm_s16le":
        return {**base, "snr_reason": "unsupported_ambient_audio"}
    sample_rate = int(sample_rate_hz or 0)
    raw = ambient_audio_bytes
    usable_length = len(raw) - (len(raw) % 2)
    samples = [sample / 32768.0 for (sample,) in struct.iter_unpack("<h", raw[:usable_length])]
    if not samples or sample_rate <= 0:
        return {**base, "snr_reason": "short_ambient"}
    frame_count = len(samples) // max(1, channels)
    ambient_duration_ms = int(round((frame_count / sample_rate) * 1000))
    abs_samples = [abs(sample) for sample in samples]
    ambient_rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    ambient_peak = max(abs_samples)
    measured = {
        **base,
        "ambient_rms": round(ambient_rms, 6),
        "ambient_peak": round(ambient_peak, 6),
        "ambient_duration_ms": ambient_duration_ms,
    }
    if ambient_duration_ms < 300:
        return {**measured, "snr_reason": "short_ambient"}
    if speech_rms is None:
        return {**measured, "snr_reason": "missing_speech"}
    if ambient_rms <= 0:
        return {**measured, "snr_reason": "ambient_silent"}
    snr_db = 20 * math.log10(max(speech_rms, 1e-12) / ambient_rms)
    return {
        **measured,
        "snr_db": round(snr_db, 2),
        "snr_status": "ok" if snr_db >= 15 else "low_snr",
        "snr_reason": None,
    }


def _endpoint_ambient_snr_metrics(
    endpoint_audio_metrics: dict[str, object] | None,
    *,
    speech_rms: float | None,
    speech_peak: float | None,
    speech_duration_ms: int,
) -> dict[str, object] | None:
    if not endpoint_audio_metrics:
        return None
    ambient_rms = _float_or_none(endpoint_audio_metrics.get("noise_floor_rms"))
    ambient_peak = _float_or_none(endpoint_audio_metrics.get("pre_roll_peak"))
    ambient_duration_ms = _int_or_zero(endpoint_audio_metrics.get("pre_roll_duration_ms"))
    endpoint_speech_peak = _float_or_none(endpoint_audio_metrics.get("speech_peak"))
    base = {
        "ambient_rms": round(ambient_rms, 6) if ambient_rms is not None else None,
        "ambient_peak": round(ambient_peak, 6) if ambient_peak is not None else None,
        "ambient_duration_ms": ambient_duration_ms,
        "speech_peak": round(endpoint_speech_peak if endpoint_speech_peak is not None else speech_peak, 6)
        if endpoint_speech_peak is not None or speech_peak is not None
        else None,
        "speech_duration_ms": speech_duration_ms,
        "snr_db": None,
        "snr_status": "unavailable",
        "snr_reason": "missing_endpoint_ambient",
        "source": "endpoint",
    }
    if ambient_rms is None:
        return base
    if ambient_duration_ms < 300:
        return {**base, "snr_reason": "short_endpoint_ambient"}
    if speech_rms is None:
        return {**base, "snr_reason": "missing_speech"}
    if ambient_rms <= 0:
        return {**base, "snr_reason": "endpoint_ambient_silent"}
    snr_db = 20 * math.log10(max(speech_rms, 1e-12) / ambient_rms)
    return {
        **base,
        "snr_db": round(snr_db, 2),
        "snr_status": "ok" if snr_db >= 15 else "low_snr",
        "snr_reason": None,
    }


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _int_or_zero(value: object) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def _empty_result(
    *,
    status: str,
    warning: str,
    sample_rate_hz: int | None,
    channels: int,
    encoding: str | None,
) -> AudioQualityResult:
    return AudioQualityResult(
        schema_version=1,
        status=status,
        warnings=[warning],
        duration_ms=0,
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        encoding=encoding,
        frame_count=0,
        rms=None,
        peak=None,
        clipping_count=0,
        clipping_ratio=0.0,
        active_audio_ratio=None,
        silence_ratio=None,
        speech_rms=None,
        speech_duration_ms=0,
        snr_reason=warning if warning in {"missing_audio", "unsupported_audio", "short_audio"} else "missing_ambient",
        source="backend",
    )


def _status_from_warnings(warnings: list[str]) -> str:
    for status in ("silent", "clipped", "short_audio", "low_level", "low_snr"):
        if status in warnings:
            return status
    return "ok"
