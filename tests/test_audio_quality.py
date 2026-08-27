from __future__ import annotations

import math

from hexevoice.voice.audio_quality import analyze_pcm_s16le_audio


def pcm_sine(*, amplitude: float, duration_ms: int = 1000, sample_rate_hz: int = 16000) -> bytes:
    frames = int(sample_rate_hz * (duration_ms / 1000))
    payload = bytearray()
    for index in range(frames):
        sample = int(math.sin(2 * math.pi * 220 * (index / sample_rate_hz)) * amplitude * 32767)
        payload.extend(sample.to_bytes(2, byteorder="little", signed=True))
    return bytes(payload)


def test_audio_quality_detects_silence():
    result = analyze_pcm_s16le_audio(b"\x00\x00" * 16000, sample_rate_hz=16000, channels=1)

    assert result.status == "silent"
    assert "silent" in result.warnings
    assert result.rms == 0
    assert result.active_audio_ratio == 0
    assert result.silence_ratio == 1


def test_audio_quality_accepts_normal_level_audio():
    result = analyze_pcm_s16le_audio(pcm_sine(amplitude=0.2), sample_rate_hz=16000, channels=1)

    assert result.status == "ok"
    assert result.warnings == []
    assert result.duration_ms == 1000
    assert result.rms is not None and result.rms > 0.1
    assert result.peak is not None and 0.19 <= result.peak <= 0.2
    assert result.speech_rms is not None
    assert result.ambient_rms is None
    assert result.snr_db is None
    assert result.snr_status == "unavailable"
    assert result.snr_reason == "missing_ambient"


def test_audio_quality_detects_clipped_audio():
    result = analyze_pcm_s16le_audio((32767).to_bytes(2, "little", signed=True) * 16000, sample_rate_hz=16000)

    assert result.status == "clipped"
    assert "clipped" in result.warnings
    assert result.clipping_count == 16000
    assert result.clipping_ratio == 1


def test_audio_quality_detects_short_audio():
    result = analyze_pcm_s16le_audio(pcm_sine(amplitude=0.2, duration_ms=120), sample_rate_hz=16000)

    assert result.status == "short_audio"
    assert "short_audio" in result.warnings
    assert result.duration_ms == 120


def test_audio_quality_detects_low_level_audio():
    result = analyze_pcm_s16le_audio(pcm_sine(amplitude=0.01), sample_rate_hz=16000)

    assert result.status == "low_level"
    assert "low_level" in result.warnings
    assert result.rms is not None and result.rms < 0.015


def test_audio_quality_reports_normal_snr_with_ambient_reference():
    result = analyze_pcm_s16le_audio(
        pcm_sine(amplitude=0.2),
        sample_rate_hz=16000,
        ambient_audio_bytes=pcm_sine(amplitude=0.01),
    )

    assert result.status == "ok"
    assert result.snr_status == "ok"
    assert result.snr_reason is None
    assert result.ambient_duration_ms == 1000
    assert result.speech_duration_ms == 1000
    assert result.ambient_rms is not None and result.ambient_rms < result.speech_rms
    assert result.ambient_peak is not None
    assert result.speech_peak is not None
    assert result.snr_db is not None and result.snr_db >= 15


def test_audio_quality_reports_short_ambient_reference_as_unavailable():
    result = analyze_pcm_s16le_audio(
        pcm_sine(amplitude=0.2),
        sample_rate_hz=16000,
        ambient_audio_bytes=pcm_sine(amplitude=0.01, duration_ms=120),
    )

    assert result.status == "ok"
    assert result.snr_status == "unavailable"
    assert result.snr_reason == "short_ambient"
    assert result.ambient_duration_ms == 120
    assert result.snr_db is None


def test_audio_quality_reports_low_snr_without_blocking_analysis():
    result = analyze_pcm_s16le_audio(
        pcm_sine(amplitude=0.2),
        sample_rate_hz=16000,
        ambient_audio_bytes=pcm_sine(amplitude=0.18),
    )

    assert result.status == "low_snr"
    assert "low_snr" in result.warnings
    assert result.snr_status == "low_snr"
    assert result.snr_reason is None
    assert result.snr_db is not None and result.snr_db < 15
