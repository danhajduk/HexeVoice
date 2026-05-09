from pathlib import Path


FIRMWARE_BACKEND_CLIENT = Path("firmware/main/voice/backend_client.cpp")
FIRMWARE_CMAKE = Path("firmware/main/CMakeLists.txt")
FIRMWARE_AUDIO = Path("firmware/main/board/audio.cpp")
FIRMWARE_AUDIO_HA_VOICE_PE = Path("firmware/main/board/audio_ha_voice_pe.cpp")
FIRMWARE_BUTTONS_HA_VOICE_PE = Path("firmware/main/board/buttons_ha_voice_pe.cpp")
FIRMWARE_DISPLAY = Path("firmware/main/board/display.cpp")
FIRMWARE_DISPLAY_NONE = Path("firmware/main/board/display_none.cpp")
FIRMWARE_STORAGE = Path("firmware/main/board/storage.cpp")
FIRMWARE_STORAGE_NVS_ONLY = Path("firmware/main/board/storage_nvs_only.cpp")
FIRMWARE_TTS_PLAYER = Path("firmware/main/voice/tts_player.cpp")
FIRMWARE_TTS_PLAYER_NOOP = Path("firmware/main/voice/tts_player_noop.cpp")
FIRMWARE_CONVERT_SPRITE = Path("firmware/tools/convert-sprite.sh")


def test_firmware_voice_events_emit_full_v1_envelope():
    source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert "kVoiceEventSchemaVersion" in source
    assert "append_event_header" in source
    assert '"event_id"' in source
    assert '"schema_version"' in source
    assert '"timestamp"' in source
    assert "session.start" in source
    assert "audio.chunk" in source
    assert "audio.end" in source
    assert "session.cancel" in source
    assert "command.ack" in source
    assert "command.error" in source


def test_firmware_backend_commands_acknowledge_receipt_with_ok():
    source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert "acknowledge_command_received(type, payload);" in source
    assert 'std::strcmp(event_type, "ota.update") == 0' in source
    assert 'std::strncmp(event_type, "endpoint.", 9) == 0' in source
    assert 'send_command_ack(payload_request_id(payload), command_type_for_event(event_type), "accepted", "OK");' in source
    assert 'return "endpoint.volume.set";' in source


def test_firmware_vad_keeps_listening_window_after_wake_word():
    source = FIRMWARE_AUDIO.read_text()

    assert "kVadSilenceHoldMs = 2500" in source
    assert 'finish_audio_stream("vad_silence")' in source


def test_firmware_heartbeat_reports_network_metadata():
    source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert "ip_address" in source
    assert "rssi_dbm" in source
    assert "current_ip_address()" in source
    assert "wifi_rssi" in source


def test_firmware_media_transfer_uses_temp_file_checksum_and_cleanup():
    source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert '"endpoint.media.transfer"' in source
    assert '"%s/.%s.tmp"' in source
    assert "ensure_sd_media_directories()" in source
    assert "mkdir_failed" in source
    assert "PSA_ALG_SHA_256" in source
    assert "checksum_mismatch" in source
    assert "std::remove(temp_path)" in source
    assert "std::rename(temp_path, final_path)" in source
    assert 'cJSON_GetObjectItem(payload, "rewrite")' in source
    assert "media_transfer_active" in source
    assert "downloading_file" in source
    assert "request_display_assets_reload()" in source
    assert 'std::strcmp(request.destination, "picture") == 0' in source
    assert 'std::strcmp(request.destination, "sprite") == 0' in source


def test_firmware_sound_transfer_can_activate_sd_playback():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    player_source = FIRMWARE_TTS_PLAYER.read_text()

    assert "hexe::voice::play_sd_sound(request.filename)" in backend_source
    assert "read_audio_file" in player_source
    assert "sd_card_sounds_path()" in player_source
    assert "play_wav(audio)" in player_source


