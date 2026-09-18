from __future__ import annotations

import asyncio
from hashlib import sha256
from io import BytesIO

import httpx
from PIL import Image

from hexevoice.radar_assets import RADAR_HEIGHT, RADAR_WIDTH, RadarAssetService


def png_bytes() -> bytes:
    image = Image.new("RGB", (1200, 600), (220, 20, 40))
    for x in range(400, 800):
        for y in range(600):
            image.putpixel((x, y), (20, 180, 80))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_prepares_center_cropped_rgb888_asset_and_reuses_cache(tmp_path, monkeypatch):
    source = png_bytes()
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        assert str(request.url) == "https://interaction.local/radar.png"
        return httpx.Response(200, content=source, headers={"content-type": "image/png"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    service = RadarAssetService(tmp_path)
    snapshot = {
        "snapshot_id": "weather-home-1",
        "radar": {
            "status": "ready",
            "frame_id": "frame-1",
            "image_url": "https://interaction.local/radar.png",
            "sha256": sha256(source).hexdigest(),
        },
    }

    prepared = asyncio.run(service.prepare(snapshot))
    again = asyncio.run(service.prepare(snapshot))

    assert calls == 1
    assert prepared == again
    assert prepared.size_bytes == RADAR_WIDTH * RADAR_HEIGHT * 3
    assert prepared.path.read_bytes()[0:3] == bytes((220, 20, 40))
    center_offset = ((RADAR_HEIGHT // 2) * RADAR_WIDTH + (RADAR_WIDTH // 2)) * 3
    assert prepared.path.read_bytes()[center_offset : center_offset + 3] == bytes((20, 180, 80))
    assert sha256(prepared.path.read_bytes()).hexdigest() == prepared.sha256


def test_rejects_source_checksum_mismatch(tmp_path, monkeypatch):
    source = png_bytes()

    async def handler(request):
        return httpx.Response(200, content=source)

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    service = RadarAssetService(tmp_path)
    snapshot = {
        "snapshot_id": "weather-home-1",
        "radar": {
            "status": "ready",
            "frame_id": "frame-1",
            "image_url": "https://interaction.local/radar.png",
            "sha256": "0" * 64,
        },
    }

    try:
        asyncio.run(service.prepare(snapshot))
    except RuntimeError as exc:
        assert str(exc) == "radar_source_checksum_mismatch"
    else:
        raise AssertionError("checksum mismatch was accepted")
