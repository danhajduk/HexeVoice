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
YAML_TO_JSON = REPO_ROOT / "firmware/tools/yaml_to_json.py"
PROFILE_ROOT = REPO_ROOT / "firmware/boards"
FIRMWARE_ROOT_CMAKE = REPO_ROOT / "firmware/CMakeLists.txt"
FIRMWARE_CMAKE = REPO_ROOT / "firmware/components/endpoint_runtime/CMakeLists.txt"
FIRMWARE_ENDPOINT_MANIFEST = REPO_ROOT / "firmware/components/endpoint_runtime/idf_component.yml"
FIRMWARE_BUILD_SCRIPT = REPO_ROOT / "firmware/build.sh"
PARTITIONS_DIR = REPO_ROOT / "firmware/partitions"


def load_validator_module():
    spec = importlib.util.spec_from_file_location("validate_board_profiles", VALIDATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_yaml_to_json_module():
    spec = importlib.util.spec_from_file_location("yaml_to_json", YAML_TO_JSON)
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
        expected_energy_threshold = 300 if profile_name == "ha_voice_pe" else 900
        assert firmware_vad["default_energy_threshold"] == expected_energy_threshold
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

    assert profiles["esp_box_3"]["support_status"] == "unsupported"
    assert profiles["esp_box_3"]["adapters"]["buildable"] is False
    assert profiles["ha_voice_pe"]["adapters"]["buildable"] is True
    assert profiles["waveshare_s3_touch_lcd_1_85c_box_v2"]["adapters"]["buildable"] is True
    assert profiles["waveshare_p4_wifi6_touch_lcd_7b"]["adapters"]["buildable"] is True
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
    assert profiles["waveshare_p4_wifi6_touch_lcd_7b"]["adapters"]["source_files"] == [
        "board/audio.cpp",
        "board/buttons_boot_only.cpp",
        "board/display_waveshare_p4_7b.cpp",
        "board/led_ring.cpp",
        "board/storage_waveshare_p4_7b.cpp",
        "board/touch.cpp",
        "board/wifi.cpp",
        "voice/tts_player.cpp",
    ]
    ws185 = profiles["waveshare_s3_touch_lcd_1_85c_box_v2"]
    assert ws185["features"]["touch"] is True
    assert ws185["features"]["sd_card"] is True
    assert ws185["display"]["touch"] is True
    assert ws185["storage"]["sd_card"]["available"] is True
    assert ws185["capability_overrides"]["touchscreen"] is True
    assert ws185["capability_overrides"]["storage"] is True
    assert "button adapter is an explicit PLACEHOLDER" in ws185["adapters"]["notes"]


def test_buildable_board_profiles_declare_complete_wiring():
    validator = load_validator_module()
    profiles = {
        path.parent.name: validator.load_profile(path)
        for path in PROFILE_ROOT.glob("*/board.yaml")
    }

    assert profiles["esp_box_3"]["wiring"]["status"] == "complete"
    assert profiles["ha_voice_pe"]["wiring"]["status"] == "complete"
    assert profiles["waveshare_s3_touch_lcd_1_85c_box_v2"]["wiring"]["status"] == "complete"
    assert profiles["waveshare_p4_wifi6_touch_lcd_7b"]["wiring"]["status"] == "complete"

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


def test_p4_build_supports_explicit_silicon_profiles():
    build_script = FIRMWARE_BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'P4_SILICON_PROFILE="${HEXE_P4_SILICON_PROFILE:-rev1_3}"' in build_script
    assert "CONFIG_ESP32P4_SELECTS_REV_LESS_V3=y" in build_script
    assert "CONFIG_ESP32P4_REV_MIN_100=y" in build_script
    assert "CONFIG_ESP32P4_REV_MIN_300=y" in build_script
    assert "Unsupported HEXE_P4_SILICON_PROFILE" in build_script
    assert "P4 silicon profile changed to ${P4_SILICON_PROFILE}" in build_script


def test_p4_build_enables_esp32_c6_hosted_wifi():
    build_script = FIRMWARE_BUILD_SCRIPT.read_text(encoding="utf-8")
    manifest = (
        REPO_ROOT / "firmware/components/endpoint_runtime/idf_component.yml"
    ).read_text(encoding="utf-8")

    assert 'espressif/esp_wifi_remote:' in manifest
    assert 'version: "==1.6.4"' in manifest
    assert 'if: "idf_version <6.0"\n        version: "==0.14.5"' in manifest
    assert 'espressif/esp_hosted:' in manifest
    assert 'version: ">=2.11,<3.0"' in manifest
    assert 'if: "idf_version <6.0"\n        version: "==1.4.7"' in manifest
    assert "CONFIG_ESP_WIFI_REMOTE_LIBRARY_HOSTED=y" in build_script
    assert "CONFIG_SLAVE_IDF_TARGET_ESP32C6=y" in build_script
    assert "CONFIG_ESP_HOSTED_P4_DEV_BOARD_FUNC_BOARD=y" in build_script
    assert "CONFIG_ESP_HOSTED_MEMPOOL_PREFER_SPIRAM=y" in build_script
    assert "P4 hosted Wi-Fi requires ESP32-C6 remote transport" in build_script
    assert "rev1 P4 hosted Wi-Fi uses the Waveshare legacy stack" in build_script


def test_wifi_disconnect_logging_keeps_credentials_private():
    wifi_source = (
        REPO_ROOT / "firmware/components/endpoint_runtime/board/wifi.cpp"
    ).read_text(encoding="utf-8")

    assert '"Wi-Fi disconnected reason=%u, retrying"' in wifi_source
    assert "event->reason" in wifi_source


def test_p4_dma_component_is_only_required_on_idf_6():
    cmake = (
        REPO_ROOT / "firmware/components/endpoint_runtime/CMakeLists.txt"
    ).read_text(encoding="utf-8")

    assert "if(IDF_VERSION_MAJOR GREATER_EQUAL 6)" in cmake
    assert "list(APPEND HEXE_ENDPOINT_REQUIRES esp_driver_dma)" in cmake


def test_p4_display_falls_back_to_dcs_display_on_command():
    source = (
        REPO_ROOT / "firmware/components/endpoint_runtime/board/display_waveshare_p4_7b.cpp"
    ).read_text(encoding="utf-8")

    assert "display_on_result == ESP_ERR_NOT_SUPPORTED" in source
    assert "esp_lcd_panel_io_tx_param(g_panel_io, LCD_CMD_DISPON, nullptr, 0)" in source
    assert "ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(g_panel, true))" not in source


def test_p4_display_caches_rgb888_background_and_batches_flushes():
    source = (
        REPO_ROOT / "firmware/components/endpoint_runtime/board/display_waveshare_p4_7b.cpp"
    ).read_text(encoding="utf-8")

    assert "constexpr int kFlushRows = 120;" in source
    assert "heap_caps_malloc(kSdTestBackgroundBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)" in source
    assert "std::memcpy(g_flush_buffer, g_background_pixels + offset, expected_bytes)" in source
    assert "std::swap(g_background_pixels[offset], g_background_pixels[offset + 2])" in source
    assert "heap_caps_free(g_background_pixels);" in source


def test_p4_display_blends_header_status_sprites_from_sd():
    source = (
        REPO_ROOT / "firmware/components/endpoint_runtime/board/display_waveshare_p4_7b.cpp"
    ).read_text(encoding="utf-8")

    for sprite in ("wifi_on", "wifi_off", "node_connected", "asset_downloading"):
        assert f'"{sprite}"' in source
    assert "hexe::system::asset_sync_active()" in source
    assert "state.backend_connected" in source
    assert "sprite->alpha[source_pixel]" in source
    assert 'StatusSprite g_wifi_on_sprite{"wifi_on", kStatusSpriteSize, kStatusSpriteSize};' in source
    assert 'StatusSprite g_sidebar_sprite{"sidebar", kSidebarWidth, kSidebarHeight};' in source
    assert 'StatusSprite g_sidebar_right_sprite{"sidebar_right", kSidebarWidth, kSidebarHeight};' in source
    assert "constexpr int kSidebarButtonWidth = 72;" in source
    assert "constexpr int kSidebarButtonHeight = 56;" in source
    assert "draw_sidebars(state, g_frame_time_ms, screen);" in source
    assert "StatusFlag::kUiReady" in source
    assert "StatusFlag::kIdleReady" in source
    assert "state.wifi_connected && state.backend_connected && !state.ota_active" in source
    assert "draw_screen_layout(state, g_frame_time_ms, screen);" in source
    assert "draw_big_clock(state, now_ms, &element);" in source
    assert "draw_big_date(state, now_ms, &element);" in source
    assert "draw_activity_sprite(state, now_ms" in source
    for sprite in (
        "activity_listening",
        "activity_thinking",
        "activity_replay",
        "activity_timer",
        "button_timer",
        "button_weather",
        "button_config",
    ):
        assert f'"{sprite}"' in source
    assert "screen->sidebars_enabled" in source
    assert "screen->button_count" in source
    assert "constexpr int kStatusSpriteSize = 40;" in source
    assert 'constexpr char kStatusLayoutFilename[] = "status_layout.json";' in source
    assert 'cJSON_IsObject(icons_config) ? icons_config : root, "y", g_status_layout.y' in source
    assert "if (!layout.floating && status_icon_active(id, state))" in source
    assert "layout.x, g_status_layout.y" in source
    assert "int floating_x = g_status_layout.floating_x;" in source
    assert "floating_x += sprite->width + g_status_layout.floating_gap;" in source
    assert "g_status_layout_loaded = false;" in source
    assert "release_status_sprite(&g_wifi_on_sprite)" in source
    for animation in ("blink_dot", "running_dots", "pulse", "pulse_ring", "slide_in"):
        assert f'"{animation}"' in source
    assert "status_flag_value" in source
    assert "relative_pixels" in source
    assert "status_sprite_slide_offset" in source
    assert "animation_state->started_ms" in source
    assert "esp_timer_get_time() / 50000" in source


def test_p4_ui_config_compiles_items_presets_and_screens(tmp_path):
    source = (
        REPO_ROOT / "firmware/components/endpoint_runtime/board/display_waveshare_p4_7b.cpp"
    ).read_text(encoding="utf-8")
    directory = (
        REPO_ROOT
        / "firmware/assets/waveshare_p4_wifi6_touch_lcd_7b/assets/config"
    )
    yaml_to_json = load_yaml_to_json_module()
    index = yaml_to_json.load_yaml_with_includes(directory / "status_layout.yaml")
    assert index == {
        "schema_version": 3,
        "compiler": "p4_ui",
        "sources": [
            "animation_presets.yaml",
            "button_presets.yaml",
            "items.yaml",
            "screens_layout.yaml",
        ],
    }
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "firmware/tools/compile_p4_ui_config.py"),
            str(directory),
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    layout = {}
    for filename in (
        "chrome_layout.json",
        "status_icons.json",
        "activity_layout.json",
        "idle_layout.json",
        "screens_layout.json",
        "status_layout.json",
    ):
        compiled_payload = json.loads((tmp_path / filename).read_text(encoding="utf-8"))
        generated_payload = json.loads(
            (
                REPO_ROOT
                / "firmware/assets/waveshare_p4_wifi6_touch_lcd_7b/assets/sprite"
                / filename
            ).read_text(encoding="utf-8")
        )
        assert generated_payload == compiled_payload
        assert (tmp_path / filename).stat().st_size <= 8192
        layout.update(compiled_payload)

    screen_index = (directory / "screens_layout.yaml").read_text(encoding="utf-8")
    screen_files = sorted((directory / "screens").glob("*.yaml"))
    assert len(screen_files) == 15
    assert screen_index.count("!include screens/") == len(screen_files)
    assert [screen["id"] for screen in layout["screens"]] == [
        "updating",
        "updating_phase",
        "listening",
        "thinking",
        "playback",
        "replying",
        "timer_finished",
        "timer",
        "idle",
        "muted",
        "error",
        "backend_connecting",
        "wifi_connecting",
        "booting",
        "default",
    ]

    authored_items = yaml_to_json.load_yaml_with_includes(directory / "items.yaml")["items"]
    items_by_id = {item["id"]: item for item in authored_items}
    assert items_by_id["header_clock"]["data"] == "time"
    assert items_by_id["big_clock"]["data"] == "time"
    assert items_by_id["big_date"]["data"] == "date_long"
    assert items_by_id["timer_primary"]["data"] == "timer1"
    assert items_by_id["timer_upcoming"]["data"] == "timers_next"
    assert items_by_id["ota_progress"]["data"] == "ota_progress"

    icon_layout = layout["icons"]
    assert isinstance(icon_layout["y"], int)
    assert "y" not in icon_layout["floating"]
    assert all("y" not in icon for icon in icon_layout["items"])
    assert layout["sidebars"]["left"]["when"]["flag"] == "ui_ready"
    assert layout["sidebars"]["right"]["when"]["flag"] == "ui_ready"
    assert layout["sidebars"]["left"]["offset"]["x"] < 0
    assert layout["sidebars"]["right"]["offset"]["x"] > 0
    assert layout["idle_clock"]["enabled"] is True
    assert layout["idle_clock"]["date_format"] == "%A, %B %d %Y."
    assert layout["idle_clock"]["date"]["x"] == 512
    assert set(("sprite", "hours", "separator", "minutes", "date")) <= layout["idle_clock"].keys()
    assert layout["sidebar_buttons"] == {"x": 8, "y": 116, "gap": 20}
    screens = layout["screens"]
    assert screens[-1]["id"] == "default"
    assert [screen["id"] for screen in screens[:6]] == [
        "updating", "updating_phase", "listening", "thinking", "playback", "replying"
    ]
    assert {item["type"] for screen in screens for item in screen["elements"]} == {
        "clock", "big_clock", "big_date", "activity", "timer_primary", "timer_upcoming", "progress_bar"
    }
    assert all(isinstance(screen["sidebars"], bool) for screen in screens)
    assert all(isinstance(screen["buttons"], list) for screen in screens)
    assert next(screen for screen in screens if screen["id"] == "idle")["buttons"] == [
        "button_timer", "button_weather", "button_config"
    ]
    assert next(screen for screen in screens if screen["id"] == "updating")["buttons"] == []
    conditional_screens = [screen for screen in screens if screen["id"] != "default"]
    assert all(screen["conditions"]["match"] in {"all", "any"} for screen in conditional_screens)
    assert all(screen["conditions"]["items"] for screen in conditional_screens)
    timer_screen = next(screen for screen in screens if screen["id"] == "timer")
    assert [item["flag"] for item in timer_screen["conditions"]["items"]] == [
        "timer_active", "idle_ready"
    ]
    assert 'cJSON_GetObjectItem(screen_item, "conditions")' in source
    assert "hexe::ui_flag_value(condition.custom_flag)" in source
    assert 'cJSON_GetObjectItem(screen_item, "sidebars")' in source
    assert 'cJSON_GetObjectItem(screen_item, "buttons")' in source
    activity_items = layout["activity_sprites"]["items"]
    assert [item["id"] for item in activity_items] == ["listening", "thinking", "replay", "timer"]
    assert all(not item.get("animations") for item in activity_items)
    for element in ("sprite", "hours", "separator", "minutes", "date"):
        assert layout["idle_clock"][element]["animations"]
    animations = [animation for icon in icon_layout["items"] for animation in icon.get("animations", [])]
    animations.extend(
        animation
        for element in ("sprite", "hours", "separator", "minutes", "date")
        for animation in layout["idle_clock"][element].get("animations", [])
    )
    animations.extend(
        animation
        for screen in screens
        for element in screen["elements"]
        for animation in element.get("animations", [])
    )
    assert {animation["type"] for animation in animations} == {
        "blink_dot",
        "running_dots",
        "pulse",
        "pulse_ring",
        "slide_in",
    }
    for animation in animations:
        assert "flag" in animation["when"]
        for key in ("radius", "spacing"):
            if key in animation:
                assert 0 < animation[key] <= 1
        if "position" in animation:
            assert 0 <= animation["position"]["x"] <= 1
            assert 0 <= animation["position"]["y"] <= 1
        if "offset" in animation:
            assert -1 <= animation["offset"]["x"] <= 1
            assert -1 <= animation["offset"]["y"] <= 1


