from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from hexevoice.config import Settings
from hexevoice.main import create_app


def test_firmware_board_asset_route_serves_manifest_and_payload(tmp_path: Path) -> None:
    payload = bytes(320 * 240 * 2)
    board_assets = tmp_path / "assets" / "waveshare_s3_touch_lcd_1_85c_box_v2" / "assets"
    (board_assets / "picture").mkdir(parents=True)
    (board_assets / "picture" / "idle.rgb565").write_bytes(payload)
    (board_assets / "assets.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "board_profile": "waveshare_s3_touch_lcd_1_85c_box_v2",
                "asset_library_version": "2026.09.06.1",
                "assets": [
                    {
                        "asset_id": "idle_face",
                        "media_type": "picture",
                        "filename": "idle.rgb565",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                endpoint_asset_library_dir=tmp_path / "assets",
            )
        )
    )

    manifest = client.get("/firmware/assets/waveshare_s3_touch_lcd_1_85c_box_v2/assets/assets.json")
    asset = client.get("/firmware/assets/waveshare_s3_touch_lcd_1_85c_box_v2/assets/picture/idle.rgb565")
    traversal = client.get("/firmware/assets/waveshare_s3_touch_lcd_1_85c_box_v2/assets/picture/%2E%2E/assets.json")

    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/json")
    assert manifest.json()["asset_library_version"] == "2026.09.06.1"
    assert manifest.json()["assets"][0]["size_bytes"] == len(payload)
    assert len(manifest.json()["assets"][0]["sha256"]) == 64
    assert asset.status_code == 200
    assert asset.content == payload
    assert traversal.status_code == 400
