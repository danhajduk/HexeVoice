#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import glob
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ASSET_ROOT = ROOT / "firmware" / "assets"
IMAGE_SUFFIXES = {".png"}
SPRITE_METADATA_SUFFIXES = {".json", ".yaml", ".yml"}
RGB888_BOARD_PROFILES = {"waveshare_p4_wifi6_touch_lcd_7b"}


def _parse_size(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", value.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT, for example 1024x600")
    return int(match.group(1)), int(match.group(2))


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def _image_size(path: Path) -> tuple[int, int]:
    dimensions = _png_dimensions(path)
    if dimensions is None:
        raise ValueError(f"could not read PNG dimensions from {path}")
    return dimensions


def _source_images(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in IMAGE_SUFFIXES
    )


def _source_sprite_metadata(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in SPRITE_METADATA_SUFFIXES
    )


def _write_sprite_metadata(source: Path, output: Path, dry_run: bool, yaml_python: str | None) -> None:
    if source.suffix.lower() == ".json":
        print(f"+ copy {source} {output}")
        if not dry_run:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
        return

    output = output.with_suffix(".json")
    print(f"+ yaml-to-json {source} {output}")
    if dry_run:
        return
    if yaml_python is None:
        raise SystemExit("PyYAML is required to convert sprite layout YAML files")
    subprocess.run(
        [yaml_python, str(Path(__file__).with_name("yaml_to_json.py")), str(source), str(output)],
        check=True,
    )


def _write_p4_ui_config(config_dir: Path, output_dir: Path, dry_run: bool, yaml_python: str | None) -> None:
    command = [
        yaml_python or "python3",
        str(Path(__file__).with_name("compile_p4_ui_config.py")),
        str(config_dir),
        str(output_dir),
    ]
    print("+ " + " ".join(command), flush=True)
    if dry_run:
        return
    if yaml_python is None:
        raise SystemExit("PyYAML is required to compile the P4 UI configuration")
    subprocess.run(command, check=True)


def _next_library_version(library_path: Path, now: datetime) -> str:
    date_prefix = f"{now:%Y.%m.%d}"
    fallback = f"{date_prefix}.1"
    if not library_path.exists():
        return fallback
    try:
        current = json.loads(library_path.read_text(encoding="utf-8")).get("asset_library_version")
    except (OSError, json.JSONDecodeError):
        return fallback
    if not isinstance(current, str):
        return fallback
    match = re.fullmatch(rf"{re.escape(date_prefix)}\.(\d+)", current)
    if not match:
        return fallback
    return f"{date_prefix}.{int(match.group(1)) + 1}"