def test_yaml_layout_includes_are_relative_and_reject_cycles_and_escape(tmp_path):
    yaml_to_json = load_yaml_to_json_module()
    screens = tmp_path / "screens"
    screens.mkdir()
    (tmp_path / "layout.yaml").write_text(
        "screens:\n  - !include screens/idle.yaml\n",
        encoding="utf-8",
    )
    (screens / "idle.yaml").write_text("id: idle\n", encoding="utf-8")

    assert yaml_to_json.load_yaml_with_includes(tmp_path / "layout.yaml") == {
        "screens": [{"id": "idle"}]
    }

    (screens / "idle.yaml").write_text(
        "!include ../layout.yaml\n",
        encoding="utf-8",
    )
    try:
        yaml_to_json.load_yaml_with_includes(tmp_path / "layout.yaml")
    except yaml_to_json.yaml.YAMLError as exc:
        assert "cyclic YAML include" in str(exc)
    else:
        raise AssertionError("cyclic YAML include was accepted")

    outside = tmp_path.parent / f"{tmp_path.name}-outside.yaml"
    outside.write_text("id: outside\n", encoding="utf-8")
    (tmp_path / "layout.yaml").write_text(
        "screen: !include ../outside.yaml\n",
        encoding="utf-8",
    )
    try:
        yaml_to_json.load_yaml_with_includes(tmp_path / "layout.yaml")
    except yaml_to_json.yaml.YAMLError as exc:
        assert "include escapes source directory" in str(exc)
    else:
        raise AssertionError("escaping YAML include was accepted")


