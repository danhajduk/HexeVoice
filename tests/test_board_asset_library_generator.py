from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import wave


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "firmware" / "tools" / "generate-board-asset-library.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_board_asset_library", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * 800)


def test_generate_board_asset_library_scans_typed_asset_folders(tmp_path):
    generator = _load_generator()
    assets_root = tmp_path / "assets-root"
    board_assets = assets_root / "ha_voice_pe" / "assets"
    (board_assets / "picture").mkdir(parents=True)
    (board_assets / "sound").mkdir()
    (board_assets / "picture" / "Idle Face.rgb565").write_bytes(b"\x00" * (320 * 240 * 2))
    _write_wav(board_assets / "sound" / "chime.wav")

    result = generator.main(
        [
            "--root",
            str(assets_root),
            "--asset-library-version",
            "2026.09.06.1",
            "--updated-at",
            "2026-09-06T00:00:00+00:00",
            "ha_voice_pe",
        ]
    )

    assert result == 0
    payload = json.loads((board_assets / "assets.json").read_text(encoding="utf-8"))
    assert payload["board_profile"] == "ha_voice_pe"
    assert payload["asset_library_version"] == "2026.09.06.1"
    assert [asset["asset_id"] for asset in payload["assets"]] == ["idle_face", "chime"]
    picture = payload["assets"][0]
    assert picture["media_type"] == "picture"
    assert picture["filename"] == "Idle Face.rgb565"
    assert picture["source_filename"] == "Idle Face.rgb565"
    assert picture["metadata"]["pixel_format"] == "rgb565"
    assert picture["metadata"]["width"] == 320
    assert picture["metadata"]["height"] == 240
    assert len(picture["metadata"]["sha256"]) == 64
    sound = payload["assets"][1]
    assert sound["media_type"] == "sound"
    assert sound["metadata"]["audio_format"] == "wav_pcm"
    assert sound["metadata"]["sample_rate_hz"] == 16_000
    assert sound["metadata"]["duration_ms"] == 50
    assert generator.main(["--root", str(assets_root), "--check", "ha_voice_pe"]) == 0


def test_generate_board_asset_library_preserves_existing_metadata(tmp_path):
    generator = _load_generator()
    assets_root = tmp_path / "assets-root"
    board_assets = assets_root / "waveshare_s3_touch_lcd_1_85c_box_v2" / "assets"
    (board_assets / "picture").mkdir(parents=True)
    (board_assets / "picture" / "idle_clock_face.rgb565").write_bytes(b"\x00" * (360 * 360 * 2))
    (board_assets / "assets.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "board_profile": "waveshare_s3_touch_lcd_1_85c_box_v2",
                "asset_library_version": "waveshare-185c-ui-20260906.1",
                "updated_at": "2026-09-06T00:00:00+00:00",
                "assets": [
                    {
                        "asset_id": "idle_clock_face",
                        "media_type": "picture",
                        "filename": "idle_clock_face.rgb565",
                        "source_filename": "idle_clock_face.rgb565",
                        "role": "idle_clock_face",
                        "version": "2026.09.06.1",
                        "metadata": {
                            "width": 360,
                            "height": 360,
                            "shape": "round",
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = generator.main(["--root", str(assets_root), "waveshare_s3_touch_lcd_1_85c_box_v2"])

    assert result == 0
    payload = json.loads((board_assets / "assets.json").read_text(encoding="utf-8"))
    assert payload["asset_library_version"] == "waveshare-185c-ui-20260906.1"
    first_updated_at = payload["updated_at"]
    assert first_updated_at != "2026-09-06T00:00:00+00:00"
    assert payload["assets"][0]["role"] == "idle_clock_face"
    assert payload["assets"][0]["version"] == "2026.09.06.1"
    assert payload["assets"][0]["metadata"]["shape"] == "round"
    assert payload["assets"][0]["metadata"]["width"] == 360
    assert payload["assets"][0]["metadata"]["height"] == 360
    assert payload["assets"][0]["metadata"]["size_bytes"] == 360 * 360 * 2

    result = generator.main(["--root", str(assets_root), "waveshare_s3_touch_lcd_1_85c_box_v2"])

    assert result == 0
    payload = json.loads((board_assets / "assets.json").read_text(encoding="utf-8"))
    assert payload["updated_at"] == first_updated_at
