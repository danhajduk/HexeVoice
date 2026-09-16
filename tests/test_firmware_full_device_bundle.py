from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_TOOL = REPO_ROOT / "firmware/tools/create_full_device_bundle.py"
BUILD_SCRIPT = REPO_ROOT / "firmware/build.sh"
PROFILE_ROOT = REPO_ROOT / "firmware/boards"
PARTITION_ROOT = REPO_ROOT / "firmware/partitions"
PROVISIONING_TOOL = REPO_ROOT / "firmware/tools/provisioning-env-to-nvs-csv.py"


def write_fake_build(build_dir: Path, *, app_payload: bytes, chip: str = "esp32s3") -> None:
    (build_dir / "bootloader").mkdir(parents=True)
    (build_dir / "partition_table").mkdir(parents=True)
    (build_dir / "bootloader/bootloader.bin").write_bytes(b"bootloader")
    (build_dir / "partition_table/partition-table.bin").write_bytes(b"partition-table")
    (build_dir / "ota_data_initial.bin").write_bytes(b"ota-data")
    (build_dir / "hexe_firmware.bin").write_bytes(app_payload)
    (build_dir / "flasher_args.json").write_text(
        json.dumps(
            {
                "extra_esptool_args": {"chip": chip},
                "flash_settings": {
                    "flash_mode": "dio",
                    "flash_size": "16MB",
                    "flash_freq": "80m",
                },
                "bootloader": {"offset": "0x0"},
                "partition-table": {"offset": "0x8000"},
                "otadata": {"offset": "0xd000"},
                "app": {"offset": "0x10000"},
            }
        ),
        encoding="utf-8",
    )


def run_bundle(tmp_path: Path, board_profile: str = "ha_voice_pe") -> tuple[Path, subprocess.CompletedProcess[str]]:
    recovery_build = tmp_path / "recovery"
    endpoint_build = tmp_path / "endpoint"
    output_dir = tmp_path / "bundle"
    write_fake_build(recovery_build, app_payload=b"recovery")
    write_fake_build(endpoint_build, app_payload=b"endpoint")

    result = subprocess.run(
        [
            sys.executable,
            str(BUNDLE_TOOL),
            "--board-profile",
            board_profile,
            "--profile-root",
            str(PROFILE_ROOT),
            "--partition-root",
            str(PARTITION_ROOT),
            "--recovery-build-dir",
            str(recovery_build),
            "--endpoint-build-dir",
            str(endpoint_build),
            "--output-dir",
            str(output_dir),
            "--provisioning-csv-tool",
            str(PROVISIONING_TOOL),
        ],
        text=True,
        capture_output=True,
    )
    return output_dir, result


def test_full_device_bundle_writes_recovery_to_factory_and_endpoint_to_ota0(tmp_path):
    output_dir, result = run_bundle(tmp_path)

    assert result.returncode == 0, result.stderr
    manifest = json.loads((output_dir / "full-device-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "hexe-full-device-flash-bundle-v1"
    assert manifest["manufacturing_service_only"] is True
    assert manifest["board_profile"] == "ha_voice_pe"
    assert manifest["partition_schema"] == "s3-16m-recovery-single-model-v1"
    assert manifest["images"]["recovery"]["application_type"] == "recovery"
    assert manifest["images"]["recovery"]["partition"] == "factory"
    assert manifest["images"]["recovery"]["offset"] == "0x10000"
    assert manifest["images"]["endpoint"]["application_type"] == "endpoint"
    assert manifest["images"]["endpoint"]["partition"] == "ota_0"
    assert manifest["images"]["endpoint"]["offset"] == "0x210000"
    assert manifest["recovery_update_policy"] == "factory_partition_is_written_only_by_full_device_bundle"
    assert manifest["endpoint_update_policy"] == "normal_endpoint_ota_targets_ota_partitions_only"

    assert (output_dir / "hexe_min_ha_voice_pe.bin").read_bytes() == b"recovery"
    assert (output_dir / "hexe_firmware_ha_voice_pe.bin").read_bytes() == b"endpoint"
    assert (output_dir / "provisioning-env-to-nvs-csv.py").is_file()
    assert (output_dir / "SHA256SUMS").is_file()

    flash_script = (output_dir / "flash-full-device.sh").read_text(encoding="utf-8")
    assert '"${FACTORY_RECOVERY_OFFSET}" "${RECOVERY_IMAGE}"' in flash_script
    assert '"${OTA0_ENDPOINT_OFFSET}" "${ENDPOINT_IMAGE}"' in flash_script
    assert '"${FACTORY_RECOVERY_OFFSET}" "${ENDPOINT_IMAGE}"' not in flash_script


def test_full_device_bundle_accepts_p4_recovery_app(tmp_path):
    output_dir, result = run_bundle(tmp_path, board_profile="waveshare_p4_wifi6_touch_lcd_7b")

    assert result.returncode == 0
    manifest = json.loads((output_dir / "full-device-manifest.json").read_text(encoding="utf-8"))
    assert manifest["board_profile"] == "waveshare_p4_wifi6_touch_lcd_7b"
    assert manifest["partition_schema"] == "p4-32m-v1"


def test_build_script_exposes_full_device_bundle_command():
    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "[build|push|bundle]" in build_script
    assert "build_full_device_bundle" in build_script
    assert "HEXE_FIRMWARE_APP=minimal" in build_script
    assert "HEXE_FIRMWARE_APP=endpoint" in build_script
    assert 'features.display)" != "true"' in build_script
    assert "--recovery-build-dir" in build_script
    assert "--endpoint-build-dir" in build_script
    assert "full-flash|full-device" in build_script
