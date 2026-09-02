from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "firmware/tools/validate_board_profiles.py"
GENERATOR = REPO_ROOT / "firmware/tools/generate_board_profile_config.py"
SCAFFOLD = REPO_ROOT / "firmware/tools/create_board_profile.py"
PARTITION_VALIDATOR = REPO_ROOT / "firmware/tools/validate_partition_schema.py"
PROFILE_ROOT = REPO_ROOT / "firmware/boards"
FIRMWARE_ROOT_CMAKE = REPO_ROOT / "firmware/CMakeLists.txt"
FIRMWARE_CMAKE = REPO_ROOT / "firmware/components/endpoint_runtime/CMakeLists.txt"
FIRMWARE_BUILD_SCRIPT = REPO_ROOT / "firmware/build.sh"
PARTITIONS_DIR = REPO_ROOT / "firmware/partitions"


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


def test_retired_xvf3800_xiao_s3_profile_is_not_committed():
    assert not (PROFILE_ROOT / "xvf3800_xiao_s3").exists()


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
    assert profiles["waveshare_s3_touch_lcd_1_85c_box_v2"]["adapters"]["buildable"] is True
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
    assert profiles["waveshare_s3_touch_lcd_1_85c_box_v2"]["adapters"]["source_files"] == [
        "board/waveshare_s3_1_85c_bus.cpp",
        "board/audio_waveshare_s3_1_85c_box_v2.cpp",
        "board/buttons_waveshare_s3_1_85c_box_v2.cpp",
        "board/display_waveshare_s3_1_85c_box_v2.cpp",
        "board/led_ring.cpp",
        "board/storage_waveshare_s3_1_85c_box_v2.cpp",
        "board/touch_waveshare_s3_1_85c_box_v2.cpp",
        "voice/tts_player_waveshare_s3_1_85c_box_v2.cpp",
    ]
    assert "PLACEHOLDER" in profiles["waveshare_s3_touch_lcd_1_85c_box_v2"]["adapters"]["notes"]


