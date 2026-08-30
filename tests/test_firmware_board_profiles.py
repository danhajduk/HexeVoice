from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "firmware/tools/validate_board_profiles.py"
PROFILE_ROOT = REPO_ROOT / "firmware/boards"


def load_validator_module():
    spec = importlib.util.spec_from_file_location("validate_board_profiles", VALIDATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_firmware_board_profiles_validate():
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(PROFILE_ROOT)],
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stdout.strip() == "Validated 4 board profile(s)."


def test_board_profile_validator_accepts_json_profiles(tmp_path):
    validator = load_validator_module()
    source = PROFILE_ROOT / "ha_voice_pe/board.yaml"
    profile = validator.load_profile(source)
    profile_dir = tmp_path / "ha_voice_pe"
    profile_dir.mkdir()
    profile_path = profile_dir / "board.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(profile_path)],
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stdout.strip() == "Validated 1 board profile(s)."


def test_waveshare_1_85c_profile_requires_v2_and_rejects_v1(tmp_path):
    source = PROFILE_ROOT / "waveshare_s3_touch_lcd_1_85c_box_v2/board.yaml"
    profile_dir = tmp_path / "waveshare_s3_touch_lcd_1_85c_box_v2"
    profile_dir.mkdir()
    invalid_profile = source.read_text(encoding="utf-8").replace(
        "unsupported:\n    - v1",
        "unsupported: []",
    )
    profile_path = profile_dir / "board.yaml"
    profile_path.write_text(invalid_profile, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(profile_path)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "profile must require V2 and reject V1" in result.stderr


def test_board_profiles_reject_secret_like_instance_config(tmp_path):
    source = PROFILE_ROOT / "ha_voice_pe/board.yaml"
    profile_dir = tmp_path / "ha_voice_pe"
    profile_dir.mkdir()
    profile_path = profile_dir / "board.yaml"
    profile_path.write_text(
        source.read_text(encoding="utf-8") + "\nwifi_password: nope\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(profile_path)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "must not contain secret-like key" in result.stderr
