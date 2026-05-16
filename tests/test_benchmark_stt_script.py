from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import wave


def test_benchmark_stt_can_generate_fixture(tmp_path):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "benchmark-stt.py"
    spec = importlib.util.spec_from_file_location("benchmark_stt", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["benchmark_stt"] = module
    spec.loader.exec_module(module)

    fixture = module.generate_fixture(tmp_path / "fixture.wav")

    assert fixture.exists()
    with wave.open(str(fixture), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 16000