def test_buildable_board_profiles_declare_complete_wiring():
    validator = load_validator_module()
    profiles = {
        path.parent.name: validator.load_profile(path)
        for path in PROFILE_ROOT.glob("*/board.yaml")
    }

    assert profiles["esp_box_3"]["wiring"]["status"] == "complete"
    assert profiles["ha_voice_pe"]["wiring"]["status"] == "complete"
    assert profiles["waveshare_s3_touch_lcd_1_85c_box_v2"]["wiring"]["status"] == "complete"
    assert profiles["waveshare_p4_wifi6_touch_lcd_7b"]["wiring"]["status"] == "partial"

    pe_wiring = profiles["ha_voice_pe"]["wiring"]
    pe_i2c = {bus["name"]: bus for bus in pe_wiring["i2c_buses"]}
    pe_i2s = {bus["name"]: bus for bus in pe_wiring["i2s_buses"]}
    pe_gpios = {gpio["name"]: gpio for gpio in pe_wiring["gpios"]}
    pe_leds = {strip["name"]: strip for strip in pe_wiring["led_strips"]}

    assert pe_i2c["audio_control"]["sda"] == 5
    assert pe_i2c["audio_control"]["scl"] == 6
    assert pe_i2c["audio_control"]["devices"] == [
        {"name": "voice_kit", "address": 66},
        {"name": "speaker_codec", "address": 24},
    ]
    assert pe_i2s["microphone"]["bclk"] == 13
    assert pe_i2s["microphone"]["lrclk"] == 14
    assert pe_i2s["microphone"]["din"] == 15
    assert pe_i2s["speaker"]["bclk"] == 8
    assert pe_i2s["speaker"]["lrclk"] == 7
    assert pe_i2s["speaker"]["dout"] == 10
    assert pe_gpios["center_button"]["gpio"] == 0
    assert pe_gpios["hardware_mute"]["gpio"] == 3
    assert pe_leds["led_ring"]["data"] == 21
    assert pe_leds["led_ring"]["power"] == 45
    assert pe_leds["led_ring"]["pixel_count"] == 12

    ws_wiring = profiles["waveshare_s3_touch_lcd_1_85c_box_v2"]["wiring"]
    ws_gpios = {gpio["name"]: gpio for gpio in ws_wiring["gpios"]}
    ws_i2c = {bus["name"]: bus for bus in ws_wiring["i2c_buses"]}
    ws_i2s = {bus["name"]: bus for bus in ws_wiring["i2s_buses"]}
    ws_spi = {bus["name"]: bus for bus in ws_wiring["spi_buses"]}

    assert ws_gpios["touch_interrupt"]["gpio"] == 4
    assert ws_gpios["speaker_pa"]["gpio"] == 15
    assert ws_gpios["boot_button"]["gpio"] == 0
    assert ws_gpios["sdmmc_clk"]["gpio"] == 14
    assert ws_gpios["sdmmc_cmd"]["gpio"] == 17
    assert ws_gpios["sdmmc_d0"]["gpio"] == 16
    assert ws_i2c["peripheral_control"]["sda"] == 11
    assert ws_i2c["peripheral_control"]["scl"] == 10
    assert ws_i2c["peripheral_control"]["devices"] == [
        {"name": "touch_controller", "address": 21, "notes": "CST816S touch controller."},
        {"name": "io_expander", "address": 32, "notes": "TCA9554 reset expander; EXIO1=touch reset, EXIO2=LCD reset."},
        {"name": "rtc", "address": 81, "notes": "PCF85063-style RTC footprint used by Waveshare examples."},
        {"name": "microphone_codec", "address": 64, "notes": "ES7210 7-bit address; esp_codec_dev uses the matching shifted default internally."},
        {"name": "speaker_codec", "address": 24, "notes": "ES8311 7-bit address."},
    ]
    assert ws_i2s["audio"]["mclk"] == 2
    assert ws_i2s["audio"]["bclk"] == 48
    assert ws_i2s["audio"]["lrclk"] == 38
    assert ws_i2s["audio"]["din"] == 39
    assert ws_i2s["audio"]["dout"] == 47
    assert ws_spi["display"]["mode"] == "qspi"
    assert ws_spi["display"]["clk"] == 40
    assert ws_spi["display"]["data0"] == 46
    assert ws_spi["display"]["data1"] == 45
    assert ws_spi["display"]["data2"] == 42
    assert ws_spi["display"]["data3"] == 41
    assert ws_spi["display"]["cs"] == 21
    assert ws_spi["display"]["te"] == 18

