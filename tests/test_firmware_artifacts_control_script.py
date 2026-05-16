from __future__ import annotations

from pathlib import Path
import hashlib
import subprocess
import sys


def _write_firmware_fixture(source: Path) -> None:
    source.mkdir(parents=True)
    artifacts = {
        "hexe_firmware.bin": b"default-firmware",
        "hexe_firmware_esp_box_3.bin": b"esp-box-firmware",
        "hexe_firmware_ha_voice_pe.bin": b"voice-pe-firmware",
        "manifest.json": b'{"filename":"hexe_firmware.bin","version":"0.2.0"}',
        "manifest-esp_box_3.json": b'{"filename":"hexe_firmware_esp_box_3.bin","version":"0.2.0"}',
        "manifest-ha_voice_pe.json": b'{"filename":"hexe_firmware_ha_voice_pe.bin","version":"0.2.0"}',
    }
    for name, content in artifacts.items():
        (source / name).write_bytes(content)
    checksum_lines = []
    for name in (
        "hexe_firmware.bin",
        "hexe_firmware_esp_box_3.bin",
        "hexe_firmware_ha_voice_pe.bin",
    ):
        checksum_lines.append(f"{hashlib.sha256((source / name).read_bytes()).hexdigest()}  {name}")
    (source / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def test_firmware_artifacts_control_downloads_from_local_source_dir(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "runtime" / "firmware"
    _write_firmware_fixture(source)

    result = subprocess.run(
        ["bash", "scripts/firmware-artifacts-control.sh", "download"],
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": sys.executable,
            "HEXEVOICE_FIRMWARE_SOURCE_DIR": str(source),
            "HEXEVOICE_FIRMWARE_ARTIFACT_DIR": str(target),
        },
        text=True,
        capture_output=True,
        check=True,
    )

    assert "installed hexe_firmware.bin" in result.stdout
    assert "checksums: ok (3)" in result.stdout
    assert (target / "hexe_firmware_esp_box_3.bin").read_bytes() == b"esp-box-firmware"
    assert (target / "manifest-ha_voice_pe.json").exists()


def test_firmware_artifacts_control_downloads_from_base_url(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "runtime" / "firmware"
    _write_firmware_fixture(source)

    result = subprocess.run(
        ["bash", "scripts/firmware-artifacts-control.sh", "download"],
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": sys.executable,
            "HEXEVOICE_FIRMWARE_ARTIFACT_BASE_URL": source.as_uri(),
            "HEXEVOICE_FIRMWARE_ARTIFACT_DIR": str(target),
        },
        text=True,
        capture_output=True,
        check=True,
    )

    assert "installed manifest-esp_box_3.json" in result.stdout
    assert (target / "hexe_firmware_ha_voice_pe.bin").read_bytes() == b"voice-pe-firmware"


def test_firmware_artifacts_control_reports_checksum_mismatch(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "runtime" / "firmware"
    _write_firmware_fixture(source)
    (source / "SHA256SUMS").write_text(
        "0000000000000000000000000000000000000000000000000000000000000000  hexe_firmware.bin\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "scripts/firmware-artifacts-control.sh", "download"],
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": sys.executable,
            "HEXEVOICE_FIRMWARE_SOURCE_DIR": str(source),
            "HEXEVOICE_FIRMWARE_ARTIFACT_DIR": str(target),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "checksum_mismatch:hexe_firmware.bin" in result.stderr


def test_firmware_artifacts_control_reports_missing_source_configuration(tmp_path):
    result = subprocess.run(
        ["bash", "scripts/firmware-artifacts-control.sh", "download"],
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": sys.executable,
            "HEXEVOICE_FIRMWARE_ARTIFACT_DIR": str(tmp_path / "runtime" / "firmware"),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "firmware_source_not_configured" in result.stderr