def _find_converter_python() -> str:
    candidates: list[str] = []
    if python := os.environ.get("PYTHON"):
        candidates.append(python)
    if idf_python_env := os.environ.get("IDF_PYTHON_ENV_PATH"):
        candidates.append(str(Path(idf_python_env) / "bin" / "python"))
    candidates.extend(sorted(glob.glob(str(Path.home() / ".espressif" / "python_env" / "idf*_py*_env" / "bin" / "python"))))
    candidates.append(sys.executable or "python3")
    candidates.append("python3")

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            result = subprocess.run(
                [candidate, "-c", "import PIL"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            continue
        if result.returncode == 0:
            return candidate
    raise SystemExit(
        "Pillow is required by convert_image.py. Set PYTHON to an environment "
        "with Pillow installed, for example PYTHON=$IDF_PYTHON_ENV_PATH/bin/python."
    )


def _find_yaml_python() -> str | None:
    candidates = [
        os.environ.get("PYTHON", ""),
        str(Path(os.environ["IDF_PYTHON_ENV_PATH"]) / "bin" / "python")
        if os.environ.get("IDF_PYTHON_ENV_PATH") else "",
        *sorted(glob.glob(str(Path.home() / ".espressif" / "python_env" / "idf*_py*_env" / "bin" / "python"))),
        sys.executable or "python3",
        "python3",
    ]
    for candidate in dict.fromkeys(item for item in candidates if item):
        try:
            result = subprocess.run(
                [candidate, "-c", "import yaml"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            continue
        if result.returncode == 0:
            return candidate
    return None


def _run(command: list[str], dry_run: bool) -> None:
    print("+ " + " ".join(command), flush=True)
    if dry_run:
        return
    subprocess.run(command, check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert board source PNG files to raw RGB endpoint assets "
            "and regenerate assets.json."
        )
    )
    parser.add_argument("board_profile", help="Board profile folder under firmware/assets.")
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT, help="Defaults to firmware/assets.")
    parser.add_argument(
        "--pixel-format",
        choices=("rgb565", "rgb888"),
        help="Output pixel format. Defaults to RGB888 for the P4 7-inch board and RGB565 otherwise.",
    )
    parser.add_argument("--width", type=int, help="Override all picture output widths. Defaults to each source image width.")
    parser.add_argument("--height", type=int, help="Override all picture output heights. Defaults to each source image height.")
    parser.add_argument(
        "--fit",
        choices=("stretch", "contain", "cover"),
        default="stretch",
        help="Picture resize mode when resizing is needed. Defaults to stretch.",
    )
    parser.add_argument(
        "--sprite-size",
        type=_parse_size,
        help="Force all sprite outputs to WIDTHxHEIGHT. Defaults to each source image's native size.",
    )
    parser.add_argument(
        "--sprite-fit",
        choices=("stretch", "contain", "cover"),
        default="stretch",
        help="Sprite resize mode when resizing is needed. Defaults to stretch.",
    )
    parser.add_argument(
        "--byte-order",
        choices=("little", "big"),
        default="little",
        help="Raw RGB565 byte order. Defaults to little.",
    )
    parser.add_argument(
        "--asset-library-version",
        help="Manifest version. Defaults to incrementing today's suffix, for example 2026.09.16.4.",
    )
    parser.add_argument("--updated-at", help="Manifest updated_at. Defaults to current UTC timestamp.")
    parser.add_argument(
        "--keep-version",
        action="store_true",
        help="Keep the manifest's existing asset_library_version instead of bumping it.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print conversion and manifest commands without writing files.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    board_dir = args.asset_root / args.board_profile
    if not board_dir.exists():
        raise SystemExit(f"board asset directory not found: {board_dir}")
    if (args.width is None) != (args.height is None):
        raise SystemExit("--width and --height must be provided together")

    converter = Path(__file__).with_name("convert_image.py")
    converter_python = _find_converter_python()
    manifest_generator = Path(__file__).with_name("generate-board-asset-library.py")
    picture_dir = board_dir / "assets" / "picture"
    sprite_dir = board_dir / "assets" / "sprite"
    font_dir = board_dir / "assets" / "font"
    config_dir = board_dir / "assets" / "config"

    pixel_format = args.pixel_format or ("rgb888" if args.board_profile in RGB888_BOARD_PROFILES else "rgb565")
    output_suffix = f".{pixel_format}"
    converter_format = f"raw-{pixel_format}"

    picture_sources = _source_images(board_dir)
    sprite_sources = _source_images(board_dir / "sprites")
    p4_ui_config = config_dir / "items.yaml"
    sprite_metadata_sources = [] if p4_ui_config.exists() else _source_sprite_metadata(config_dir)
    yaml_python = _find_yaml_python() if any(
        path.suffix.lower() in {".yaml", ".yml"} for path in sprite_metadata_sources
    ) or p4_ui_config.exists() else None
    if not picture_sources and not sprite_sources and not sprite_metadata_sources and not p4_ui_config.exists():
        print(
            f"No source images or configuration found in {board_dir}, "
            f"{board_dir / 'sprites'}, or {config_dir}"
        )

    for source in picture_sources:
        picture_width, picture_height = (args.width, args.height) if args.width is not None else _image_size(source)
        output = picture_dir / f"{source.stem}{output_suffix}"
        command = [
            converter_python,
            str(converter),
            str(source),
            str(output),
            "--format",
            converter_format,
            "--width",
            str(picture_width),
            "--height",
            str(picture_height),
            "--fit",
            args.fit,
        ]
        if pixel_format == "rgb565":
            command.extend(["--byte-order", args.byte_order])
        _run(command, args.dry_run)

    for source in sprite_sources:
        sprite_width, sprite_height = args.sprite_size or _image_size(source)
        output = sprite_dir / f"{source.stem}{output_suffix}"
        alpha_output = sprite_dir / f"{source.stem}.alpha8"
        command = [
            converter_python,
            str(converter),
            str(source),
            str(output),
            "--format",
            converter_format,
            "--width",
            str(sprite_width),
            "--height",
            str(sprite_height),
            "--fit",
            args.sprite_fit,
            "--alpha-mode",
            "discard",
            "--alpha-output",
            str(alpha_output),
            "--alpha-mask-format",
            "alpha8",
        ]
        if pixel_format == "rgb565":
            command.extend(["--byte-order", args.byte_order])
        _run(command, args.dry_run)

    for source in sprite_metadata_sources:
        output = sprite_dir / source.name
        _write_sprite_metadata(source, output, args.dry_run, yaml_python)
    if p4_ui_config.exists():
        _write_p4_ui_config(config_dir, sprite_dir, args.dry_run, yaml_python)

    clock_font_source = font_dir / "manrope" / "Manrope-VariableFont_wght.ttf"
    if clock_font_source.exists():
        _run(
            [
                converter_python,
                str(Path(__file__).with_name("generate-clock-font.py")),
                str(clock_font_source),
                str(font_dir / "manrope" / "clock_42.hxf"),
                "--pixel-size",
                "42",
            ],
            args.dry_run,
        )
        _run(
            [
                converter_python,
                str(Path(__file__).with_name("generate-clock-font.py")),
                str(clock_font_source),
                str(font_dir / "manrope" / "date_32.hxf"),
                "--pixel-size",
                "32",
                "--glyphs= 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.",
            ],
            args.dry_run,
        )
        _run(
            [
                converter_python,
                str(Path(__file__).with_name("generate-clock-font.py")),
                str(clock_font_source),
                str(font_dir / "manrope" / "version_24.hxf"),
                "--pixel-size",
                "24",
                "--glyphs=-0123456789abcdef",
            ],
            args.dry_run,
        )

    manifest_args = [
        sys.executable,
        str(manifest_generator),
        args.board_profile,
        "--root",
        str(args.asset_root),
    ]
    if not args.keep_version:
        now = datetime.now(UTC)
        manifest_args.extend(
            [
                "--asset-library-version",
                args.asset_library_version or _next_library_version(board_dir / "assets" / "assets.json", now),
            ]
        )
    elif args.asset_library_version:
        raise SystemExit("--asset-library-version cannot be used with --keep-version")
    if args.updated_at:
        manifest_args.extend(["--updated-at", args.updated_at])
    _run(manifest_args, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
