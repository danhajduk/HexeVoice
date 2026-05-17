from pathlib import Path


FIRMWARE_BACKEND_CLIENT = Path("firmware/main/voice/backend_client.cpp")
FIRMWARE_BUILD_SCRIPT = Path("firmware/build.sh")
FIRMWARE_EXPORT_SCRIPT = Path("firmware/export-artifacts.sh")
FIRMWARE_CMAKE = Path("firmware/main/CMakeLists.txt")
FIRMWARE_AUDIO = Path("firmware/main/board/audio.cpp")
FIRMWARE_AUDIO_HA_VOICE_PE = Path("firmware/main/board/audio_ha_voice_pe.cpp")
FIRMWARE_BUTTONS_HA_VOICE_PE = Path("firmware/main/board/buttons_ha_voice_pe.cpp")
FIRMWARE_DISPLAY = Path("firmware/main/board/display.cpp")
FIRMWARE_DISPLAY_NONE = Path("firmware/main/board/display_none.cpp")
FIRMWARE_LED_RING = Path("firmware/main/board/led_ring.cpp")
FIRMWARE_LED_RING_HA_VOICE_PE = Path("firmware/main/board/led_ring_ha_voice_pe.cpp")
FIRMWARE_STORAGE = Path("firmware/main/board/storage.cpp")
FIRMWARE_STORAGE_NVS_ONLY = Path("firmware/main/board/storage_nvs_only.cpp")
FIRMWARE_TTS_PLAYER = Path("firmware/main/voice/tts_player.cpp")
FIRMWARE_TTS_PLAYER_HA_VOICE_PE = Path("firmware/main/voice/tts_player_ha_voice_pe.cpp")
FIRMWARE_TTS_PLAYER_NOOP = Path("firmware/main/voice/tts_player_noop.cpp")
FIRMWARE_CONVERT_SPRITE = Path("firmware/tools/convert-sprite.sh")
FIRMWARE_APP_MAIN = Path("firmware/main/app_main.cpp")
FIRMWARE_APP_STATE = Path("firmware/main/app_state.h")


def test_firmware_voice_events_emit_full_v1_envelope():
    source = FIRMWARE_BACKEND_CLIENT.read_text()
    tts_sources = FIRMWARE_TTS_PLAYER.read_text() + FIRMWARE_TTS_PLAYER_HA_VOICE_PE.read_text()

    assert "kVoiceEventSchemaVersion" in source
    assert "append_event_header" in source
    assert '"event_id"' in source
    assert '"schema_version"' in source
    assert '"timestamp"' in source
    assert "session.start" in source
    assert 'start_voice_session(const char *wake_source)' in source
    assert 'normalized_wake_source(wake_source)' in source
    assert "audio.chunk" in source
    assert "audio.end" in source
    assert "vad.speech_started" in source
    assert "notify_vad_speech_started" in source
    assert "session.cancel" in source
    assert "command.ack" in source
    assert "command.error" in source
    assert "send_tts_playback_event" in source
    assert "tts.playback.download_started" in tts_sources
    assert "tts.playback.first_audio_frame" in tts_sources
    assert "tts.playback.completed" in tts_sources
    assert "tts.playback.failed" in tts_sources
    assert "prewarm_tts_output" in source
    assert "stream_http_wav" in FIRMWARE_TTS_PLAYER_HA_VOICE_PE.read_text()
    assert "Streaming TTS WAV at %d Hz while downloading" in FIRMWARE_TTS_PLAYER_HA_VOICE_PE.read_text()
    assert "kVoiceWsSendAttempts = 3" in source
    assert "Voice WebSocket send failed after %d attempts" in source


def test_firmware_backend_commands_acknowledge_receipt_with_ok():
    source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert "acknowledge_command_received(type, payload);" in source
    assert 'std::strcmp(event_type, "ota.update") == 0' in source
    assert 'std::strncmp(event_type, "endpoint.", 9) == 0' in source
    assert 'send_command_ack(payload_request_id(payload), command_type_for_event(event_type), "accepted", "OK");' in source
    assert 'return "endpoint.volume.set";' in source
    assert 'return "endpoint.micro_vad.set";' in source


def test_firmware_vad_keeps_listening_window_after_wake_word():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    source = FIRMWARE_AUDIO.read_text()
    pe_source = FIRMWARE_AUDIO_HA_VOICE_PE.read_text()

    assert "kVadSilenceHoldMs = 2500" in source
    assert 'finish_audio_stream("vad_silence")' in source
    assert "notify_vad_speech_started(level)" in source
    assert "notify_vad_speech_started(level)" in pe_source
    assert "kPostTtsInputIgnoreUs = 800000" in backend_source
    assert "start_post_tts_input_cooldown();" in backend_source
    assert "post_tts_input_cooldown_active()" in backend_source
    assert "g_preroll_count = 0" in backend_source
    assert "play_wake_accepted_sound();" in backend_source
    assert '"micro_vad"' in backend_source
    assert '"max_pause_ms", 3000' in backend_source
    assert "hexe::voice::post_tts_input_cooldown_active()" in pe_source
    assert "micro_vad_chunk_active = false" in pe_source


