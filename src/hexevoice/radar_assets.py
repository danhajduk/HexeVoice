from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError


RADAR_WIDTH = 800
RADAR_HEIGHT = 420
RADAR_X = 112
RADAR_Y = 96
MAX_RADAR_SOURCE_BYTES = 12 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RadarAssetError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedRadarAsset:
    asset_id: str
    snapshot_id: str
    revision: str
    path: Path
    sha256: str
    size_bytes: int
    width: int = RADAR_WIDTH
    height: int = RADAR_HEIGHT
    x: int = RADAR_X
    y: int = RADAR_Y

    def as_dict(self, *, download_url: str) -> dict[str, Any]:
        return {
            "asset_type": "weather.radar",
            "asset_id": self.asset_id,
            "snapshot_id": self.snapshot_id,
            "revision": self.revision,
            "download_url": download_url,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_type": "application/x-hexe-rgb888",
            "pixel_format": "rgb888",
            "width": self.width,
            "height": self.height,
            "x": self.x,
            "y": self.y,
        }


class RadarAssetService:
    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._records: dict[str, PreparedRadarAsset] = {}

    async def prepare(self, snapshot: dict[str, Any]) -> PreparedRadarAsset:
        radar = snapshot.get("radar") if isinstance(snapshot.get("radar"), dict) else {}
        if radar.get("status") != "ready":
            raise RadarAssetError("radar_not_ready")
        image_url = str(radar.get("image_url") or "").strip()
        source_sha256 = str(radar.get("sha256") or "").lower()
        if not image_url or SHA256_RE.fullmatch(source_sha256) is None:
            raise RadarAssetError("invalid_radar_reference")
        snapshot_id = _safe_id(snapshot.get("snapshot_id"), "snapshot_id")
        revision = _safe_id(radar.get("frame_id") or source_sha256[:24], "revision")
        asset_id = f"weather-radar-{sha256(f'{snapshot_id}:{revision}'.encode()).hexdigest()[:24]}"
        existing = self._records.get(asset_id) or self._load_record(asset_id)
        if existing is not None and existing.path.is_file():
            self._records[asset_id] = existing
            return existing

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(image_url)
            response.raise_for_status()
            source = response.content
        if not source or len(source) > MAX_RADAR_SOURCE_BYTES:
            raise RadarAssetError("radar_source_size_invalid")
        if sha256(source).hexdigest() != source_sha256:
            raise RadarAssetError("radar_source_checksum_mismatch")
        pixels = await asyncio.to_thread(_convert_to_rgb888, source)
        digest = sha256(pixels).hexdigest()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_dir / f"{asset_id}.rgb888"
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(pixels)
        temporary.replace(path)
        asset = PreparedRadarAsset(
            asset_id=asset_id,
            snapshot_id=snapshot_id,
            revision=revision,
            path=path,
            sha256=digest,
            size_bytes=len(pixels),
        )
        self._write_record(asset)
        self._records[asset_id] = asset
        return asset

    def path(self, asset_id: str) -> Path | None:
        if not re.fullmatch(r"weather-radar-[0-9a-f]{24}", asset_id or ""):
            return None
        asset = self._records.get(asset_id) or self._load_record(asset_id)
        return asset.path if asset is not None and asset.path.is_file() else None

    def _record_path(self, asset_id: str) -> Path:
        return self._cache_dir / f"{asset_id}.json"

    def _write_record(self, asset: PreparedRadarAsset) -> None:
        payload = {
            "asset_id": asset.asset_id,
            "snapshot_id": asset.snapshot_id,
            "revision": asset.revision,
            "sha256": asset.sha256,
            "size_bytes": asset.size_bytes,
        }
        self._record_path(asset.asset_id).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def _load_record(self, asset_id: str) -> PreparedRadarAsset | None:
        try:
            payload = json.loads(self._record_path(asset_id).read_text(encoding="utf-8"))
            asset = PreparedRadarAsset(
                asset_id=asset_id,
                snapshot_id=_safe_id(payload.get("snapshot_id"), "snapshot_id"),
                revision=_safe_id(payload.get("revision"), "revision"),
                path=self._cache_dir / f"{asset_id}.rgb888",
                sha256=str(payload["sha256"]),
                size_bytes=int(payload["size_bytes"]),
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None
        if SHA256_RE.fullmatch(asset.sha256) is None or asset.size_bytes != RADAR_WIDTH * RADAR_HEIGHT * 3:
            return None
        return asset


def _safe_id(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", normalized):
        raise RadarAssetError(f"invalid_{label}")
    return normalized


def _convert_to_rgb888(source: bytes) -> bytes:
    try:
        with Image.open(BytesIO(source)) as image:
            image.load()
            converted = ImageOps.fit(
                image.convert("RGB"),
                (RADAR_WIDTH, RADAR_HEIGHT),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            return converted.tobytes("raw", "RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RadarAssetError("radar_image_invalid") from exc
