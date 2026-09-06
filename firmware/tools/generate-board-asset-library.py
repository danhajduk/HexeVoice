#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import wave
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ASSET_ROOT = ROOT / "firmware" / "assets"

MEDIA_TYPES = ("picture", "sprite", "sound")
ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    "picture": {".rgb565", ".png", ".jpg", ".jpeg"},
    "sprite": {".rgb565", ".alpha8", ".alpha1", ".png", ".jpg", ".jpeg", ".json"},
    "sound": {".wav"},
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _default_library_version(now: datetime | None = None) -> str:
    stamp = now or datetime.now(UTC)
    return f"{stamp:%Y.%m.%d}.1"


def _load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _existing_assets_by_file(existing: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for item in existing.get("assets") or []:
        if not isinstance(item, dict):
            continue
        media_type = item.get("media_type")
        source_filename = item.get("source_filename") or item.get("filename")
        if isinstance(media_type, str) and isinstance(source_filename, str):
            indexed[(media_type, source_filename)] = item
    return indexed


def _asset_id_from_stem(stem: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem.strip()).strip("._-").lower()
    if not normalized:
        normalized = "asset"
    if normalized[0].isdigit():
        normalized = f"asset_{normalized}"
    return normalized[:80]


def _dedupe_asset_id(preferred: str, media_type: str, used: set[str]) -> str:
    if preferred not in used:
        used.add(preferred)
        return preferred
    typed = f"{media_type}_{preferred}"[:80]
    if typed not in used:
        used.add(typed)
        return typed
    suffix = 2
    while True:
        candidate = f"{typed[:75]}_{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        suffix += 1


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _infer_rgb565_dimensions(size_bytes: int, existing: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "pixel_format": "rgb565",
        "byte_order": str(existing.get("byte_order") or "little_endian"),
    }
    if isinstance(existing.get("width"), int) and isinstance(existing.get("height"), int):
        metadata["width"] = existing["width"]
        metadata["height"] = existing["height"]
        return metadata

    pixels = size_bytes // 2
    if size_bytes == 320 * 240 * 2:
        metadata["width"] = 320
        metadata["height"] = 240
        return metadata

    side = int(math.isqrt(pixels))
    if side * side == pixels:
        metadata["width"] = side
        metadata["height"] = side
    return metadata


def _wav_metadata(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            payload = handle.read()
        with wave.open(io.BytesIO(payload), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate_hz = wav.getframerate()
            sample_width_bytes = wav.getsampwidth()
            frame_count = wav.getnframes()
    except (OSError, wave.Error, EOFError):
        return {}

    return {
        "audio_format": "wav_pcm",
        "channels": channels,
        "sample_rate_hz": sample_rate_hz,
        "bits_per_sample": sample_width_bytes * 8,
        "duration_ms": int((frame_count / sample_rate_hz) * 1000) if sample_rate_hz else None,
    }


def _infer_metadata(path: Path, media_type: str, existing: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(existing)
    suffix = path.suffix.lower()
    size_bytes = path.stat().st_size

    if suffix == ".rgb565":
        metadata.update(_infer_rgb565_dimensions(size_bytes, metadata))
    elif suffix in {".alpha8", ".alpha1"}:
        metadata["alpha_format"] = suffix.lstrip(".")
    elif suffix == ".wav":
        metadata.update(_wav_metadata(path))
    elif suffix == ".json":
        metadata.setdefault("content_format", "json")
    elif media_type in {"picture", "sprite"} and suffix in {".png", ".jpg", ".jpeg"}:
        metadata.setdefault("source_format", suffix.lstrip("."))

    metadata["size_bytes"] = size_bytes
    metadata["sha256"] = _hash_file(path)
    return metadata


def _scan_media_files(assets_dir: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for media_type in MEDIA_TYPES:
        media_dir = assets_dir / media_type
        if not media_dir.exists():
            continue
        for path in sorted(item for item in media_dir.iterdir() if item.is_file()):
            if path.name.startswith("."):
                continue
            if path.suffix.lower() not in ALLOWED_EXTENSIONS[media_type]:
                continue
            files.append((media_type, path))
    return files


def build_asset_library(
    *,
    asset_root: Path,
    board_profile: str,
    asset_library_version: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    board_dir = asset_root / board_profile
    assets_dir = board_dir / "assets"
    library_path = assets_dir / "assets.json"
    existing = _load_existing(library_path)
    existing_by_file = _existing_assets_by_file(existing)
    used_asset_ids: set[str] = set()
    assets: list[dict[str, Any]] = []

    for media_type, path in _scan_media_files(assets_dir):
        prior = existing_by_file.get((media_type, path.name), {})
        preferred_asset_id = str(prior.get("asset_id") or _asset_id_from_stem(path.stem))
        asset_id = _dedupe_asset_id(preferred_asset_id, media_type, used_asset_ids)
        item: dict[str, Any] = {
            "asset_id": asset_id,
            "media_type": media_type,
            "filename": str(prior.get("filename") or path.name),
            "source_filename": path.name,
        }
        if prior.get("role"):
            item["role"] = prior["role"]
        if prior.get("version"):
            item["version"] = prior["version"]
        prior_metadata = prior.get("metadata") if isinstance(prior.get("metadata"), dict) else {}
        item["metadata"] = _infer_metadata(path, media_type, prior_metadata)
        assets.append(item)

    assets.sort(key=lambda item: (str(item["media_type"]), str(item["asset_id"])))
    version = asset_library_version or existing.get("asset_library_version") or _default_library_version()
    payload = {
        "schema_version": 1,
        "board_profile": board_profile,
        "asset_library_version": version,
        "updated_at": updated_at or existing.get("updated_at") or _utc_now(),
        "assets": assets,
    }

    old_payload = dict(existing)
    old_payload.pop("updated_at", None)
    new_payload = dict(payload)
    new_payload.pop("updated_at", None)
    if existing and old_payload != new_payload:
        payload["updated_at"] = updated_at or _utc_now()
    return payload


def _render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a board endpoint asset library assets.json.")
    parser.add_argument("board_profile", help="Board profile folder under the asset root.")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ASSET_ROOT,
        help="Asset root directory. Defaults to firmware/assets.",
    )
    parser.add_argument(
        "--asset-library-version",
        help="Version to write into assets.json. Defaults to the existing version or today's .1 version.",
    )
    parser.add_argument("--updated-at", help="Override updated_at in ISO-8601 UTC form.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if assets.json is missing or stale.")
    args = parser.parse_args(argv)

    assets_dir = args.root / args.board_profile / "assets"
    if not assets_dir.exists():
        parser.error(f"board asset directory does not exist: {assets_dir}")

    library_path = assets_dir / "assets.json"
    try:
        payload = build_asset_library(
            asset_root=args.root,
            board_profile=args.board_profile,
            asset_library_version=args.asset_library_version,
            updated_at=args.updated_at,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    rendered = _render(payload)

    if args.check:
        try:
            current = _load_existing(library_path)
        except (OSError, ValueError):
            print(f"{library_path} is missing or invalid", file=sys.stderr)
            return 1
        if current != payload:
            print(f"{library_path} is stale; run this script without --check", file=sys.stderr)
            return 1
        return 0

    library_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote {library_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
