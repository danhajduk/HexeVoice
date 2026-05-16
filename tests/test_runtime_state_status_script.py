from __future__ import annotations

from pathlib import Path
import subprocess


def test_runtime_state_status_splits_source_from_runtime() -> None:
    sample_status = "\n".join(
        [
            " M runtime/voice_session_history.json",
            "?? runtime/logs/backend.log",
            " M .venv/lib/python3.11/site-packages/example.pyc",
            'D  ".venv/lib/python3.11/site-packages/script (dev).tmpl"',
            " M src/hexevoice/main.py",
            "?? docs/new-note.md",
            "?? scripts/stack.env",
        ]
    )

    result = subprocess.run(
        ["bash", "scripts/runtime-state-status.sh", "--from-stdin"],
        cwd=Path(__file__).resolve().parents[1],
        input=sample_status,
        text=True,
        capture_output=True,
        check=True,
    )

    source_section, runtime_section = result.stdout.split("Runtime/local mutable state:")
    assert "src/hexevoice/main.py" in source_section
    assert "docs/new-note.md" in source_section
    assert "runtime/voice_session_history.json" not in source_section
    assert "runtime/voice_session_history.json" in runtime_section
    assert ".venv/lib/python3.11/site-packages/example.pyc" in runtime_section
    assert "script (dev).tmpl" in runtime_section
    assert "scripts/stack.env" in runtime_section
