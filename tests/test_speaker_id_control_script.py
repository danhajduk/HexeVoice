from __future__ import annotations

from pathlib import Path
import subprocess


def _write_fake_python(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$PYTHON_CALLS"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_speaker_id_control_install_uses_cpu_torch_wheels_by_default(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    calls_path = tmp_path / "python-calls.txt"
    fake_python = tmp_path / "python"
    _write_fake_python(fake_python)

    result = subprocess.run(
        ["bash", "scripts/speaker-id-control.sh", "install"],
        cwd=repo_root,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": str(fake_python),
            "PYTHON_CALLS": str(calls_path),
            "SPEAKER_ID_ENV_FILE": str(tmp_path / "missing.env"),
            "HEXEVOICE_SOCKET_DIR": str(tmp_path / "sockets"),
        },
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Installing Speaker ID PyTorch dependencies from https://download.pytorch.org/whl/cpu" in result.stdout
    assert "Installing Speaker ID provider package: speechbrain" in result.stdout
    assert calls_path.read_text(encoding="utf-8").splitlines() == [
        "-m pip install --upgrade --index-url https://download.pytorch.org/whl/cpu torch torchaudio",
        "-m pip install --upgrade speechbrain",
    ]


def test_speaker_id_control_install_allows_custom_package_override(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    calls_path = tmp_path / "python-calls.txt"
    fake_python = tmp_path / "python"
    _write_fake_python(fake_python)

    result = subprocess.run(
        ["bash", "scripts/speaker-id-control.sh", "install"],
        cwd=repo_root,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": str(fake_python),
            "PYTHON_CALLS": str(calls_path),
            "SPEAKER_ID_ENV_FILE": str(tmp_path / "missing.env"),
            "HEXEVOICE_SOCKET_DIR": str(tmp_path / "sockets"),
            "SPEAKER_ID_INSTALL_PACKAGES": "speechbrain torchaudio",
        },
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Installing Speaker ID provider dependencies: speechbrain torchaudio" in result.stdout
    assert calls_path.read_text(encoding="utf-8").strip() == "-m pip install speechbrain torchaudio"
