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
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


RADAR_WIDTH = 800
RADAR_HEIGHT = 420
RADAR_X = 112
RADAR_Y = 96
MAX_RADAR_SOURCE_BYTES = 12 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WEATHER_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "weather_icons"
WEATHER_ICON_FILES = {
    "clear": ("clear-day.png", "clear-night.png"),
    "cloudy": ("overcast.png", "overcast.png"),
    "drizzle": ("drizzle.png", "partly-cloudy-night-drizzle.png"),
    "fog": ("fog.png", "fog-night.png"),
    "partly_cloudy": ("partly-cloudy-day.png", "partly-cloudy-night.png"),
    "rain": ("rain.png", "partly-cloudy-night-rain.png"),
    "snow": ("snow.png", "partly-cloudy-night-snow.png"),
    "thunderstorm": ("thunderstorms.png", "thunderstorms-night.png"),
    "wind": ("wind.png", "wind.png"),
}


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
        background = snapshot.get("bg") if isinstance(snapshot.get("bg"), dict) else {}
        snapshot_id = _safe_id(snapshot.get("snapshot_id"), "snapshot_id")
        visual_state = _weather_visual_state(snapshot)
        revision = sha256(json.dumps(visual_state, sort_keys=True).encode()).hexdigest()[:24]
        asset_id = f"weather-radar-{sha256(f'{snapshot_id}:{revision}'.encode()).hexdigest()[:24]}"
        existing = self._records.get(asset_id) or self._load_record(asset_id)
        if existing is not None and existing.path.is_file():
            self._records[asset_id] = existing
            return existing

        source = await self._download_visual_source(radar, "radar")
        if source is None:
            source = await self._download_visual_source(background, "bg")
        pixels = await asyncio.to_thread(_compose_weather_rgb888, source, snapshot)
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

    @staticmethod
    async def _download_visual_source(component: dict[str, Any], name: str) -> bytes | None:
        if component.get("status") not in {"ready", "stale"}:
            return None
        image_url = str(component.get("image_url") or "").strip()
        source_sha256 = str(component.get("sha256") or "").lower()
        if not image_url or SHA256_RE.fullmatch(source_sha256) is None:
            raise RadarAssetError(f"invalid_{name}_reference")
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(image_url)
            response.raise_for_status()
            source = response.content
        if not source or len(source) > MAX_RADAR_SOURCE_BYTES:
            raise RadarAssetError(f"{name}_source_size_invalid")
        if sha256(source).hexdigest() != source_sha256:
            raise RadarAssetError(f"{name}_source_checksum_mismatch")
        return source

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


def _weather_visual_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    radar = snapshot.get("radar") if isinstance(snapshot.get("radar"), dict) else {}
    background = snapshot.get("bg") if isinstance(snapshot.get("bg"), dict) else {}
    current = snapshot.get("current_conditions") if isinstance(snapshot.get("current_conditions"), dict) else {}
    forecast = snapshot.get("forecast_summary") if isinstance(snapshot.get("forecast_summary"), dict) else {}
    location = snapshot.get("location") if isinstance(snapshot.get("location"), dict) else {}
    return {
        "radar_revision": radar.get("frame_id") or radar.get("sha256") or radar.get("status"),
        "bg_revision": background.get("revision") or background.get("sha256") or background.get("status"),
        "location": location.get("label"),
        "temperature": current.get("temperature"),
        "unit": current.get("unit"),
        "condition": current.get("condition"),
        "condition_key": current.get("condition_key"),
        "is_day": current.get("is_day"),
        "today_high": forecast.get("today_high"),
        "today_low": forecast.get("today_low"),
    }