def test_firmware_composited_ui_supports_manifest_alpha_and_clock_scene():
    source = FIRMWARE_DISPLAY.read_text()

    assert "ui_manifest.json" in source
    assert "load_composed_scene()" in source
    assert "draw_composed_scene" in source
    assert '"alpha8"' in source
    assert '"alpha1"' in source
    assert "draw_clock_overlay" in source
    assert "g_scene.avatars" in source
    assert "g_scene.sprites" in source
    assert "format_clock_date_parts" in source
    assert '"date_split"' in source
    assert '"day_x"' in source
    assert '"day_format"' in source
    assert '"day_text"' in source
    assert '"day_scale_percent"' in source
    assert '"date_scale_percent"' in source
    assert "scaled_units" in source
    assert "draw_ota_progress" in source
    assert '"ota_progress"' in source
    assert '"fill_color_rgb565"' in source
    assert "kOta" in source
    assert "Wednesday" in source


def test_firmware_idle_uses_clock_avatar_immediately():
    source = FIRMWARE_DISPLAY.read_text()

    assert "kClock" in source
    assert '"clock"' in source
    assert '"idle_timeout_ms"' in source
    assert "case hexe::AppPhase::kIdle:\n      return UiAssetId::kClock;" in source
    assert "idle_clock_due" not in source
    assert "return asset_id_for_phase(phase);" in source
    assert "if (id == UiAssetId::kClock) {\n    draw_clock_overlay();" in source


def test_firmware_ota_uses_ota_avatar_and_configurable_progress():
    source = FIRMWARE_DISPLAY.read_text()

    assert 'std::strcmp(key, "ota") == 0' in source
    assert "hexe::state().ota_active" in source
    assert "return UiAssetId::kOta" in source
    assert "g_scene.ota_progress" in source
    assert '"frame"' in source
    assert "bar.frame" in source
    assert '"orientation"' in source
    assert '"vertical"' in source
    assert "draw_ota_progress();" in source


def test_firmware_handles_backend_session_state_events():
    source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert 'std::strcmp(type, "session.state") == 0' in source
    assert 'std::strcmp(ux_state, "replying") == 0' in source
    assert "hexe::idle_or_connecting_phase()" in source


def test_firmware_ui_assets_are_manifest_driven_not_hardcoded_filenames():
    source = FIRMWARE_DISPLAY.read_text()

    assert "g_ui_assets" not in source
    assert "try_load_sd_ui_asset_file" not in source
    assert "Logo 320x240.rgb565" not in source
    assert "Idle.rgb565" not in source
    assert "Listen.rgb565" not in source
    assert "Thinking.rgb565" not in source
    assert "Talk.rgb565" not in source
    assert "Work.rgb565" not in source
    assert "Error.rgb565" not in source


def test_firmware_display_requires_manifest_but_skips_missing_layers():
    source = FIRMWARE_DISPLAY.read_text()

    assert "draw_simple_ui_asset" not in source
    assert "simple_style_for_asset" not in source
    assert "overlay.json" not in source
    assert "g_scene.avatars[static_cast<uint8_t>(UiAssetId::kIdle)]" not in source
    assert "if (!g_scene.loaded) {\n    return false;\n  }" in source
    assert "if (!g_scene.loaded || g_scene.background.pixels == nullptr)" not in source
    assert "Composited UI manifest did not load a valid background" not in source
    assert "if (!draw_composed_scene(asset_id)) {\n    return;\n  }" in source
    assert "request_display_assets_reload" in source
    assert "g_display_assets_reload_requested.exchange(false" in source
    assert "free_composed_scene(g_scene)" in source


def test_firmware_sprite_converter_targets_ui_manifest_layers():
    source = FIRMWARE_CONVERT_SPRITE.read_text()

    assert "overlay.json" not in source
    assert "LAYER_JSON_NAME" in source
    assert "ui_manifest.json" in source
    assert 'ALPHA_COLOR="${ALPHA_COLOR:-#FF00FF}"' in source
    assert "--alpha-color" in source


def test_firmware_display_keeps_full_framebuffer_out_of_internal_dma():
    source = FIRMWARE_DISPLAY.read_text()

    assert "g_lcd_flush_buffer" in source
    assert "MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT" in source
    assert "kFlushBufferBytes" in source
    assert "kWidth * kHeight * sizeof(uint16_t), MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL" not in source
    assert "esp_lcd_panel_io_register_event_callbacks" in source
    assert "on_color_trans_done" in source
    assert "xSemaphoreTake(g_lcd_flush_done" in source
    assert "xSemaphoreGiveFromISR" in source


