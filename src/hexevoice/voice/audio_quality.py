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
    snr_db: float | None = None

    def as_context(self) -> dict[str, object]:
        return asdict(self)


def analyze_pcm_s16le_audio(
    audio_bytes: bytes | None,
    *,
    sample_rate_hz: int | None,
    channels: int = 1,
    encoding: str | None = "pcm_s16le",
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
    )


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
    )


def _status_from_warnings(warnings: list[str]) -> str:
    for status in ("silent", "clipped", "short_audio", "low_level"):
        if status in warnings:
            return status
    return "ok"
