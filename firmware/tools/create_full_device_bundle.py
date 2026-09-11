#!/usr/bin/env python3
from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

from validate_board_profiles import load_profile, validate_profile
from validate_partition_schema import (
    PartitionValidationError,
    parse_size,
    schema_csv_path,
    validate_partition_layout,
)


SCHEMA_VERSION = "hexe-full-device-flash-bundle-v1"


class BundleError(ValueError):
    pass


def require_file(path: Path) -> None:
    if not path.is_file():
        raise BundleError(f"missing required file: {path}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_flash_args(path: Path) -> dict[str, Any]:
    require_file(path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    try:
        return {
            "chip": payload["extra_esptool_args"]["chip"],
            "flash_mode": payload["flash_settings"]["flash_mode"],
            "flash_size": payload["flash_settings"]["flash_size"],
            "flash_freq": payload["flash_settings"]["flash_freq"],
            "bootloader_offset": payload["bootloader"]["offset"],
            "partition_table_offset": payload["partition-table"]["offset"],
            "ota_data_offset": payload["otadata"]["offset"],
        }
    except KeyError as exc:
        raise BundleError(f"{path}: missing ESP-IDF flash field {exc}") from exc


def format_offset(value: int) -> str:
    return f"0x{value:x}"


def copy_file(source: Path, destination: Path) -> None:
    require_file(source)
    shutil.copy2(source, destination)


def write_sha256sums(output_dir: Path) -> None:
    lines = []
    for path in sorted(output_dir.glob("*.bin")):
        lines.append(f"{sha256(path)}  {path.name}\n")
    (output_dir / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def validate_same_partition_inputs(recovery_build_dir: Path, endpoint_build_dir: Path) -> None:
    recovery_file = recovery_build_dir / "partition_table/partition-table.bin"
    endpoint_file = endpoint_build_dir / "partition_table/partition-table.bin"
    require_file(recovery_file)
    require_file(endpoint_file)
    if not filecmp.cmp(recovery_file, endpoint_file, shallow=False):
        raise BundleError("recovery and endpoint partition table artifacts differ")


def write_provisioning_example(path: Path, board_profile: str) -> None:
    path.write_text(
        "\n".join(
            [
                "# Copy this file to provisioning.env before flashing, then edit the values.",
                "# provisioning.env may contain Wi-Fi credentials. Do not publish it.",
                "",
                f"ENDPOINT_ID={board_profile}",
                f"DISPLAY_NAME={board_profile}",
                "BACKEND_HOST=10.0.0.100",
                "HTTP_PORT=9004",
                "WS_PORT=9004",
                "USE_TLS=false",
                "WIFI_SSID=",
                "WIFI_PASSWORD=",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_flash_script(path: Path, manifest_filename: str) -> None:
    path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
cd "${{SCRIPT_DIR}}"

PORT="${{1:-/dev/ttyACM0}}"
BAUD="${{BAUD:-460800}}"
PROVISIONING_ENV="${{PROVISIONING_ENV:-provisioning.env}}"
PROVISIONING_WORKDIR=""

if [[ -z "${{IDF_PATH:-}}" ]]; then
  echo "IDF_PATH is not set. Run '. ~/esp-idf/export.sh' first." >&2
  exit 1
fi

cleanup() {{
  if [[ -n "${{PROVISIONING_WORKDIR}}" && -d "${{PROVISIONING_WORKDIR}}" ]]; then
    rm -rf "${{PROVISIONING_WORKDIR}}"
  fi
}}
trap cleanup EXIT

mapfile -t FLASH_METADATA < <(python - {manifest_filename} <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    manifest = json.load(handle)

flash = manifest["flash"]
artifacts = manifest["artifacts"]
print(flash["chip"])
print(flash["mode"])
print(flash["size"])
print(flash["freq"])
print(flash["offsets"]["bootloader"])
print(artifacts["bootloader"])
print(flash["offsets"]["partition_table"])
print(artifacts["partition_table"])
print(flash["offsets"]["nvs"])
print(flash["offsets"]["nvs_size"])
print(flash["offsets"]["ota_data"])
print(artifacts["ota_data"])
print(flash["offsets"]["factory"])
print(artifacts["recovery"])
print(flash["offsets"]["ota_0"])
print(artifacts["endpoint"])
PY
)
if [[ "${{#FLASH_METADATA[@]}}" -ne 16 ]]; then
  echo "Invalid full-device flash metadata: ${{SCRIPT_DIR}}/{manifest_filename}" >&2
  exit 1
fi

FLASH_CHIP="${{FLASH_METADATA[0]}}"
FLASH_MODE="${{FLASH_METADATA[1]}}"
FLASH_SIZE="${{FLASH_METADATA[2]}}"
FLASH_FREQ="${{FLASH_METADATA[3]}}"
BOOTLOADER_OFFSET="${{FLASH_METADATA[4]}}"
BOOTLOADER_IMAGE="${{FLASH_METADATA[5]}}"
PARTITION_TABLE_OFFSET="${{FLASH_METADATA[6]}}"
PARTITION_TABLE_IMAGE="${{FLASH_METADATA[7]}}"
NVS_OFFSET="${{FLASH_METADATA[8]}}"
NVS_SIZE="${{FLASH_METADATA[9]}}"
OTA_DATA_OFFSET="${{FLASH_METADATA[10]}}"
OTA_DATA_IMAGE="${{FLASH_METADATA[11]}}"
FACTORY_RECOVERY_OFFSET="${{FLASH_METADATA[12]}}"
RECOVERY_IMAGE="${{FLASH_METADATA[13]}}"
OTA0_ENDPOINT_OFFSET="${{FLASH_METADATA[14]}}"
ENDPOINT_IMAGE="${{FLASH_METADATA[15]}}"

for artifact in "${{BOOTLOADER_IMAGE}}" "${{PARTITION_TABLE_IMAGE}}" "${{OTA_DATA_IMAGE}}" "${{RECOVERY_IMAGE}}" "${{ENDPOINT_IMAGE}}"; do
  if [[ ! -f "${{artifact}}" ]]; then
    echo "Missing required flash artifact: ${{SCRIPT_DIR}}/${{artifact}}" >&2
    exit 1
  fi
done

FLASH_ARGS=(
  "${{BOOTLOADER_OFFSET}}" "${{BOOTLOADER_IMAGE}}"
  "${{PARTITION_TABLE_OFFSET}}" "${{PARTITION_TABLE_IMAGE}}"
)

if [[ -f "${{PROVISIONING_ENV}}" ]]; then
  echo "Building provisioning NVS image from ${{PROVISIONING_ENV}}"
  PROVISIONING_WORKDIR="$(mktemp -d)"
  PROVISIONING_CSV="${{PROVISIONING_WORKDIR}}/provisioning.csv"
  PROVISIONING_BIN="${{PROVISIONING_WORKDIR}}/provisioning.bin"

  python provisioning-env-to-nvs-csv.py "${{PROVISIONING_ENV}}" "${{PROVISIONING_CSV}}"

  python "${{IDF_PATH}}/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py" \\
    generate "${{PROVISIONING_CSV}}" "${{PROVISIONING_BIN}}" "${{NVS_SIZE}}"
  FLASH_ARGS+=("${{NVS_OFFSET}}" "${{PROVISIONING_BIN}}")
else
  echo "No ${{PROVISIONING_ENV}} found; flashing without provisioning NVS image."
fi

FLASH_ARGS+=(
  "${{OTA_DATA_OFFSET}}" "${{OTA_DATA_IMAGE}}"
  "${{FACTORY_RECOVERY_OFFSET}}" "${{RECOVERY_IMAGE}}"
  "${{OTA0_ENDPOINT_OFFSET}}" "${{ENDPOINT_IMAGE}}"
)

python "${{IDF_PATH}}/components/esptool_py/esptool/esptool.py" \\
  --chip "${{FLASH_CHIP}}" \\
  -p "${{PORT}}" \\
  -b "${{BAUD}}" \\
  write_flash -z \\
  --flash-mode "${{FLASH_MODE}}" \\
  --flash-freq "${{FLASH_FREQ}}" \\
  --flash-size "${{FLASH_SIZE}}" \\
  "${{FLASH_ARGS[@]}}"
""",
        encoding="utf-8",
    )
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_readme(path: Path, board_profile: str, partition_schema: str) -> None:
    path.write_text(
        f"""# Full Device Firmware Bundle

This manufacturing/service bundle flashes recovery firmware to the factory
partition and full endpoint firmware to `ota_0`.

## Included Files

- `bootloader.bin`
- `partition-table.bin`
- `ota_data_initial.bin`
- `hexe_min_{board_profile}.bin`
- `hexe_firmware_{board_profile}.bin`
- `full-device-manifest.json`
- `flash-full-device.sh`
- `provisioning.env.example`
- `provisioning-env-to-nvs-csv.py`
- `SHA256SUMS`

## Flash

```bash
. ~/esp-idf/export.sh
cd firmware/export-full-{board_profile}
./flash-full-device.sh /dev/ttyACM0
```

If `provisioning.env` is present, the script converts it to an NVS image and
writes it to the `nvs` partition. Keep that file local because it can contain
Wi-Fi credentials.

## Layout

- board profile: `{board_profile}`
- partition schema: `{partition_schema}`
- factory app: `hexe_min_{board_profile}.bin`
- ota_0 app: `hexe_firmware_{board_profile}.bin`
""",
        encoding="utf-8",
    )


def build_manifest(
    *,
    board_profile: str,
    profile: dict[str, Any],
    flash_args: dict[str, Any],
    output_dir: Path,
    recovery_image: str,
    endpoint_image: str,
    bootloader_image: str,
    partition_table_image: str,
    ota_data_image: str,
    factory_offset: int,
    factory_size: int,
    ota0_offset: int,
    ota0_size: int,
    nvs_offset: int,
    nvs_size: int,
) -> dict[str, Any]:
    recovery_path = output_dir / recovery_image
    endpoint_path = output_dir / endpoint_image
    return {
        "schema_version": SCHEMA_VERSION,
        "manufacturing_service_only": True,
        "board_profile": board_profile,
        "partition_schema": profile["build"]["partition_schema"],
        "flash_size": profile["hardware"]["flash_size"],
        "idf_target": profile["build"]["idf_target"],
        "recovery_update_policy": "factory_partition_is_written_only_by_full_device_bundle",
        "endpoint_update_policy": "normal_endpoint_ota_targets_ota_partitions_only",
        "handoff": {
            "initial_boot": "factory_recovery_with_initial_ota_data",
            "endpoint_partition": "ota_0",
            "provisioning_storage": "default_nvs_plaintext_until_task_306_config_migration",
        },
        "flash": {
            "chip": flash_args["chip"],
            "mode": flash_args["flash_mode"],
            "size": flash_args["flash_size"],
            "freq": flash_args["flash_freq"],
            "offsets": {
                "bootloader": flash_args["bootloader_offset"],
                "partition_table": flash_args["partition_table_offset"],
                "nvs": format_offset(nvs_offset),
                "nvs_size": format_offset(nvs_size),
                "ota_data": flash_args["ota_data_offset"],
                "factory": format_offset(factory_offset),
                "ota_0": format_offset(ota0_offset),
            },
        },
        "artifacts": {
            "bootloader": bootloader_image,
            "partition_table": partition_table_image,
            "ota_data": ota_data_image,
            "recovery": recovery_image,
            "endpoint": endpoint_image,
        },
        "images": {
            "recovery": {
                "application_type": "recovery",
                "partition": "factory",
                "offset": format_offset(factory_offset),
                "partition_size_bytes": factory_size,
                "filename": recovery_image,
                "size_bytes": recovery_path.stat().st_size,
                "sha256": sha256(recovery_path),
            },
            "endpoint": {
                "application_type": "endpoint",
                "partition": "ota_0",
                "offset": format_offset(ota0_offset),
                "partition_size_bytes": ota0_size,
                "filename": endpoint_image,
                "size_bytes": endpoint_path.stat().st_size,
                "sha256": sha256(endpoint_path),
            },
        },
    }


def create_bundle(args: argparse.Namespace) -> None:
    profile_path = args.profile_root / args.board_profile / "board.yaml"
    profile = load_profile(profile_path)
    validate_profile(profile, profile_path)

    if profile["build"]["recovery_app"] is not True:
        raise BundleError(f"{args.board_profile}: board profile does not declare recovery app support")
    if profile["build"]["idf_target"] != "esp32s3":
        raise BundleError(
            f"{args.board_profile}: full-device bundle is not buildable until recovery app supports "
            f"{profile['build']['idf_target']}"
        )
    if profile["adapters"]["buildable"] is not True:
        raise BundleError(f"{args.board_profile}: endpoint adapters are not buildable")

    recovery_build_dir = args.recovery_build_dir
    endpoint_build_dir = args.endpoint_build_dir
    validate_same_partition_inputs(recovery_build_dir, endpoint_build_dir)

    flash_args = load_flash_args(recovery_build_dir / "flasher_args.json")
    endpoint_flash_args = load_flash_args(endpoint_build_dir / "flasher_args.json")
    metadata_keys = (
        "chip",
        "flash_mode",
        "flash_size",
        "flash_freq",
        "bootloader_offset",
        "partition_table_offset",
        "ota_data_offset",
    )
    for key in metadata_keys:
        if flash_args[key] != endpoint_flash_args[key]:
            raise BundleError(f"recovery and endpoint flash metadata differ for {key}")

    partitions = validate_partition_layout(
        schema_csv_path(args.partition_root, profile["build"]["partition_schema"]),
        parse_size(profile["hardware"]["flash_size"]),
    )
    by_name = {partition.name: partition for partition in partitions}
    for required in ("nvs", "factory", "ota_0"):
        if required not in by_name:
            raise BundleError(f"{profile['build']['partition_schema']}: missing required partition {required}")

    recovery_app = recovery_build_dir / "hexe_firmware.bin"
    endpoint_app = endpoint_build_dir / "hexe_firmware.bin"
    require_file(recovery_app)
    require_file(endpoint_app)
    if recovery_app.stat().st_size > by_name["factory"].size:
        raise BundleError(f"{args.board_profile}: recovery image does not fit factory partition")
    if endpoint_app.stat().st_size > by_name["ota_0"].size:
        raise BundleError(f"{args.board_profile}: endpoint image does not fit ota_0 partition")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    bootloader_image = "bootloader.bin"
    partition_table_image = "partition-table.bin"
    ota_data_image = "ota_data_initial.bin"
    recovery_image = f"hexe_min_{args.board_profile}.bin"
    endpoint_image = f"hexe_firmware_{args.board_profile}.bin"

    copy_file(recovery_build_dir / "bootloader/bootloader.bin", output_dir / bootloader_image)
    copy_file(recovery_build_dir / "partition_table/partition-table.bin", output_dir / partition_table_image)
    copy_file(recovery_build_dir / "ota_data_initial.bin", output_dir / ota_data_image)
    copy_file(recovery_app, output_dir / recovery_image)
    copy_file(endpoint_app, output_dir / endpoint_image)
    copy_file(args.provisioning_csv_tool, output_dir / "provisioning-env-to-nvs-csv.py")

    manifest = build_manifest(
        board_profile=args.board_profile,
        profile=profile,
        flash_args=flash_args,
        output_dir=output_dir,
        recovery_image=recovery_image,
        endpoint_image=endpoint_image,
        bootloader_image=bootloader_image,
        partition_table_image=partition_table_image,
        ota_data_image=ota_data_image,
        factory_offset=by_name["factory"].offset,
        factory_size=by_name["factory"].size,
        ota0_offset=by_name["ota_0"].offset,
        ota0_size=by_name["ota_0"].size,
        nvs_offset=by_name["nvs"].offset,
        nvs_size=by_name["nvs"].size,
    )
    manifest_path = output_dir / "full-device-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "flash_args_full_device.json").write_text(
        json.dumps(manifest["flash"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_flash_script(output_dir / "flash-full-device.sh", manifest_path.name)
    write_provisioning_example(output_dir / "provisioning.env.example", args.board_profile)
    write_readme(output_dir / "README.md", args.board_profile, profile["build"]["partition_schema"])
    write_sha256sums(output_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a full-device recovery plus endpoint flash bundle.")
    parser.add_argument("--board-profile", required=True)
    parser.add_argument("--profile-root", type=Path, default=Path("firmware/boards"))
    parser.add_argument("--partition-root", type=Path, default=Path("firmware/partitions"))
    parser.add_argument("--recovery-build-dir", type=Path, required=True)
    parser.add_argument("--endpoint-build-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--provisioning-csv-tool",
        type=Path,
        default=Path("firmware/tools/provisioning-env-to-nvs-csv.py"),
    )
    args = parser.parse_args()

    try:
        create_bundle(args)
    except (BundleError, PartitionValidationError, FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Created full-device flash bundle at {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
