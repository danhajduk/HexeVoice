#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from validate_board_profiles import ValidationError, load_profile, validate_profile


def cmake_quote(value: object) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def cmake_bool(value: object) -> str:
    return "TRUE" if bool(value) else "FALSE"


def render_cmake(profile: dict[str, object], profile_path: Path) -> str:
    board_profile = str(profile["board_profile"])
    display_name = str(profile["display_name"])
    support_status = str(profile["support_status"])
    build = profile["build"]
    hardware = profile["hardware"]
    adapters = profile["adapters"]
    if not isinstance(build, dict) or not isinstance(hardware, dict) or not isinstance(adapters, dict):
        raise ValidationError("validated profile lost required object sections")

    definitions = build.get("compile_definitions")
    source_files = adapters.get("source_files")
    if not isinstance(definitions, list) or not all(isinstance(item, str) for item in definitions):
        raise ValidationError(f"{board_profile}: build.compile_definitions must be a string list")
    if not isinstance(source_files, list) or not all(isinstance(item, str) for item in source_files):
        raise ValidationError(f"{board_profile}: adapters.source_files must be a string list")

    lines = [
        "# Generated from firmware/boards/*/board.yaml.",
        "# Do not edit by hand.",
        f"set(HEXE_BOARD_PROFILE {cmake_quote(board_profile)})",
        f"set(HEXE_BOARD_PROFILE_PATH {cmake_quote(profile_path)})",
        f"set(HEXE_BOARD_DISPLAY_NAME {cmake_quote(display_name)})",
        f"set(HEXE_BOARD_SUPPORT_STATUS {cmake_quote(support_status)})",
        f"set(HEXE_BOARD_IDF_TARGET {cmake_quote(build.get('idf_target'))})",
        f"set(HEXE_BOARD_PARTITION_SCHEMA {cmake_quote(build.get('partition_schema'))})",
        f"set(HEXE_BOARD_APP_SLOT_SIZE {cmake_quote(build.get('app_slot_size'))})",
        f"set(HEXE_BOARD_FLASH_SIZE {cmake_quote(hardware.get('flash_size'))})",
        f"set(HEXE_BOARD_PSRAM_SIZE {cmake_quote(hardware.get('psram_size'))})",
        f"set(HEXE_BOARD_RECOVERY_APP {cmake_bool(build.get('recovery_app'))})",
        f"set(HEXE_BOARD_ADAPTER_BUILDABLE {cmake_bool(adapters.get('buildable'))})",
        "set(HEXE_BOARD_DEFINITIONS",
    ]
    for definition in definitions:
        lines.append(f"  {definition}")
    lines.extend(
        [
            ")",
            "set(HEXE_BOARD_SRCS",
        ]
    )
    for source in source_files:
        lines.append(f"  {cmake_quote(source)}")
    lines.extend(
        [
            ")",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a CMake board adapter fragment from a Hexe board profile.")
    parser.add_argument("--profile-root", type=Path, default=Path("firmware/boards"))
    parser.add_argument("--board-profile", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    profile_path = args.profile_root / args.board_profile / "board.yaml"
    if not profile_path.exists():
        print(f"Unsupported HEXE_BOARD_PROFILE: {args.board_profile}", file=sys.stderr)
        print(f"Missing board profile file: {profile_path}", file=sys.stderr)
        return 1

    try:
        profile = load_profile(profile_path)
        validate_profile(profile, profile_path)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_cmake(profile, profile_path), encoding="utf-8")
    except (OSError, ValidationError) as exc:
        print(f"Failed to generate board profile config: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