def test_firmware_asset_manifest_limit_covers_p4_sprite_library():
    source = (
        REPO_ROOT / "firmware/components/endpoint_runtime/system/asset_sync.cpp"
    ).read_text(encoding="utf-8")
    manifest = (
        REPO_ROOT
        / "firmware/assets/waveshare_p4_wifi6_touch_lcd_7b/assets/assets.json"
    )

    assert "constexpr size_t kMaxManifestBytes = 64 * 1024;" in source
    assert "MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT" in source
    assert "read_http_manifest" in source
    assert manifest.stat().st_size < 64 * 1024


def test_p4_header_clock_waits_for_sync_and_uses_centered_12_hour_time():
    source = (
        REPO_ROOT / "firmware/components/endpoint_runtime/board/display_waveshare_p4_7b.cpp"
    ).read_text(encoding="utf-8")

    assert '#include "system/clock.h"' in source
    assert "if (!hexe::system::clock_synced())" in source
    assert "hexe::system::current_local_time(&local)" in source
    assert '"%02d:%02d"' in source
    assert "local.tm_hour % 12" in source
    assert 'std::memcmp(data, "HXF1", 4)' in source
    assert 'json_integer(clock, "font_size", g_status_layout.clock.font_size, 12, 96)' in source
    assert "const int colon_center =" in source
    assert "scale_font_metric(" in source
    assert "(row * glyph->height) / scaled_height" in source
    assert "g_status_layout.clock.x_offset - colon_center" in source
    assert "const int baseline_y = 48 + g_status_layout.clock.y_offset;" in source
    assert "(local.tm_hour * 60) + local.tm_min + 1" in source
    assert "void draw_version_text(const char *build_id)" in source
    assert "const char *suffix = separator == nullptr ? build_id : separator + 1;" in source
    assert "suffix_end = std::strchr(suffix, '-')" in source
    assert "draw_version_text(build_id);" in source

    layout = json.loads(
        (
            REPO_ROOT
            / "firmware/assets/waveshare_p4_wifi6_touch_lcd_7b/assets/sprite/status_layout.json"
        ).read_text(encoding="utf-8")
    )
    chrome = json.loads(
        (
            REPO_ROOT
            / "firmware/assets/waveshare_p4_wifi6_touch_lcd_7b/assets/sprite/chrome_layout.json"
        ).read_text(encoding="utf-8")
    )
    assert "version" not in layout
    layout.update(chrome)
    assert layout["clock"]["font"] == "manrope/clock_42.hxf"
    assert 12 <= layout["clock"]["font_size"] <= 96
    assert {"color", "x_offset", "y_offset"} <= layout["clock"].keys()
    assert layout["version"]["font"] == "manrope/version_24.hxf"
    assert 8 <= layout["version"]["font_size"] <= 64
    assert {"color", "x", "y"} <= layout["version"].keys()
    version_font = (
        REPO_ROOT
        / "firmware/assets/waveshare_p4_wifi6_touch_lcd_7b/assets/font/manrope/version_24.hxf"
    )
    assert version_font.read_bytes().startswith(b"HXF1")


