from pathlib import Path


FIRMWARE_BACKEND_CLIENT = Path("firmware/main/voice/backend_client.cpp")
FIRMWARE_DISPLAY = Path("firmware/main/board/display.cpp")
FIRMWARE_STORAGE = Path("firmware/main/board/storage.cpp")
FIRMWARE_TTS_PLAYER = Path("firmware/main/voice/tts_player.cpp")


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


def test_firmware_storage_reformat_is_media_only():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    storage_source = FIRMWARE_STORAGE.read_text()

    assert '"endpoint.storage.reformat"' in backend_source
    assert "reformat_sd_media()" in backend_source
    assert "remove_tree_contents(kPicturesPath)" in storage_source
    assert "remove_tree_contents(kSpritesPath)" in storage_source
    assert "remove_tree_contents(kSoundsPath)" in storage_source
    assert "ensure_sd_media_directories_internal()" in storage_source