def test_board_profile_generator_renders_cmake_adapter_fragment(tmp_path):
    output = tmp_path / "board_profile_config.cmake"
    header_output = tmp_path / "board_profile_pins.h"

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
            "--header-output",
            str(header_output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stdout == ""
    cmake = output.read_text(encoding="utf-8")
    assert 'set(HEXE_BOARD_PROFILE "ha_voice_pe")' in cmake
    assert 'set(HEXE_BOARD_IDF_TARGET "esp32s3")' in cmake
    assert 'set(HEXE_BOARD_SOC "esp32s3")' in cmake
    assert 'set(HEXE_BOARD_PARTITION_SCHEMA "s3-16m-recovery-v1")' in cmake
    assert "set(HEXE_BOARD_ADAPTER_BUILDABLE TRUE)" in cmake
    assert "HEXE_BOARD_PROFILE_HA_VOICE_PE=1" in cmake
    assert '"board/audio_ha_voice_pe.cpp"' in cmake
    assert '"voice/tts_player_ha_voice_pe.cpp"' in cmake

    header = header_output.read_text(encoding="utf-8")
    assert 'constexpr const char *kBoardProfile = "ha_voice_pe";' in header
    assert 'constexpr const char *kSoc = "esp32s3";' in header
    assert 'constexpr const char *kIdfTarget = "esp32s3";' in header
    assert 'constexpr const char *kPartitionSchema = "s3-16m-recovery-v1";' in header
    assert 'constexpr const char *kAppSlotSize = "4MiB";' in header
    assert 'constexpr const char *kFlashSize = "16MiB";' in header
    assert 'constexpr const char *kPsramSize = "8MiB";' in header
    assert "constexpr bool kBleOnboardingSupported = true;" in header
    assert 'constexpr const char *kBleOnboardingTransport = "native";' in header
    assert 'constexpr const char *kBleOnboardingStatus = "active";' in header
    assert "constexpr int kAudioControlSda = 5;" in header
    assert "constexpr int kAudioControlScl = 6;" in header
    assert "constexpr int kAudioControlVoiceKitAddress = 66;" in header
    assert "constexpr int kAudioControlSpeakerCodecAddress = 24;" in header
    assert "constexpr int kMicrophoneBclk = 13;" in header
    assert "constexpr int kSpeakerDout = 10;" in header
    assert "constexpr int kVoicePeLedCount = kLedRingPixelCount;" in header


def test_board_profile_generator_renders_waveshare_buildable_scaffold(tmp_path):
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
    assert "set(HEXE_BOARD_ADAPTER_BUILDABLE TRUE)" in cmake
    assert "HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2=1" in cmake
    assert '"board/waveshare_s3_1_85c_bus.cpp"' in cmake
    assert '"board/storage_waveshare_s3_1_85c_box_v2.cpp"' in cmake


def test_firmware_cmake_uses_generated_board_profile_adapters():
    root_cmake = FIRMWARE_ROOT_CMAKE.read_text(encoding="utf-8")
    cmake = FIRMWARE_CMAKE.read_text(encoding="utf-8")

    assert "HEXE_FIRMWARE_APP" in root_cmake
    assert 'set(HEXE_FIRMWARE_APP "endpoint")' in root_cmake
    assert 'apps/${HEXE_FIRMWARE_APP}/main' in root_cmake
    assert "HEXE_FIRMWARE_RUNTIME_COMPONENT endpoint_runtime" in root_cmake
    assert "HEXE_FIRMWARE_RUNTIME_COMPONENT recovery_runtime" in root_cmake
    assert "COMPONENT_DIRS" in root_cmake
    assert "generate_board_profile_config.py" in cmake
    assert "--header-output" in cmake
    assert "board_profile_pins.h" in cmake
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
    assert "partition_csv_for_schema" in build_script
    assert "flash_size_kconfig_symbol" in build_script
    assert "flash_size_kconfig_value" in build_script
    assert "refresh_profile_sdkconfig_if_generated_defaults_changed" in build_script
    assert "CONFIG_BT_NIMBLE_ENABLED=y" in build_script
    assert 's3-8m-v1) echo "partitions/s3_8m_v1.csv"' in build_script
    assert 's3-8m-recovery-v1) echo "partitions/s3_8m_recovery_v1.csv"' in build_script
    assert 's3-16m-v1) echo "partitions/s3_16m_v1.csv"' in build_script
    assert 's3-16m-recovery-v1) echo "partitions/s3_16m_recovery_v1.csv"' in build_script
    assert 'p4-32m-v1) echo "partitions/p4_32m_v1.csv"' in build_script
    assert '8MiB|8MB|8M) echo "CONFIG_ESPTOOLPY_FLASHSIZE_8MB"' in build_script
    assert '16MiB|16MB|16M) echo "CONFIG_ESPTOOLPY_FLASHSIZE_16MB"' in build_script
    assert '32MiB|32MB|32M) echo "CONFIG_ESPTOOLPY_FLASHSIZE_32MB"' in build_script
    assert "SDKCONFIG_DEFAULTS" in build_script
    assert "build.partition_schema" in build_script
    assert "hardware.flash_size" in build_script
    assert 'CONFIG_ESPTOOLPY_FLASHSIZE="' in build_script
    assert 'rm -f "${sdkconfig_path}"' in build_script
    assert "build.idf_target" in build_script
    assert "build_profile esp_box_3" not in build_script
    assert "build_profile ha_voice_pe" not in build_script


def test_named_partition_schema_files_exist_and_cover_profile_classes():
    schemas = {
        "s3_8m_v1.csv": ("ota_0,      app,  ota_0,   ,         2560K,", "ota_1,      app,  ota_1,   ,         2560K,"),
        "s3_8m_recovery_v1.csv": (
            "factory,    app,  factory, ,         2M,",
            "ota_0,      app,  ota_0,   ,         2560K,",
            "ota_1,      app,  ota_1,   ,         2560K,",
        ),
        "s3_16m_v1.csv": ("ota_0,      app,  ota_0,   0x10000,  4M,", "ota_1,      app,  ota_1,   ,         4M,"),
        "s3_16m_recovery_v1.csv": (
            "factory,    app,  factory, ,         2M,",
            "ota_0,      app,  ota_0,   ,         4M,",
            "ota_1,      app,  ota_1,   ,         4M,",
        ),
        "p4_32m_v1.csv": ("ota_0,      app,  ota_0,   ,         8M,", "ota_1,      app,  ota_1,   ,         8M,"),
    }

    for filename, expected_lines in schemas.items():
        source = (PARTITIONS_DIR / filename).read_text(encoding="utf-8")
        assert "nvs,        data, nvs,     0x9000,   16K," in source
        assert "otadata,    data, ota,     0xd000,   8K," in source
        for expected_line in expected_lines:
            assert expected_line in source


def test_partition_schema_validator_accepts_committed_board_profiles():
    result = subprocess.run(
        [
            sys.executable,
            str(PARTITION_VALIDATOR),
            "--profile-root",
            str(PROFILE_ROOT),
            "--partition-root",
            str(PARTITIONS_DIR),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stdout.strip() == "Validated partition schema for 4 board profile(s)."


def test_recovery_profiles_require_recovery_partition_schema(tmp_path):
    source = PROFILE_ROOT / "ha_voice_pe/board.yaml"
    validator = load_validator_module()
    profile = validator.load_profile(source)
    profile["build"]["partition_schema"] = "s3-16m-v1"
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
    assert "recovery_app profiles must use a recovery partition schema" in result.stderr


def test_partition_schema_validator_warns_and_rejects_app_size_gates(tmp_path):
    warning_binary = tmp_path / "warning.bin"
    warning_binary.write_bytes(b"")
    warning_binary.open("r+b").truncate((3 * 1024 * 1024) + 1)

    warning = subprocess.run(
        [
            sys.executable,
            str(PARTITION_VALIDATOR),
            "--profile-root",
            str(PROFILE_ROOT),
            "--partition-root",
            str(PARTITIONS_DIR),
            "--board-profile",
            "ha_voice_pe",
            "--app-binary",
            str(warning_binary),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "75 percent slot warning" in warning.stderr

    rejected_binary = tmp_path / "rejected.bin"
    rejected_binary.write_bytes(b"")
    rejected_binary.open("r+b").truncate(int(4 * 1024 * 1024 * 0.85))

    rejected = subprocess.run(
        [
            sys.executable,
            str(PARTITION_VALIDATOR),
            "--profile-root",
            str(PROFILE_ROOT),
            "--partition-root",
            str(PARTITIONS_DIR),
            "--board-profile",
            "ha_voice_pe",
            "--app-binary",
            str(rejected_binary),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert rejected.returncode == 1
    assert "exceeds 85 percent slot gate" in rejected.stderr


def test_board_profile_scaffold_dry_run_creates_valid_planned_profile(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD),
            "--profile",
            "test_s3_voice_display",
            "--display-name",
            "Test S3 Voice Display",
            "--vendor",
            "TestVendor",
            "--model",
            "TestBoard",
            "--source-url",
            "https://example.com/test-board",
            "--with-display",
            "--with-touch",
            "--with-sd-card",
            "--display-size-inches",
            "2.8",
            "--display-width-px",
            "320",
            "--display-height-px",
            "240",
            "--dry-run",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    profile_dir = tmp_path / "test_s3_voice_display"
    profile_dir.mkdir()
    profile_path = profile_dir / "board.yaml"
    profile_path.write_text(result.stdout, encoding="utf-8")
    validation = subprocess.run(
        [sys.executable, str(VALIDATOR), str(profile_path)],
        check=True,
        text=True,
        capture_output=True,
    )

    assert validation.stdout.strip() == "Validated 1 board profile(s)."
    assert "support_status: planned" in result.stdout
    assert "wiring:" in result.stdout
    assert "status: partial" in result.stdout
    assert "buildable: false" in result.stdout


def test_board_profile_scaffold_writes_and_refuses_existing_profile(tmp_path):
    command = [
        sys.executable,
        str(SCAFFOLD),
        "--profile",
        "test_minimal_voice",
        "--display-name",
        "Test Minimal Voice",
        "--vendor",
        "TestVendor",
        "--model",
        "TestBoard",
        "--source-url",
        "https://example.com/test-board",
        "--output-root",
        str(tmp_path),
    ]

    first = subprocess.run(command, check=True, text=True, capture_output=True)
    second = subprocess.run(command, check=False, text=True, capture_output=True)

    assert first.stdout.strip() == f"Wrote {tmp_path / 'test_minimal_voice/board.yaml'}"
    assert second.returncode == 1
    assert "Refusing to overwrite existing board profile" in second.stderr


def test_board_profile_scaffold_requires_display_dimensions():
    result = subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD),
            "--profile",
            "bad_display_profile",
            "--display-name",
            "Bad Display Profile",
            "--vendor",
            "TestVendor",
            "--model",
            "TestBoard",
            "--source-url",
            "https://example.com/test-board",
            "--with-display",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "--with-display requires" in result.stderr


def test_board_profile_scaffold_uses_p4_defaults(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD),
            "--profile",
            "test_p4_voice_display",
            "--display-name",
            "Test P4 Voice Display",
            "--vendor",
            "TestVendor",
            "--model",
            "TestP4",
            "--source-url",
            "https://example.com/test-p4-board",
            "--soc",
            "esp32p4",
            "--coprocessor",
            "esp32c6",
            "--with-display",
            "--with-touch",
            "--display-size-inches",
            "7",
            "--display-width-px",
            "1024",
            "--display-height-px",
            "600",
            "--dry-run",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    profile_dir = tmp_path / "test_p4_voice_display"
    profile_dir.mkdir()
    profile_path = profile_dir / "board.yaml"
    profile_path.write_text(result.stdout, encoding="utf-8")
    subprocess.run([sys.executable, str(VALIDATOR), str(profile_path)], check=True, text=True, capture_output=True)

    assert "idf_target: esp32p4" in result.stdout
    assert "partition_schema: p4-32m-v1" in result.stdout
    assert "app_slot_size: 8MiB" in result.stdout
    assert "flash_size: 32MiB" in result.stdout
    assert "psram_size: 32MiB" in result.stdout
    assert "coprocessor: esp32c6" in result.stdout


def test_board_profile_scaffold_uses_s3_8m_recovery_defaults(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD),
            "--profile",
            "test_s3_voice_8m",
            "--display-name",
            "Test S3 Voice 8M",
            "--vendor",
            "TestVendor",
            "--model",
            "TestS3",
            "--source-url",
            "https://example.com/test-s3-board",
            "--flash-size",
            "8MiB",
            "--dry-run",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    profile_dir = tmp_path / "test_s3_voice_8m"
    profile_dir.mkdir()
    profile_path = profile_dir / "board.yaml"
    profile_path.write_text(result.stdout, encoding="utf-8")
    subprocess.run([sys.executable, str(VALIDATOR), str(profile_path)], check=True, text=True, capture_output=True)

    assert "partition_schema: s3-8m-recovery-v1" in result.stdout
    assert "app_slot_size: 2560K" in result.stdout
    assert "flash_size: 8MiB" in result.stdout


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


def test_board_profile_validator_rejects_buildable_profile_without_complete_wiring(tmp_path):
    source = PROFILE_ROOT / "ha_voice_pe/board.yaml"
    validator = load_validator_module()
    profile = validator.load_profile(source)
    profile["wiring"]["status"] = "partial"
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
    assert "buildable profiles must have complete wiring" in result.stderr


def test_board_profile_validator_rejects_buildable_profile_with_missing_pin(tmp_path):
    source = PROFILE_ROOT / "ha_voice_pe/board.yaml"
    validator = load_validator_module()
    profile = validator.load_profile(source)
    profile["wiring"]["i2c_buses"][0]["sda"] = None
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
    assert "incomplete pin mapping" in result.stderr


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
