from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "firmware/tools/validate_board_profiles.py"
GENERATOR = REPO_ROOT / "firmware/tools/generate_board_profile_config.py"
PROFILE_ROOT = REPO_ROOT / "firmware/boards"
FIRMWARE_CMAKE = REPO_ROOT / "firmware/main/CMakeLists.txt"
FIRMWARE_BUILD_SCRIPT = REPO_ROOT / "firmware/build.sh"


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


def test_board_profiles_separate_hardware_dsp_vad_from_firmware_vad():
    validator = load_validator_module()
    profiles = {
        path.parent.name: validator.load_profile(path)
        for path in PROFILE_ROOT.glob("*/board.yaml")
    }

    assert profiles["ha_voice_pe"]["audio"]["input"]["dsp"]["vad"] is True
    assert profiles["esp_box_3"]["audio"]["input"]["dsp"]["vad"] is False
    assert profiles["waveshare_s3_touch_lcd_1_85c_box_v2"]["audio"]["input"]["dsp"]["vad"] is False
    assert profiles["waveshare_p4_wifi6_touch_lcd_7b"]["audio"]["input"]["dsp"]["vad"] is False

    for profile_name, profile in profiles.items():
        firmware_vad = profile["vad"]["firmware"]
        assert firmware_vad["available"] is True
        assert firmware_vad["algorithm"] == "energy_threshold"
        assert firmware_vad["input_source"] == "pcm_audio_frames"
        assert firmware_vad["configurable"] is True
        assert firmware_vad["frame_ms"] == 20
        assert firmware_vad["default_energy_threshold"] == 900
        assert firmware_vad["default_pause_ms"] == 190
        if profile["support_status"] == "active":
            assert firmware_vad["status"] == "active"
        else:
            assert firmware_vad["status"] == "planned"

    assert profiles["ha_voice_pe"]["vad"]["firmware"]["adaptive_noise_floor"] is True
    assert profiles["esp_box_3"]["vad"]["firmware"]["adaptive_noise_floor"] is False


def test_buildable_board_profiles_declare_existing_adapter_sources():
    validator = load_validator_module()
    profiles = {
        path.parent.name: validator.load_profile(path)
        for path in PROFILE_ROOT.glob("*/board.yaml")
    }

    assert profiles["esp_box_3"]["adapters"]["buildable"] is True
    assert profiles["ha_voice_pe"]["adapters"]["buildable"] is True
    assert profiles["waveshare_s3_touch_lcd_1_85c_box_v2"]["adapters"]["buildable"] is False
    assert profiles["waveshare_p4_wifi6_touch_lcd_7b"]["adapters"]["buildable"] is False
    assert profiles["esp_box_3"]["adapters"]["source_files"] == [
        "board/audio.cpp",
        "board/buttons.cpp",
        "board/display.cpp",
        "board/led_ring.cpp",
        "board/storage.cpp",
        "board/touch.cpp",
        "voice/tts_player.cpp",
    ]
    assert profiles["ha_voice_pe"]["adapters"]["source_files"] == [
        "board/audio_ha_voice_pe.cpp",
        "board/buttons_ha_voice_pe.cpp",
        "board/display_none.cpp",
        "board/led_ring_ha_voice_pe.cpp",
        "board/storage_nvs_only.cpp",
        "board/touch_none.cpp",
        "voice/tts_player_ha_voice_pe.cpp",
    ]


def test_board_profile_generator_renders_cmake_adapter_fragment(tmp_path):
    output = tmp_path / "board_profile_config.cmake"

    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--profile-root",
            str(PROFILE_ROOT),
            "--board-profile",
            "ha_voice_pe",
            "--output",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stdout == ""
    cmake = output.read_text(encoding="utf-8")
    assert 'set(HEXE_BOARD_PROFILE "ha_voice_pe")' in cmake
    assert 'set(HEXE_BOARD_PARTITION_SCHEMA "s3-16m-v1")' in cmake
    assert "set(HEXE_BOARD_ADAPTER_BUILDABLE TRUE)" in cmake
    assert "HEXE_BOARD_PROFILE_HA_VOICE_PE=1" in cmake
    assert '"board/audio_ha_voice_pe.cpp"' in cmake
    assert '"voice/tts_player_ha_voice_pe.cpp"' in cmake


def test_board_profile_generator_keeps_planned_profiles_non_buildable(tmp_path):
    output = tmp_path / "board_profile_config.cmake"

    subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--profile-root",
            str(PROFILE_ROOT),
            "--board-profile",
            "waveshare_s3_touch_lcd_1_85c_box_v2",
            "--output",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    cmake = output.read_text(encoding="utf-8")
    assert 'set(HEXE_BOARD_PROFILE "waveshare_s3_touch_lcd_1_85c_box_v2")' in cmake
    assert "set(HEXE_BOARD_ADAPTER_BUILDABLE FALSE)" in cmake
    assert "HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2=1" in cmake
    assert "set(HEXE_BOARD_SRCS\n)" in cmake


def test_firmware_cmake_uses_generated_board_profile_adapters():
    cmake = FIRMWARE_CMAKE.read_text(encoding="utf-8")

    assert "generate_board_profile_config.py" in cmake
    assert "board_profile_config.cmake" in cmake
    assert 'include("${HEXE_GENERATED_DIR}/board_profile_config.cmake")' in cmake
    assert "HEXE_BOARD_ADAPTER_BUILDABLE" in cmake
    assert 'set(HEXE_BOARD_SRCS\n  "board/audio.cpp"' not in cmake
    assert "elseif(HEXE_BOARD_PROFILE STREQUAL" not in cmake


def test_firmware_build_script_discovers_buildable_profiles_from_yaml():
    build_script = FIRMWARE_BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "BOARD_PROFILE_ROOT" in build_script
    assert "validate_board_profiles" in build_script
    assert "adapters.buildable" in build_script
    assert "buildable_profiles" in build_script
    assert "build_profile esp_box_3" not in build_script
    assert "build_profile ha_voice_pe" not in build_script


def test_board_profile_validator_rejects_missing_firmware_vad(tmp_path):
    source = PROFILE_ROOT / "ha_voice_pe/board.yaml"
    validator = load_validator_module()
    profile = validator.load_profile(source)
    profile.pop("vad")
    profile_dir = tmp_path / "ha_voice_pe"
    profile_dir.mkdir()
    profile_path = profile_dir / "board.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(profile_path)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "missing required keys: vad" in result.stderr


def test_board_profile_validator_rejects_buildable_profile_without_sources(tmp_path):
    source = PROFILE_ROOT / "ha_voice_pe/board.yaml"
    validator = load_validator_module()
    profile = validator.load_profile(source)
    profile["adapters"]["source_files"] = []
    profile_dir = tmp_path / "ha_voice_pe"
    profile_dir.mkdir()
    profile_path = profile_dir / "board.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(profile_path)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "buildable profiles must define adapter source files" in result.stderr


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