def test_firmware_heartbeat_reports_network_metadata():
    source = FIRMWARE_BACKEND_CLIENT.read_text()
    app_state_source = FIRMWARE_APP_STATE.read_text()
    tts_source = FIRMWARE_TTS_PLAYER.read_text()
    pe_tts_source = FIRMWARE_TTS_PLAYER_HA_VOICE_PE.read_text()
    audio_source = FIRMWARE_AUDIO.read_text()
    pe_audio_source = FIRMWARE_AUDIO_HA_VOICE_PE.read_text()

    assert "ip_address" in source
    assert "rssi_dbm" in source
    assert "enum class PlaybackLifecycleState" in app_state_source
    assert "mic_paused_for_playback" in app_state_source
    assert "tts_playback_state" in app_state_source
    assert "paused_for_playback" in source
    assert "playback_active" in source
    assert "playback_state" in source
    assert "playback_lifecycle_state_name" in source
    assert "set_playback_lifecycle(hexe::PlaybackLifecycleState::kQueued, true)" in tts_source
    assert "set_playback_lifecycle(hexe::PlaybackLifecycleState::kStarted, true)" in pe_tts_source
    assert "played ? hexe::PlaybackLifecycleState::kFinished" in pe_tts_source
    assert "set_playback_lifecycle(hexe::PlaybackLifecycleState::kStopped, false)" in pe_tts_source
    assert "mic_paused_for_playback = true" in audio_source
    assert "mic_paused_for_playback = false" in pe_audio_source
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
    assert "play_wav(audio, request)" in player_source
    assert "tts.playback.first_audio_frame" in player_source
    assert "tts.playback.completed" in player_source
    assert "tts.playback.failed" in player_source


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
    assert "g_wake_accepted_for_session" in source
    assert 'wake_accepted || (g_wake_accepted_for_session && std::strcmp(ux_state, "listening") == 0)' in source
    assert 'g_wake_accepted_for_session && std::strcmp(ux_state, "thinking") == 0' in source
    assert "if (g_wake_accepted_for_session) {\n      hexe::state().phase = hexe::AppPhase::kThinking;" in source
    assert "event_requests_followup_listen" in source
    assert "resume_audio_stream_for_followup" in source
    assert '"listen_timeout_ms"' in source
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
    tts_source = FIRMWARE_TTS_PLAYER_HA_VOICE_PE.read_text()

    assert "HEXE_BOARD_PROFILE" in cmake_source
    assert 'HEXE_BOARD_PROFILE STREQUAL "ha_voice_pe"' in cmake_source
    assert '"board/audio_ha_voice_pe.cpp"' in cmake_source
    assert '"board/buttons_ha_voice_pe.cpp"' in cmake_source
    assert '"board/display_none.cpp"' in cmake_source
    assert '"board/storage_nvs_only.cpp"' in cmake_source
    assert '"voice/tts_player_ha_voice_pe.cpp"' in cmake_source
    assert cmake_source.count('"voice/tts_player.cpp"') == 1
    assert "esp_driver_i2c" in cmake_source
    assert "esp_driver_i2s" in cmake_source

    assert "I2S_ROLE_SLAVE" in audio_source
    assert "I2S_DATA_BIT_WIDTH_32BIT" in audio_source
    assert "I2S_SLOT_MODE_STEREO" in audio_source
    assert "voice_channel_sample" in audio_source
    assert "GPIO_NUM_13" in audio_source
    assert "GPIO_NUM_14" in audio_source
    assert "GPIO_NUM_15" in audio_source
    assert "GPIO_NUM_4" in audio_source
    assert "GPIO_NUM_5" in audio_source
    assert "GPIO_NUM_6" in audio_source
    assert "GPIO_NUM_47" in tts_source
    assert "kVoiceKitI2cAddress = 0x42" in audio_source
    assert "kDfuGetVersionCommand = 88" in audio_source
    assert "gpio_set_level(kVoiceKitReset, 1)" in audio_source
    assert "gpio_set_level(kVoiceKitReset, 0)" in audio_source
    assert "Voice Kit XMOS firmware version" in audio_source
    assert "Voice Kit did not respond after reset" in audio_source
    assert "kVadTaskStackBytes = 8192" in audio_source
    assert "kMicReadTimeoutLogEvery = 200" in audio_source
    assert "Voice PE microphone read timeout count=" in audio_source
    assert "kVadStartVoiceFrames = 3" in audio_source
    assert "kVadStartNoiseMultiplier = 3" in audio_source
    assert "kVadReleasePeakPercent = 60" in audio_source
    assert "kVadSilenceHoldMs = 1200" in audio_source
    assert "kMaxMicroVadPauseMs = 3000" in FIRMWARE_APP_MAIN.parent.joinpath("system/settings.cpp").read_text()
    assert "speech_peak_level" in audio_source
    assert "update_noise_floor" in audio_source
    assert "std::array<int32_t, kFrameSamples * 2> g_raw_samples" in audio_source
    assert "std::array<int16_t, kFrameSamples> g_mono_samples" in audio_source
    assert 'xTaskCreate(vad_task, "hexe_vpe_vad", kVadTaskStackBytes' in audio_source
    assert "return g_voice_kit_ready;" in audio_source[audio_source.index("bool audio_output_ready()") :]

    assert "GPIO_NUM_0" in buttons_source
    assert "GPIO_NUM_3" in buttons_source
    assert "hardware_mute_active" in buttons_source
    assert 'start_voice_session("button")' in buttons_source

    assert "Display disabled for this board profile" in display_source
    assert 'return "none";' in display_source
    assert "NVS storage initialized; SD media storage disabled" in storage_source
    assert "kAic3204I2cAddress = 0x18" in tts_source
    assert "GPIO_NUM_7" in tts_source
    assert "GPIO_NUM_8" in tts_source
    assert "GPIO_NUM_10" in tts_source
    assert "kSpeakerSampleRate = 48000" in tts_source
    assert "I2S_ROLE_SLAVE" in tts_source
    assert "I2S_DATA_BIT_WIDTH_32BIT" in tts_source
    assert "I2S_SLOT_MODE_STEREO" in tts_source
    assert "i2c_master_get_bus_handle" in tts_source
    assert "i2s_channel_write" in tts_source
    assert "ensure_codec_ready" in tts_source
    assert "set_codec_volume" in tts_source
    assert "interpolate_pcm16" in tts_source
    assert "kPlaybackDrainFrames = kSpeakerSampleRate / 4" in tts_source
    assert "write_silence_drain" in tts_source
    assert "kWakeDingStreamId[] = \"wake-ding\"" in tts_source
    assert "play_wake_ding" in tts_source
    assert "play_wake_accepted_sound()" in tts_source
    assert "if (!wake_ding) {\n      state.phase = hexe::AppPhase::kReplying;" in tts_source
    assert "tts.playback.first_audio_frame" in tts_source
    assert "tts.playback.completed" in tts_source
    assert "tts.playback.failed" in tts_source
    assert "Home Assistant Voice PE TTS player initialized" in tts_source
    assert "tts_playback_active()" in tts_source


