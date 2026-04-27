from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any, Literal
from uuid import uuid4
import wave

from pydantic import BaseModel, Field


EndpointMediaType = Literal["picture", "sprite", "sound"]

PICTURE_BYTES = 320 * 240 * 2
SPRITE_MAX_BYTES = 512 * 1024
SOUND_MAX_BYTES = 5 * 1024 * 1024

DESTINATIONS: dict[EndpointMediaType, str] = {
    "picture": "picture",
    "sprite": "sprite",
    "sound": "sound",
}

DESTINATION_PATHS: dict[EndpointMediaType, str] = {
    "picture": "/sdcard/hexe/pictures",
    "sprite": "/sdcard/hexe/sprites",
    "sound": "/sdcard/hexe/sounds",
}

ALLOWED_EXTENSIONS: dict[EndpointMediaType, set[str]] = {
    "picture": {".rgb565", ".png", ".jpg", ".jpeg"},
    "sprite": {".rgb565", ".alpha8", ".alpha1", ".png", ".jpg", ".jpeg", ".json"},
    "sound": {".wav"},
}


class EndpointMediaValidationError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class EndpointMediaAsset(BaseModel):
    asset_id: str
    media_type: EndpointMediaType
    destination: str
    endpoint_path: str
    filename: str
    source_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class EndpointMediaLibrary(BaseModel):
    schema_version: int = 1
    assets: dict[str, EndpointMediaAsset] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def safe_filename(filename: str) -> str:
    name = filename.strip()
    if not name:
        raise EndpointMediaValidationError("invalid_filename", "Filename is required.")
    if name.startswith(".") or "/" in name or "\\" in name or ".." in name:
        raise EndpointMediaValidationError("invalid_filename", "Filename must be a simple file name.")
    if any(ord(char) < 32 for char in name):
        raise EndpointMediaValidationError("invalid_filename", "Filename contains control characters.")
    if len(name) > 120:
        raise EndpointMediaValidationError("invalid_filename", "Filename is too long.")
    return name


def safe_asset_id(asset_id: str | None) -> str:
    if not asset_id:
        return f"media_{uuid4().hex}"
    normalized = asset_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", normalized):
        raise EndpointMediaValidationError("invalid_asset_id", "Asset id may contain letters, numbers, dots, dashes, and underscores.")
    if normalized.startswith(".") or ".." in normalized:
        raise EndpointMediaValidationError("invalid_asset_id", "Asset id is not safe.")
    return normalized