def test_p4_profile_uses_bsp_gt911_touch_adapter():
    source = (REPO_ROOT / "firmware/components/endpoint_runtime/board/touch.cpp").read_text(
        encoding="utf-8"
    )

    assert "bsp_touch_new(nullptr, &g_touch)" in source
    assert "esp_lcd_touch_read_data(g_touch)" in source
    assert "esp_lcd_touch_get_data(g_touch, &point, &point_count, 1)" in source
    assert "hexe::voice::tts_playback_active()" in source
    assert "display_activity_zone_contains(g_touch_start_x, g_touch_start_y)" in source
    assert 'hexe::voice::stop_playback("touch_activity")' in source


def test_endpoint_accepts_backend_ui_flags_for_screen_conditions():
    state_header = (REPO_ROOT / "firmware/components/endpoint_runtime/app_state.h").read_text(encoding="utf-8")
    backend = (REPO_ROOT / "firmware/components/endpoint_runtime/voice/backend_client.cpp").read_text(
        encoding="utf-8"
    )

    assert "constexpr size_t kMaxUiFlags = 16;" in state_header
    assert 'std::strcmp(type, "endpoint.ui.flags") == 0' in backend
    assert 'cJSON_GetObjectItem(payload, "flags")' in backend
    assert "hexe::set_ui_flag(flag->string, cJSON_IsTrue(flag))" in backend
    assert 'std::strcmp(type, "endpoint.ui.screen") == 0' in backend
    assert 'cJSON_GetObjectItem(payload, "screen_id")' in backend
    assert "std::clamp(duration_seconds, 1, 30) * 1000" in backend
    assert "hexe::trigger_ui_screen" in backend

    display = (
        REPO_ROOT / "firmware/components/endpoint_runtime/board/display_waveshare_p4_7b.cpp"
    ).read_text(encoding="utf-8")
    assert "hexe::active_ui_screen(forced_screen_id" in display
    assert "std::strcmp(g_status_layout.screens[index].id, forced_screen_id)" in display


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
    assert 'set(HEXE_BOARD_PARTITION_SCHEMA "s3-16m-recovery-single-model-v1")' in cmake
    assert 'set(HEXE_BOARD_STORAGE_MODEL_POLICY "embedded_fallback_then_internal_single_model_cache")' in cmake
    assert "set(HEXE_BOARD_FEATURE_DISPLAY FALSE)" in cmake
    assert "set(HEXE_BOARD_FEATURE_TOUCH FALSE)" in cmake
    assert "set(HEXE_BOARD_FEATURE_SD_CARD FALSE)" in cmake
    assert "set(HEXE_BOARD_FEATURE_USB_OTG FALSE)" in cmake
    assert "set(HEXE_BOARD_ADAPTER_BUILDABLE TRUE)" in cmake
    assert "HEXE_BOARD_PROFILE_HA_VOICE_PE=1" in cmake
    assert '"board/audio_ha_voice_pe.cpp"' in cmake
    assert '"voice/tts_player_ha_voice_pe.cpp"' in cmake

    header = header_output.read_text(encoding="utf-8")
    assert 'constexpr const char *kBoardProfile = "ha_voice_pe";' in header
    assert 'constexpr const char *kSoc = "esp32s3";' in header
    assert 'constexpr const char *kIdfTarget = "esp32s3";' in header
    assert 'constexpr const char *kPartitionSchema = "s3-16m-recovery-single-model-v1";' in header
    assert 'constexpr const char *kStorageModelPolicy = "embedded_fallback_then_internal_single_model_cache";' in header
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
    assert "set(HEXE_BOARD_FEATURE_DISPLAY TRUE)" in cmake
    assert "set(HEXE_BOARD_FEATURE_TOUCH TRUE)" in cmake
    assert "set(HEXE_BOARD_FEATURE_SD_CARD TRUE)" in cmake
    assert "set(HEXE_BOARD_FEATURE_USB_OTG FALSE)" in cmake
    assert 'set(HEXE_BOARD_STORAGE_MODEL_POLICY "internal_single_or_sd_model_set_no_embedded_fallback")' in cmake
    assert "HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2=1" in cmake
    assert '"board/waveshare_s3_1_85c_bus.cpp"' in cmake
    assert '"board/storage_waveshare_s3_1_85c_box_v2.cpp"' in cmake