def test_voice_pe_led_ring_driver_contract_and_priority():
    app_source = FIRMWARE_APP_MAIN.read_text()
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    cmake_source = FIRMWARE_CMAKE.read_text()
    noop_source = FIRMWARE_LED_RING.read_text()
    led_source = FIRMWARE_LED_RING_HA_VOICE_PE.read_text()
    doc_source = Path("docs/voice-pe-led-ring.md").read_text()

    assert '"board/led_ring.cpp"' in cmake_source
    assert '"board/led_ring_ha_voice_pe.cpp"' in cmake_source
    assert "esp_driver_rmt" in cmake_source
    assert "init_led_ring();" in app_source
    assert "update_led_ring_patterns();" in app_source

    assert "kLedDataGpio = GPIO_NUM_21" in led_source
    assert "kLedPowerGpio = GPIO_NUM_45" in led_source
    assert "kLedCount = 12" in led_source
    assert "kPatternFrameMs = 100" in led_source
    assert "kBottomLedIndex = 0" in led_source
    assert "kVisualToPhysical" in led_source
    assert "7, 8, 9, 10, 11, 0, 1, 2, 3, 4, 5, 6" in led_source
    assert "g_pixels[physical_index * 3 + 0] = green" in led_source
    assert "g_pixels[physical_index * 3 + 1] = red" in led_source
    assert "g_pixels[physical_index * 3 + 2] = blue" in led_source
    assert "set_led_power(false)" in led_source
    assert "transmit_pixels_locked(false)" in led_source
    assert "render_frame_locked" in led_source
    assert "set_pixel(frame, kBottomLedIndex, color(255, 120, 0))" in led_source
    assert "set_pixel(frame, 3, accent)" in led_source
    assert "set_pixel(frame, 9, accent)" in led_source
    assert "listening_blink_on" not in led_source
    assert "const bool capturing_active = state.vad_speaking || state.audio_streaming" in led_source
    assert "return LedPattern::kWakeListening" in led_source
    assert "cursor / 2" not in led_source
    assert "color(0, 55, 80)" in led_source
    assert "color(80, 255, 180)" in led_source

    pattern_source = led_source[led_source.index("LedPattern pattern_for_state") :]
    assert pattern_source.index("kBooting") < pattern_source.index("kOtaProgress")
    assert pattern_source.index("kOtaProgress") < pattern_source.index("kMuted")
    assert pattern_source.index("kMuted") < pattern_source.index("kWifiConnecting")
    assert pattern_source.index("kWifiConnecting") < pattern_source.index("kBackendConnecting")
    assert pattern_source.index("kBackendConnecting") < pattern_source.index("kSpeakerSilent")
    assert pattern_source.index("kSpeakerSilent") < pattern_source.index("kListening")

    assert "led_ring_show_completed()" in led_source
    assert "led_ring_simulate_pattern(const char *pattern_name, int duration_ms)" in led_source
    assert '"capturing", LedPattern::kCapturing' in led_source
    assert '"speaker_silent", LedPattern::kSpeakerSilent' in led_source
    assert "led_ring_show_completed();" in backend_source
    assert "kCancelled" not in led_source
    assert "led_ring_show_cancelled" not in led_source
    assert "led_ring_show_cancelled" not in backend_source
    assert 'std::strcmp(type, "endpoint.led.simulate") == 0' in backend_source
    assert 'std::strcmp(type, "endpoint.micro_vad") == 0' in backend_source
    assert "ESP_ERR_NOT_SUPPORTED" in noop_source

    assert "Priority order" in doc_source
    assert "OTA-Safe Behavior" in doc_source
    assert "100 ms" in doc_source
    assert "visual slot `0` is the bottom LED" in doc_source
    assert "Listening should keep the two side LEDs at visual slots `3` and `9` steadily on" in doc_source
    assert "overlay the bottom orange marker while the side listening LEDs stay on" in doc_source
    assert "Wi-Fi and disconnected diagnostic patterns should traverse the full ring" in doc_source
    assert "dim completed-progress LEDs and a brighter moving" in doc_source
    assert "center-held rotation" in doc_source