def test_firmware_display_skips_unchanged_frames():
    source = FIRMWARE_DISPLAY.read_text()

    assert "DisplayFrameSignature" in source
    assert "make_frame_signature" in source
    assert "should_render_frame(signature)" in source
    assert "same_frame_signature" in source
    assert "remember_frame_signature(signature)" in source
    assert "clock_tick_signature(asset_id)" in source
    assert "audio_pulse_phase" in source
    assert "g_last_frame_signature_valid = false" in source


def test_firmware_display_layers_can_clip_offscreen():
    source = FIRMWARE_DISPLAY.read_text()

    assert 'cJSON_GetObjectItem(node, "clip")' in source
    assert "requires clip=true" in source
    assert "geometry %dx%d at %d,%d is outside the screen" in source
    assert "asset.x + asset.width <= 0" in source
    assert "asset.y + asset.height <= 0" in source
    assert "blend_pixel(asset.x + col, asset.y + row" in source


def test_firmware_storage_reformat_is_media_only():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    storage_source = FIRMWARE_STORAGE.read_text()

    assert '"endpoint.storage.reformat"' in backend_source
    assert "reformat_sd_media()" in backend_source
    assert 'cJSON_AddBoolToObject(storage, "media_reformat", sd_available)' in backend_source
    assert 'cJSON_AddBoolToObject(controls, "storage_reformat", sd_available)' in backend_source
    assert "remove_tree_contents(kPicturesPath)" in storage_source
    assert "remove_tree_contents(kSpritesPath)" in storage_source
    assert "remove_tree_contents(kSoundsPath)" in storage_source
    assert "ensure_sd_media_directories_internal()" in storage_source


def test_firmware_supports_home_assistant_voice_pe_profile():
    cmake_source = FIRMWARE_CMAKE.read_text()
    audio_source = FIRMWARE_AUDIO_HA_VOICE_PE.read_text()
    buttons_source = FIRMWARE_BUTTONS_HA_VOICE_PE.read_text()
    display_source = FIRMWARE_DISPLAY_NONE.read_text()
    storage_source = FIRMWARE_STORAGE_NVS_ONLY.read_text()
    tts_source = FIRMWARE_TTS_PLAYER_NOOP.read_text()

    assert "HEXE_BOARD_PROFILE" in cmake_source
    assert 'HEXE_BOARD_PROFILE STREQUAL "ha_voice_pe"' in cmake_source
    assert '"board/audio_ha_voice_pe.cpp"' in cmake_source
    assert '"board/buttons_ha_voice_pe.cpp"' in cmake_source
    assert '"board/display_none.cpp"' in cmake_source
    assert '"board/storage_nvs_only.cpp"' in cmake_source
    assert '"voice/tts_player_noop.cpp"' in cmake_source
    assert cmake_source.count('"voice/tts_player.cpp"') == 1

    assert "I2S_ROLE_SLAVE" in audio_source
    assert "I2S_DATA_BIT_WIDTH_32BIT" in audio_source
    assert "I2S_SLOT_MODE_STEREO" in audio_source
    assert "GPIO_NUM_13" in audio_source
    assert "GPIO_NUM_14" in audio_source
    assert "GPIO_NUM_15" in audio_source
    assert "GPIO_NUM_4" in audio_source
    assert "GPIO_NUM_47" in audio_source
    assert "return false;" in audio_source[audio_source.index("bool audio_output_ready()") :]

    assert "GPIO_NUM_0" in buttons_source
    assert "GPIO_NUM_3" in buttons_source
    assert "hardware_mute_active" in buttons_source

    assert "Display disabled for this board profile" in display_source
    assert 'return "none";' in display_source
    assert "NVS storage initialized; SD media storage disabled" in storage_source
    assert "TTS output disabled for this board profile" in tts_source
    assert "tts_playback_active()" in tts_source