def test_firmware_cmake_uses_generated_board_profile_adapters():
    root_cmake = FIRMWARE_ROOT_CMAKE.read_text(encoding="utf-8")
    cmake = FIRMWARE_CMAKE.read_text(encoding="utf-8")
    manifest = FIRMWARE_ENDPOINT_MANIFEST.read_text(encoding="utf-8")

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
    assert "HEXE_BOARD_FEATURE_DISPLAY" in cmake
    assert "HEXE_BOARD_FEATURE_TOUCH" in cmake
    assert "HEXE_BOARD_FEATURE_SD_CARD" in cmake
    assert "HEXE_BOARD_STORAGE_MODEL_POLICY" in cmake
    assert "list(APPEND HEXE_ENDPOINT_REQUIRES esp_lcd)" in cmake
    assert "list(APPEND HEXE_ENDPOINT_REQUIRES espressif__esp_lcd_touch)" in cmake
    assert "list(APPEND HEXE_ENDPOINT_REQUIRES espressif__esp_lcd_st77916)" in cmake
    assert "HEXE_BOARD_PROFILE STREQUAL \"waveshare_s3_touch_lcd_1_85c_box_v2\"" in cmake
    assert "esp-box-3_noglib" not in manifest
    assert "${HEXE_BOARD_PROFILE} == waveshare_s3_touch_lcd_1_85c_box_v2" in manifest
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
    assert '"HEXE_BOARD_PROFILE=${profile}"' in build_script
    assert "refresh_profile_sdkconfig_if_generated_defaults_changed" in build_script
    assert "CONFIG_BT_NIMBLE_ENABLED=y" in build_script
    assert 's3-8m-v1) echo "partitions/s3_8m_v1.csv"' in build_script
    assert 's3-8m-recovery-v1) echo "partitions/s3_8m_recovery_v1.csv"' in build_script
    assert 's3-16m-v1) echo "partitions/s3_16m_v1.csv"' in build_script
    assert 's3-16m-recovery-v1) echo "partitions/s3_16m_recovery_v1.csv"' in build_script
    assert (
        's3-16m-recovery-single-model-v1) echo "partitions/s3_16m_recovery_single_model_v1.csv"'
        in build_script
    )
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
        "s3_16m_recovery_single_model_v1.csv": (
            "factory,    app,  factory, ,         2M,",
            "ota_0,      app,  ota_0,   ,         4M,",
            "ota_1,      app,  ota_1,   ,         4M,",
            "model,      data, spiffs,  ,         1M,",
            "storage,    data, spiffs,  ,         4032K,",
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


def test_waveshare_p4_build_stack_is_pinned():
    validator = load_validator_module()
    profile = validator.load_profile(PROFILE_ROOT / "waveshare_p4_wifi6_touch_lcd_7b/board.yaml")
    endpoint_manifest = FIRMWARE_ENDPOINT_MANIFEST.read_text(encoding="utf-8")
    recovery_manifest = (REPO_ROOT / "firmware/components/recovery_runtime/idf_component.yml").read_text(encoding="utf-8")
    root_cmake = FIRMWARE_ROOT_CMAKE.read_text(encoding="utf-8")

    assert profile["build"]["required_idf_version"] == "5.5.4"
    for manifest in (endpoint_manifest, recovery_manifest):
        assert 'version: "==0.14.5"' in manifest
        assert 'version: "==1.4.7"' in manifest
    assert "HEXE_REQUIRED_IDF_VERSION" in root_cmake
    assert "idf_build_set_property(DEPENDENCIES_LOCK" in root_cmake


def test_waveshare_p4_build_rejects_wrong_idf_before_build(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_idf = fake_bin / "idf.py"
    build_marker = tmp_path / "build-ran"
    fake_idf.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"--version\" ]]; then echo 'ESP-IDF v6.1.0'; exit 0; fi\n"
        f"touch {build_marker}\n",
        encoding="utf-8",
    )
    fake_idf.chmod(0o755)

    result = subprocess.run(
        [str(FIRMWARE_BUILD_SCRIPT), "build"],
        cwd=REPO_ROOT / "firmware",
        env={
            "HOME": str(tmp_path),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "IDF_PATH": str(tmp_path / "wrong-idf"),
            "HEXE_BOARD_PROFILE": "waveshare_p4_wifi6_touch_lcd_7b",
            "EXPORT_AFTER_BUILD": "0",
            "ALLOW_DIRTY_FIRMWARE_BUILD": "1",
        },
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "requires ESP-IDF 5.5.4; active ESP-IDF is 6.1.0" in result.stderr
    assert not build_marker.exists()
