from __future__ import annotations

import json
import re
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT_DIR / "docs" / "firmware-validation-matrix.json"
MATRIX_DOC_PATH = ROOT_DIR / "docs" / "firmware-validation-matrix.md"
FIRMWARE_BUILD_SCRIPT = ROOT_DIR / "firmware" / "build.sh"


def _matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_firmware_validation_matrix_covers_supported_build_profiles():
    matrix = _matrix()
    build_source = FIRMWARE_BUILD_SCRIPT.read_text(encoding="utf-8")
    build_profiles = set(re.findall(r"build_profile (esp_box_3|ha_voice_pe)", build_source))
    matrix_profiles = {profile["id"] for profile in matrix["profiles"]}

    assert matrix_profiles == build_profiles
    assert matrix["unsupported_profiles"]["operator_behavior"]
    assert "Unsupported HEXE_BOARD_PROFILE" in build_source


def test_firmware_validation_matrix_has_required_categories_and_actions():
    matrix = _matrix()
    required_categories = set(matrix["required_categories"])
    allowed_states = {"automated", "partial", "manual", "unsupported"}

    assert required_categories == {
        "audio_streaming",
        "wake_acceptance",
        "tts_playback",
        "display_state",
        "ota_media",
        "mute_volume",
        "reconnect_behavior",
    }

    for profile in matrix["profiles"]:
        assert profile["support_state"] == "supported"
        assert profile["validation_state"] in allowed_states
        assert profile["operator_summary"]
        assert profile["automated_checks"]
        assert set(profile["checks"]) == required_categories
        for category, check in profile["checks"].items():
            assert category in required_categories
            assert check["state"] in allowed_states
            assert check["automated"], f"{profile['id']} {category} needs automated coverage pointers"
            assert check["manual"], f"{profile['id']} {category} needs manual field checks"


def test_firmware_validation_matrix_doc_mentions_profiles_and_release_gate():
    matrix = _matrix()
    doc = MATRIX_DOC_PATH.read_text(encoding="utf-8")

    for profile in matrix["profiles"]:
        assert profile["id"] in doc
        assert profile["display_name"] in doc
    assert "tests/test_firmware_validation_matrix.py" in doc
    assert "./build.sh" in doc
    assert "validation_state: \"partial\"" in doc