def _compose_weather_rgb888(source: bytes | None, snapshot: dict[str, Any]) -> bytes:
    try:
        if source is not None:
            with Image.open(BytesIO(source)) as image:
                image.load()
                canvas = ImageOps.fit(
                    image.convert("RGB"),
                    (RADAR_WIDTH, RADAR_HEIGHT),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                ).convert("RGBA")
        else:
            canvas = Image.new("RGBA", (RADAR_WIDTH, RADAR_HEIGHT), "#07131d")

        _draw_weather_overlay(canvas, snapshot)
        return canvas.convert("RGB").tobytes("raw", "RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RadarAssetError("radar_image_invalid") from exc


def _draw_weather_overlay(canvas: Image.Image, snapshot: dict[str, Any]) -> None:
    current = snapshot.get("current_conditions") if isinstance(snapshot.get("current_conditions"), dict) else {}
    forecast = snapshot.get("forecast_summary") if isinstance(snapshot.get("forecast_summary"), dict) else {}
    location = snapshot.get("location") if isinstance(snapshot.get("location"), dict) else {}
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    panel = (4, 15, 23, 216)
    border = (86, 184, 255, 190)

    draw.rounded_rectangle((18, 16, 286, 142), radius=18, fill=panel, outline=border, width=2)
    draw.rounded_rectangle((616, 16, 782, 170), radius=18, fill=panel, outline=border, width=2)
    draw.rounded_rectangle((18, 348, 286, 404), radius=16, fill=panel, outline=(53, 244, 219, 170), width=2)

    regular = WEATHER_ASSETS_DIR / "Manrope-VariableFont_wght.ttf"
    font_location = ImageFont.truetype(str(regular), 20)
    font_temperature = ImageFont.truetype(str(regular), 58)
    font_condition = ImageFont.truetype(str(regular), 18)
    font_range = ImageFont.truetype(str(regular), 24)

    location_text = str(location.get("label") or "Weather")[:24]
    draw.text((34, 27), location_text, font=font_location, fill="#A9BBC8")
    draw.text((32, 53), _temperature_text(current), font=font_temperature, fill="#F4FBFF", stroke_width=1)

    icon_path = WEATHER_ASSETS_DIR / _weather_icon_name(current)
    with Image.open(icon_path) as icon:
        icon = ImageOps.contain(icon.convert("RGBA"), (116, 116), method=Image.Resampling.LANCZOS)
        overlay.alpha_composite(icon, (641 + (116 - icon.width) // 2, 20))
    condition = str(current.get("condition") or "Current weather")[:24]
    condition_width = draw.textbbox((0, 0), condition, font=font_condition)[2]
    draw.text((699 - condition_width // 2, 139), condition, font=font_condition, fill="#D8FFFA")

    high = _compact_temperature(forecast.get("today_high"), current.get("unit"))
    low = _compact_temperature(forecast.get("today_low"), current.get("unit"))
    range_text = f"H {high}     L {low}"
    draw.text((34, 359), range_text, font=font_range, fill="#F4FBFF")
    canvas.alpha_composite(overlay)


def _temperature_text(current: dict[str, Any]) -> str:
    value = current.get("temperature")
    if value is None:
        return "--"
    return f"{_number_text(value)}\N{DEGREE SIGN}{_unit_suffix(current.get('unit'))}"


def _compact_temperature(value: object, unit: object) -> str:
    return "--" if value is None else f"{_number_text(value)}\N{DEGREE SIGN}{_unit_suffix(unit)}"


def _number_text(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)[:8]
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def _unit_suffix(value: object) -> str:
    unit = str(value or "").strip().lower().replace("\N{DEGREE SIGN}", "")
    if unit in {"f", "fahrenheit"}:
        return "F"
    if unit in {"c", "celsius"}:
        return "C"
    return ""


def _weather_icon_name(current: dict[str, Any]) -> str:
    key = str(current.get("condition_key") or "").strip().lower().replace("-", "_")
    condition = str(current.get("condition") or "").lower()
    if key not in WEATHER_ICON_FILES:
        for candidate in ("thunderstorm", "drizzle", "rain", "snow", "fog", "wind", "cloudy", "clear"):
            if candidate in condition:
                key = candidate
                break
        else:
            key = "partly_cloudy"
    day_name, night_name = WEATHER_ICON_FILES[key]
    return day_name if current.get("is_day") is not False else night_name
