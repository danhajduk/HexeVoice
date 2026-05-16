from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def expected_runtime_dirs() -> set[str]:
    payload = json.loads((ROOT / "config" / "runtime-dirs.json").read_text(encoding="utf-8"))
    return set(payload["runtime_dirs"])


def test_prepare_runtime_dirs_creates_expected_tree(tmp_path):
    runtime_dir = tmp_path / "runtime"

    result = subprocess.run(
        ["bash", "scripts/prepare-runtime-dirs.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env={"HEXEVOICE_RUNTIME_DIR": str(runtime_dir), "PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=True,
    )

    assert f"Prepared HexeVoice runtime directories under {runtime_dir}" in result.stdout
    missing = [path for path in expected_runtime_dirs() if not (runtime_dir / path).is_dir()]
    assert missing == []


def test_prepare_runtime_dirs_is_idempotent_and_preserves_existing_files(tmp_path):
    runtime_dir = tmp_path / "runtime"
    existing = runtime_dir / "voice_tts" / "keep.wav"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")

    for _ in range(2):
        subprocess.run(
            ["bash", "scripts/prepare-runtime-dirs.sh"],
            cwd=Path(__file__).resolve().parents[1],
            env={"HEXEVOICE_RUNTIME_DIR": str(runtime_dir), "PATH": "/usr/bin:/bin"},
            check=True,
        )

    assert existing.read_bytes() == b"existing"
