#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

from validate_board_profiles import discover_profiles, load_profile, validate_profile


SCHEMA_FILES = {
    "s3-8m-v1": "s3_8m_v1.csv",
    "s3-8m-recovery-v1": "s3_8m_recovery_v1.csv",
    "s3-16m-v1": "s3_16m_v1.csv",
    "s3-16m-recovery-v1": "s3_16m_recovery_v1.csv",
    "p4-32m-v1": "p4_32m_v1.csv",
}
APP_ALIGNMENT = 0x10000
DATA_ALIGNMENT = 0x1000
WARN_FRACTION = 0.75
REJECT_FRACTION = 0.85


class PartitionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Partition:
    name: str
    kind: str
    subtype: str
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


def parse_size(value: object) -> int:
    text = str(value or "").strip()
    if not text:
        raise PartitionValidationError("missing size value")
    if text.lower().startswith("0x"):
        return int(text, 16)
    lowered = text.lower()
    multipliers = {
        "kib": 1024,
        "kb": 1024,
        "k": 1024,
        "mib": 1024 * 1024,
        "mb": 1024 * 1024,
        "m": 1024 * 1024,
    }
    for suffix, multiplier in multipliers.items():
        if lowered.endswith(suffix):
            return int(float(text[: -len(suffix)]) * multiplier)
    return int(text)


def align(value: int, boundary: int) -> int:
    return ((value + boundary - 1) // boundary) * boundary


def schema_csv_path(partition_root: Path, schema: str) -> Path:
    filename = SCHEMA_FILES.get(schema)
    if filename is None:
        raise PartitionValidationError(f"unsupported partition schema {schema!r}")
    return partition_root / filename


def load_partitions(path: Path) -> list[Partition]:
    if not path.exists():
        raise PartitionValidationError(f"missing partition CSV: {path}")
    partitions: list[Partition] = []
    current_offset = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.reader(line for line in handle if line.strip() and not line.lstrip().startswith("#")):
            if len(row) < 5:
                raise PartitionValidationError(f"{path}: malformed partition row {row!r}")
            name, kind, subtype, raw_offset, raw_size = [cell.strip() for cell in row[:5]]
            size = parse_size(raw_size)
            if raw_offset:
                offset = parse_size(raw_offset)
            else:
                offset = align(current_offset, APP_ALIGNMENT if kind == "app" else DATA_ALIGNMENT)
            partitions.append(Partition(name=name, kind=kind, subtype=subtype, offset=offset, size=size))
            current_offset = offset + size
    if not partitions:
        raise PartitionValidationError(f"{path}: no partitions found")
    return partitions


def validate_partition_layout(path: Path, flash_size: int) -> list[Partition]:
    partitions = load_partitions(path)
    previous: Partition | None = None
    names: set[str] = set()
    for partition in partitions:
        if partition.name in names:
            raise PartitionValidationError(f"{path}: duplicate partition name {partition.name}")
        names.add(partition.name)
        alignment = APP_ALIGNMENT if partition.kind == "app" else DATA_ALIGNMENT
        if partition.offset % alignment != 0:
            raise PartitionValidationError(
                f"{path}: partition {partition.name} offset 0x{partition.offset:x} is not {alignment:#x}-aligned"
            )
        if previous is not None and partition.offset < previous.end:
            raise PartitionValidationError(
                f"{path}: partition {partition.name} overlaps {previous.name}"
            )
        if partition.end > flash_size:
            raise PartitionValidationError(
                f"{path}: partition {partition.name} ends at 0x{partition.end:x}, beyond flash size 0x{flash_size:x}"
            )
        previous = partition
    if not any(partition.kind == "data" and partition.subtype == "ota" for partition in partitions):
        raise PartitionValidationError(f"{path}: missing otadata partition")
    return partitions


def validate_profile_partitions(
    profile: dict[str, Any],
    profile_path: Path,
    *,
    partition_root: Path,
    app_binary: Path | None = None,
) -> list[str]:
    validate_profile(profile, profile_path)
    build = profile["build"]
    hardware = profile["hardware"]
    if not isinstance(build, dict) or not isinstance(hardware, dict):
        raise PartitionValidationError(f"{profile_path}: invalid validated profile shape")

    schema = str(build["partition_schema"])
    slot_size = parse_size(build["app_slot_size"])
    flash_size = parse_size(hardware["flash_size"])
    partitions = validate_partition_layout(schema_csv_path(partition_root, schema), flash_size)
    ota_slots = [partition for partition in partitions if partition.kind == "app" and partition.subtype.startswith("ota_")]
    if len(ota_slots) < 2:
        raise PartitionValidationError(f"{schema}: expected at least two OTA app slots")
    for slot in ota_slots:
        if slot.size != slot_size:
            raise PartitionValidationError(
                f"{schema}: {slot.name} size {slot.size} does not match profile app slot size {slot_size}"
            )

    warnings: list[str] = []
    if app_binary is not None:
        if not app_binary.exists():
            raise PartitionValidationError(f"missing app binary: {app_binary}")
        app_size = app_binary.stat().st_size
        warn_at = int(slot_size * WARN_FRACTION)
        reject_at = int(slot_size * REJECT_FRACTION)
        if app_size >= reject_at:
            raise PartitionValidationError(
                f"{profile['board_profile']}: app binary {app_size} bytes exceeds 85 percent slot gate {reject_at}"
            )
        if app_size >= warn_at:
            warnings.append(
                f"{profile['board_profile']}: app binary {app_size} bytes exceeds 75 percent slot warning {warn_at}"
            )
    return warnings


def profiles_to_validate(root: Path, board_profile: str | None) -> list[Path]:
    if board_profile:
        return [root / board_profile / "board.yaml"]
    return discover_profiles(root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Hexe firmware partition schemas and app size guardrails.")
    parser.add_argument("--profile-root", type=Path, default=Path("firmware/boards"))
    parser.add_argument("--partition-root", type=Path, default=Path("firmware/partitions"))
    parser.add_argument("--board-profile")
    parser.add_argument("--app-binary", type=Path)
    args = parser.parse_args()

    try:
        profile_paths = profiles_to_validate(args.profile_root, args.board_profile)
        for profile_path in profile_paths:
            profile = load_profile(profile_path)
            warnings = validate_profile_partitions(
                profile,
                profile_path,
                partition_root=args.partition_root,
                app_binary=args.app_binary,
            )
            for warning in warnings:
                print(f"WARNING: {warning}", file=sys.stderr)
    except PartitionValidationError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Validated partition schema for {len(profile_paths)} board profile(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
