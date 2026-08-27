from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import sys
import wave

from hexevoice.speaker_id import SpeakerIdProviderUnavailable
from hexevoice.speaker_id import available_provider_ids
from hexevoice.speaker_id import create_speaker_id_adapter
from hexevoice.speaker_id import load_wav_audio
from hexevoice.speaker_id.benchmark import run_speaker_id_benchmark


def write_fixture(path: Path, *, frequency_hz: float = 220.0) -> Path:
    sample_rate = 16000
    frame_count = sample_rate
    amplitude = 0.16
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            sample = int(math.sin(2 * math.pi * frequency_hz * (index / sample_rate)) * amplitude * 32767)
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))
    return path


def test_deterministic_adapter_extracts_and_scores_embeddings(tmp_path):
    clip = write_fixture(tmp_path / "speaker-a.wav")
    adapter = create_speaker_id_adapter("deterministic_signal")

    first = adapter.extract_embedding(clip)
    second = adapter.extract_embedding(clip)
    score = adapter.score_embeddings(first, second, threshold=0.99)

    assert first.provider_id == "deterministic_signal"
    assert first.dimensions == 32
    assert first.audio_duration_ms == 1000
    assert first.sample_rate_hz == 16000
    assert score.score == 1.0
    assert score.accepted is True
    assert score.score_margin == 0.01


def test_threshold_handling_rejects_low_score_pair(tmp_path):
    left_clip = write_fixture(tmp_path / "speaker-a.wav", frequency_hz=220.0)
    right_clip = write_fixture(tmp_path / "speaker-b.wav", frequency_hz=615.0)
    adapter = create_speaker_id_adapter("deterministic_signal")

    left = adapter.extract_embedding(left_clip)
    right = adapter.extract_embedding(right_clip)
    score = adapter.score_embeddings(left, right, threshold=0.99)

    assert score.accepted is False
    assert score.score_margin < 0


def test_wav_loader_normalizes_pcm16_audio(tmp_path):
    clip = write_fixture(tmp_path / "speaker-a.wav")

    audio = load_wav_audio(clip)

    assert audio.sample_rate_hz == 16000
    assert audio.channels == 1
    assert audio.duration_ms == 1000
    assert audio.source_path == clip.as_posix()
    assert max(audio.samples) <= 1.0
    assert min(audio.samples) >= -1.0


def test_optional_provider_status_is_import_safe(tmp_path):
    clip = write_fixture(tmp_path / "speaker-a.wav")
    adapter = create_speaker_id_adapter("speechbrain_ecapa_tdnn")
    status = adapter.status()

    assert status["provider_id"] == "speechbrain_ecapa_tdnn"
    assert status["loaded"] is False
    assert "metadata" in status
    if status["available"] is False:
        try:
            adapter.extract_embedding(clip)
        except SpeakerIdProviderUnavailable:
            pass
        else:
            raise AssertionError("missing optional provider did not fail clearly")


def test_benchmark_emits_provider_metadata_and_scores(tmp_path):
    clip = write_fixture(tmp_path / "speaker-a.wav")

    result = run_speaker_id_benchmark(
        clips=[clip],
        provider_ids=["deterministic_signal"],
        repeat=2,
        threshold=0.9,
        device_label="cpu",
    )

    assert result["schema_version"] == 1
    assert result["device_label"] == "cpu"
    assert result["runtime"]["python"]
    assert result["clip_count"] == 1
    provider = result["providers"][0]
    assert provider["provider_id"] == "deterministic_signal"
    assert provider["metadata"]["embedding_dimensions"] == 32
    assert provider["memory_rss_kb_before"] is not None
    assert provider["memory_rss_kb_after"] is not None
    assert provider["memory_rss_kb_delta"] is not None
    assert provider["clips"][0]["duration_ms_mean"] is not None
    assert provider["clips"][0]["embedding_dimensions"] == 32
    assert provider["scores"][0]["accepted"] is True


def test_benchmark_script_can_generate_fixtures_and_write_json(tmp_path):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "benchmark-speaker-id.py"
    spec = importlib.util.spec_from_file_location("benchmark_speaker_id", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["benchmark_speaker_id"] = module
    spec.loader.exec_module(module)

    fixture = module.generate_fixture(tmp_path / "fixture.wav", frequency_hz=220.0)
    providers = module.provider_ids_from_arg("deterministic_signal")
    result = run_speaker_id_benchmark(clips=[fixture], provider_ids=providers)
    output_path = tmp_path / "benchmark.json"
    output_path.write_text(json.dumps(result), encoding="utf-8")

    assert fixture.exists()
    assert providers == ["deterministic_signal"]
    assert json.loads(output_path.read_text(encoding="utf-8"))["providers"][0]["provider_id"] == "deterministic_signal"


def test_provider_catalog_includes_planned_engines():
    assert available_provider_ids() == [
        "deterministic_signal",
        "speechbrain_ecapa_tdnn",
        "wespeaker",
        "pyannote_audio",
        "nvidia_nemo_speaker",
    ]