def test_voice_pe_rotary_dial_led_affordances_do_not_trigger_center_action():
    buttons_source = FIRMWARE_BUTTONS_HA_VOICE_PE.read_text()
    led_source = FIRMWARE_LED_RING_HA_VOICE_PE.read_text()
    noop_source = FIRMWARE_LED_RING.read_text()

    assert "kDialA = GPIO_NUM_16" in buttons_source
    assert "kDialB = GPIO_NUM_18" in buttons_source
    assert "kQuadratureStepsPerDetent = 2" in buttons_source
    assert "kVolumeStepPercent = 5" in buttons_source
    assert "hexe::voice::set_output_volume(new_volume)" in buttons_source
    assert "hexe::board::led_ring_show_volume(new_volume)" in buttons_source
    assert "hexe::board::led_ring_adjust_accent_hue(direction)" in buttons_source
    assert "g_center_rotary_consumed = true" in buttons_source
    assert "Center button release consumed by rotary color selection" in buttons_source
    assert 'start_voice_session("button")' in buttons_source

    assert "LedPattern::kVolumeDisplay" in led_source
    assert "LedPattern::kColorSelect" in led_source
    assert "g_accent_hue_degrees" in led_source
    assert "show_momentary_pattern(LedPattern::kVolumeDisplay)" in led_source
    assert "show_momentary_pattern(LedPattern::kColorSelect)" in led_source
    assert "led_ring_show_volume(int volume_percent)" in noop_source
    assert "led_ring_adjust_accent_hue(int delta_steps)" in noop_source


def test_firmware_build_exports_profile_specific_ota_artifacts():
    build_source = FIRMWARE_BUILD_SCRIPT.read_text()
    export_source = FIRMWARE_EXPORT_SCRIPT.read_text()

    assert 'requested_profile="all"' in build_source
    assert "build_profile esp_box_3" in build_source
    assert "build_profile ha_voice_pe" in build_source
    assert "hexe_firmware_${1}.bin" in build_source
    assert '\\"filename\\":\\"${filename}\\"' in build_source

    assert "PROFILE_APP_FILENAME" in export_source
    assert "hexe_firmware_${BOARD_PROFILE}.bin" in export_source
    assert "manifest-${BOARD_PROFILE}.json" in export_source
    assert "hexe_firmware*.bin > SHA256SUMS" in export_source
    assert "cp \"${APP_SRC}\" \"${COMMON_EXPORT_DIR}/${PROFILE_APP_FILENAME}\"" in export_source