class EndpointMediaService:
    def __init__(self, *, media_dir: Path) -> None:
        self._media_dir = media_dir
        self._manifest_path = media_dir / "manifest.json"

    def list_assets(self) -> list[EndpointMediaAsset]:
        return sorted(self._load().assets.values(), key=lambda item: item.updated_at, reverse=True)

    def get_asset(self, asset_id: str) -> EndpointMediaAsset:
        library = self._load()
        asset = library.assets.get(asset_id)
        if asset is None:
            raise EndpointMediaValidationError("media_asset_not_found", "Media asset was not found.", status_code=404)
        return asset

    def delete_asset(self, asset_id: str) -> EndpointMediaAsset:
        library = self._load()
        asset = library.assets.pop(asset_id, None)
        if asset is None:
            raise EndpointMediaValidationError("media_asset_not_found", "Media asset was not found.", status_code=404)
        path = self.payload_path(asset)
        if path.exists():
            path.unlink()
        asset_dir = self._asset_dir(asset.asset_id)
        try:
            asset_dir.rmdir()
        except OSError:
            pass
        self._save(library)
        return asset

    def payload_path(self, asset: EndpointMediaAsset) -> Path:
        path = (self._asset_dir(asset.asset_id) / asset.filename).resolve()
        asset_dir = self._asset_dir(asset.asset_id).resolve()
        if path.parent != asset_dir:
            raise EndpointMediaValidationError("invalid_media_path", "Stored media path is invalid.")
        return path

    def store_upload(
        self,
        *,
        media_type: EndpointMediaType,
        filename: str,
        content_base64: str,
        asset_id: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> EndpointMediaAsset:
        metadata = dict(metadata or {})
        asset_id = safe_asset_id(asset_id)
        source_filename = safe_filename(filename)
        source_ext = Path(source_filename).suffix.lower()
        if source_ext not in ALLOWED_EXTENSIONS[media_type]:
            raise EndpointMediaValidationError("unsupported_media_extension", f"{source_ext or '<none>'} is not allowed for {media_type}.")

        library = self._load()
        if asset_id in library.assets and not overwrite:
            raise EndpointMediaValidationError("duplicate_media_asset", "Media asset already exists.", status_code=409)

        try:
            source_bytes = base64.b64decode(content_base64, validate=True)
        except ValueError as exc:
            raise EndpointMediaValidationError("invalid_content_base64", "Content must be valid base64.") from exc

        endpoint_bytes, endpoint_filename, endpoint_content_type, endpoint_metadata = self._prepare_endpoint_payload(
            media_type=media_type,
            source_filename=source_filename,
            source_bytes=source_bytes,
            content_type=content_type,
            metadata=metadata,
        )
        sha256 = hashlib.sha256(endpoint_bytes).hexdigest()
        now = datetime.now(UTC).isoformat()
        asset = EndpointMediaAsset(
            asset_id=asset_id,
            media_type=media_type,
            destination=DESTINATIONS[media_type],
            endpoint_path=f"{DESTINATION_PATHS[media_type]}/{endpoint_filename}",
            filename=endpoint_filename,
            source_filename=source_filename,
            content_type=endpoint_content_type,
            size_bytes=len(endpoint_bytes),
            sha256=sha256,
            metadata=endpoint_metadata,
            created_at=library.assets.get(asset_id).created_at if asset_id in library.assets else now,
            updated_at=now,
        )
        asset_dir = self._asset_dir(asset.asset_id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        temp_path = asset_dir / f".{asset.filename}.tmp"
        temp_path.write_bytes(endpoint_bytes)
        temp_path.replace(asset_dir / asset.filename)
        library.assets[asset.asset_id] = asset
        self._save(library)
        return asset

    def _prepare_endpoint_payload(
        self,
        *,
        media_type: EndpointMediaType,
        source_filename: str,
        source_bytes: bytes,
        content_type: str | None,
        metadata: dict[str, Any],
    ) -> tuple[bytes, str, str, dict[str, Any]]:
        suffix = Path(source_filename).suffix.lower()
        if media_type == "picture":
            if suffix == ".rgb565":
                if len(source_bytes) != PICTURE_BYTES:
                    raise EndpointMediaValidationError("invalid_picture_size", "Picture RGB565 payload must be exactly 153600 bytes.")
                return source_bytes, source_filename, content_type or "application/octet-stream", {
                    **metadata,
                    "pixel_format": "rgb565",
                    "width": 320,
                    "height": 240,
                }
            converted = self._convert_image_to_rgb565(source_bytes, width=320, height=240)
            return converted, f"{Path(source_filename).stem}.rgb565", "application/octet-stream", {
                **metadata,
                "pixel_format": "rgb565",
                "width": 320,
                "height": 240,
                "converted_from": suffix.lstrip("."),
            }

        if media_type == "sprite":
            if suffix == ".json":
                if len(source_bytes) > SPRITE_MAX_BYTES:
                    raise EndpointMediaValidationError("sprite_too_large", "Sprite metadata is too large.")
                return source_bytes, source_filename, content_type or "application/json", metadata
            if suffix in {".alpha8", ".alpha1"}:
                if len(source_bytes) > SPRITE_MAX_BYTES:
                    raise EndpointMediaValidationError("sprite_too_large", "Alpha mask payload is too large.")
                return source_bytes, source_filename, content_type or "application/octet-stream", {
                    **metadata,
                    "alpha_format": suffix.lstrip("."),
                }
            if suffix == ".rgb565":
                width = int(metadata.get("width") or 0)
                height = int(metadata.get("height") or 0)
                if width <= 0 or height <= 0:
                    raise EndpointMediaValidationError("missing_sprite_dimensions", "Sprite RGB565 uploads require width and height metadata.")
                if len(source_bytes) != width * height * 2:
                    raise EndpointMediaValidationError("invalid_sprite_size", "Sprite RGB565 size does not match width and height.")
                if len(source_bytes) > SPRITE_MAX_BYTES:
                    raise EndpointMediaValidationError("sprite_too_large", "Sprite payload is too large.")
                return source_bytes, source_filename, content_type or "application/octet-stream", {
                    **metadata,
                    "pixel_format": "rgb565",
                    "width": width,
                    "height": height,
                }
            width = int(metadata.get("width") or 0)
            height = int(metadata.get("height") or 0)
            if width <= 0 or height <= 0:
                raise EndpointMediaValidationError("missing_sprite_dimensions", "Sprite image conversion requires width and height metadata.")
            converted = self._convert_image_to_rgb565(source_bytes, width=width, height=height)
            if len(converted) > SPRITE_MAX_BYTES:
                raise EndpointMediaValidationError("sprite_too_large", "Converted sprite payload is too large.")
            return converted, f"{Path(source_filename).stem}.rgb565", "application/octet-stream", {
                **metadata,
                "pixel_format": "rgb565",
                "width": width,
                "height": height,
                "converted_from": suffix.lstrip("."),
            }

        if len(source_bytes) > SOUND_MAX_BYTES:
            raise EndpointMediaValidationError("sound_too_large", "Sound payload is too large.")
        sound_metadata = self._validate_wav(source_bytes)
        return source_bytes, source_filename, content_type or "audio/wav", {**metadata, **sound_metadata}

    def _convert_image_to_rgb565(self, source_bytes: bytes, *, width: int, height: int) -> bytes:
        try:
            from PIL import Image
        except ImportError as exc:
            raise EndpointMediaValidationError(
                "image_conversion_unavailable",
                "Pillow is required to convert PNG/JPEG uploads; upload pre-converted .rgb565 instead.",
                status_code=503,
            ) from exc

        with Image.open(io.BytesIO(source_bytes)) as image:
            image = image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
            output = bytearray()
            for y in range(height):
                for x in range(width):
                    r, g, b = image.getpixel((x, y))
                    value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                    output.extend(value.to_bytes(2, "little"))
            return bytes(output)

    def _validate_wav(self, payload: bytes) -> dict[str, Any]:
        try:
            with wave.open(io.BytesIO(payload), "rb") as wav:
                channels = wav.getnchannels()
                sample_rate_hz = wav.getframerate()
                sample_width_bytes = wav.getsampwidth()
                frame_count = wav.getnframes()
        except (wave.Error, EOFError) as exc:
            raise EndpointMediaValidationError("invalid_wav", "Sound uploads must be valid WAV PCM files.") from exc
        bits_per_sample = sample_width_bytes * 8
        if channels not in {1, 2} or bits_per_sample not in {16, 24, 32} or sample_rate_hz <= 0:
            raise EndpointMediaValidationError("unsupported_wav", "WAV file uses unsupported audio parameters.")
        return {
            "audio_format": "wav_pcm",
            "channels": channels,
            "sample_rate_hz": sample_rate_hz,
            "bits_per_sample": bits_per_sample,
            "duration_ms": int((frame_count / sample_rate_hz) * 1000) if sample_rate_hz else None,
        }

    def _asset_dir(self, asset_id: str) -> Path:
        return self._media_dir / safe_asset_id(asset_id)

    def _load(self) -> EndpointMediaLibrary:
        if not self._manifest_path.exists():
            return EndpointMediaLibrary()
        return EndpointMediaLibrary.model_validate(json.loads(self._manifest_path.read_text()))

    def _save(self, library: EndpointMediaLibrary) -> None:
        self._media_dir.mkdir(parents=True, exist_ok=True)
        library.updated_at = datetime.now(UTC).isoformat()
        temp_path = self._manifest_path.with_suffix(".json.tmp")
        temp_path.write_text(library.model_dump_json(indent=2))
        temp_path.replace(self._manifest_path)
