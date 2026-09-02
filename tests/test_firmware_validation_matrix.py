from __future__ import annotations

import json
import importlib.util
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT_DIR / "docs" / "firmware-validation-matrix.json"
MATRIX_DOC_PATH = ROOT_DIR / "docs" / "firmware-validation-matrix.md"
FIRMWARE_BUILD_SCRIPT = ROOT_DIR / "firmware" / "build.sh"
BOARD_PROFILE_VALIDATOR = ROOT_DIR / "firmware" / "tools" / "validate_board_profiles.py"
BOARD_PROFILE_ROOT = ROOT_DIR / "firmware" / "boards"


def _matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _board_profile(profile_id: str) -> dict:
    spec = importlib.util.spec_from_file_location(
        "validate_board_profiles",
        BOARD_PROFILE_VALIDATOR,
    )
    assert spec is not None
    assert spec.loader is not None
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    path = BOARD_PROFILE_ROOT / profile_id / "board.yaml"
    profile = validator.load_profile(path)
    validator.validate_profile(profile, path)
    return profile


def test_firmware_validation_matrix_covers_supported_build_profiles():
    matrix = _matrix()
    build_source = FIRMWARE_BUILD_SCRIPT.read_text(encoding="utf-8")
    matrix_profiles = {profile["id"] for profile in matrix["profiles"]}

    assert matrix_profiles == {"esp_box_3", "ha_voice_pe"}
    for profile_id in matrix_profiles:
        profile = _board_profile(profile_id)
        assert profile["adapters"]["buildable"] is True
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


def test_firmware_validation_matrix_covers_roadmap_stress_cases():
    matrix = _matrix()
    stress_matrix = matrix["roadmap_stress_matrix"]
    categories = {category["id"]: category for category in stress_matrix["categories"]}

    assert stress_matrix["release_gate"]
    assert stress_matrix["evidence_policy"]
    assert set(categories) == {
        "ota_recovery",
        "model_bundle",
        "storage_corruption",
        "hardware_behavior",
        "long_duration",
    }

    required_case_ids = {
        "ota_normal_success",
        "ota_corrupt_image_rejection",
        "ota_wrong_board_rejection",
        "ota_signature_failure_rejection",
        "ota_power_loss_download",
        "ota_power_loss_flash_write",
        "ota_crash_before_validation",
        "ota_network_unavailable_during_validation",
        "ota_both_slots_invalid_recovery_boots",
        "model_bundle_valid_update",
        "model_bundle_corrupt_rejection",
        "model_bundle_wrong_soc_rejection",
        "model_bundle_power_loss_download",
        "model_bundle_power_loss_pointer_switch",
        "model_bundle_failure_after_pointer_switch",
        "model_bundle_app_rollback_compatibility",
        "model_bundle_embedded_fallback",
        "corrupt_nvs_recovery",
        "corrupt_model_partition",
        "corrupt_sd_filesystem",
        "sd_removal_runtime",
        "psram_sustained_allocation",
        "microphone_channel_validation",
        "speaker_output",
        "mute_button_input",
        "status_display_led",
        "wifi_radio_reconnect",
        "wake_during_network_activity",
        "wake_during_playback",
        "far_field_behavior",
        "long_duration_audio_streaming",
        "long_duration_network_stress",
        "long_duration_thermal_power",
    }
    actual_case_ids = {
        case["id"]
        for category in stress_matrix["categories"]
        for case in category["required_cases"]
    }

    assert actual_case_ids == required_case_ids
    for category in stress_matrix["categories"]:
        assert category["title"]
        assert category["applicability"]
        assert category["required_cases"]
        for case in category["required_cases"]:
            assert case["title"]
            assert case["evidence"]


def test_firmware_validation_matrix_doc_mentions_profiles_and_release_gate():
    matrix = _matrix()
    doc = MATRIX_DOC_PATH.read_text(encoding="utf-8")

    for profile in matrix["profiles"]:
        assert profile["id"] in doc
        assert profile["display_name"] in doc
    assert "tests/test_firmware_validation_matrix.py" in doc
    assert "./build.sh" in doc
    assert "roadmap_stress_matrix" in doc
    assert "power loss during download or flash write" in doc
    assert "wake during playback" in doc
    assert "far-field pickup" in doc
    assert "validation_state: \"partial\"" in doc
