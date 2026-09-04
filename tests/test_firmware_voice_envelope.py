import csv
import hashlib
import subprocess
from pathlib import Path


FIRMWARE_BACKEND_CLIENT = Path("firmware/components/endpoint_runtime/voice/backend_client.cpp")
FIRMWARE_BUILD_SCRIPT = Path("firmware/build.sh")
FIRMWARE_EXPORT_SCRIPT = Path("firmware/export-artifacts.sh")
FIRMWARE_PROVISIONING_CSV_TOOL = Path("firmware/tools/provisioning-env-to-nvs-csv.py")
FIRMWARE_CMAKE = Path("firmware/components/endpoint_runtime/CMakeLists.txt")
FIRMWARE_AUDIO = Path("firmware/components/endpoint_runtime/board/audio.cpp")
FIRMWARE_AUDIO_HA_VOICE_PE = Path("firmware/components/endpoint_runtime/board/audio_ha_voice_pe.cpp")
FIRMWARE_BUTTONS = Path("firmware/components/endpoint_runtime/board/buttons.cpp")
FIRMWARE_BUTTONS_HA_VOICE_PE = Path("firmware/components/endpoint_runtime/board/buttons_ha_voice_pe.cpp")
FIRMWARE_DISPLAY = Path("firmware/components/endpoint_runtime/board/display.cpp")
FIRMWARE_DISPLAY_NONE = Path("firmware/components/endpoint_runtime/board/display_none.cpp")
FIRMWARE_LED_RING = Path("firmware/components/endpoint_runtime/board/led_ring.cpp")
FIRMWARE_LED_RING_HA_VOICE_PE = Path("firmware/components/endpoint_runtime/board/led_ring_ha_voice_pe.cpp")
FIRMWARE_STORAGE = Path("firmware/components/endpoint_runtime/board/storage.cpp")
FIRMWARE_STORAGE_NVS_ONLY = Path("firmware/components/endpoint_runtime/board/storage_nvs_only.cpp")
FIRMWARE_SETTINGS = Path("firmware/components/endpoint_runtime/system/settings.cpp")
FIRMWARE_SETTINGS_HEADER = Path("firmware/components/endpoint_runtime/system/settings.h")
FIRMWARE_OTA = Path("firmware/components/endpoint_runtime/system/ota.cpp")
FIRMWARE_OTA_HEADER = Path("firmware/components/endpoint_runtime/system/ota.h")
FIRMWARE_WIFI = Path("firmware/components/endpoint_runtime/board/wifi.cpp")
FIRMWARE_TTS_PLAYER = Path("firmware/components/endpoint_runtime/voice/tts_player.cpp")
FIRMWARE_TTS_PLAYER_HEADER = Path("firmware/components/endpoint_runtime/voice/tts_player.h")
FIRMWARE_TTS_PLAYER_HA_VOICE_PE = Path("firmware/components/endpoint_runtime/voice/tts_player_ha_voice_pe.cpp")
FIRMWARE_TTS_PLAYER_NOOP = Path("firmware/components/endpoint_runtime/voice/tts_player_noop.cpp")
FIRMWARE_CONVERT_SPRITE = Path("firmware/tools/convert-sprite.sh")
FIRMWARE_APP_MAIN = Path("firmware/apps/endpoint/main/app_main.cpp")
FIRMWARE_APP_STATE = Path("firmware/components/endpoint_runtime/app_state.h")
FIRMWARE_MICRO_WAKE_ENGINE = Path("firmware/components/endpoint_runtime/voice/micro_wake_engine.cpp")
FIRMWARE_MICRO_WAKE_ENGINE_HEADER = Path("firmware/components/endpoint_runtime/voice/micro_wake_engine.h")
FIRMWARE_MODEL_BUNDLE = Path("firmware/components/endpoint_runtime/voice/model_bundle.cpp")
FIRMWARE_MODEL_BUNDLE_HEADER = Path("firmware/components/endpoint_runtime/voice/model_bundle.h")
FIRMWARE_MICRO_WAKE_MODELS = Path("firmware/components/endpoint_runtime/voice/models")
FRONTEND_API_CLIENT = Path("frontend/src/api/client.js")
FRONTEND_ENDPOINT_DASHBOARD = Path("frontend/src/features/dashboard/VoiceEndpointDashboardSection.jsx")


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
    assert "wake.candidate" in source
    assert "wake.election.result" in source
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


def test_firmware_reports_stable_hardware_id_from_efuse_mac():
    source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert '#include "esp_mac.h"' in source
    assert "esp_efuse_mac_get_default(mac)" in source
    assert '"esp32s3-%02x%02x%02x%02x%02x%02x"' in source
    assert '\\"hardware_id\\":\\"%s\\"' in source
    assert 'body.append("\\",\\"hardware_id\\":\\"");' in source
    assert 'cJSON_AddStringToObject(identity, "hardware_id", hardware_id())' in source
    assert 'cJSON_AddStringToObject(identity, "id_source", "esp_efuse_mac")' in source


def test_firmware_ota_enforces_signed_manifest_and_download_checksum():
    ota_source = FIRMWARE_OTA.read_text()
    ota_header = FIRMWARE_OTA_HEADER.read_text()
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    export_script = FIRMWARE_EXPORT_SCRIPT.read_text()

    assert "struct OtaUpdateManifest" in ota_header
    assert "verify_ota_manifest_signature" in ota_source
    assert "hmac-sha256" in ota_source
    assert "inner_pad" in ota_source
    assert "outer_pad" in ota_source
    assert "kEndpointOtaManifestSigningKey" in ota_source
    assert "kEndpointOtaManifestKeyId" in ota_source
    assert "unsupported_profile" in ota_source
    assert "downgrade_or_replay" in ota_source
    assert "missing_signature" in ota_source
    assert "invalid_signature" in ota_source
    assert "missing_checksum" in ota_source
    assert "checksum_mismatch" in ota_source
    assert "HTTP_EVENT_ON_DATA" in ota_source
    assert "mbedtls_md_update" in ota_source
    assert "esp_https_ota_finish" in ota_source
    assert "constant_time_equal(calculated_sha256, request.sha256)" in ota_source

    assert '"signature_algorithm"' in backend_source
    assert '"signature_key_id"' in backend_source
    assert '"manifest_signature"' in backend_source
    assert "send_command_error(request_id, \"ota.update\", ota_error_code" in backend_source
    assert "kEndpointBoardProfile" in backend_source
    assert 'return "ota";' in backend_source
    assert 'cJSON_AddBoolToObject(ota, "active", state.ota_active)' in backend_source
    assert 'cJSON_AddStringToObject(ota, "status", state.ota_active ? "running" : "idle")' in backend_source
    assert 'cJSON_AddNumberToObject(ota, "progress_percent", state.ota_progress_percent)' in backend_source

    assert '[[ "${VERSION}" == *dirty* ]]' in export_script
    assert "Refusing to export dirty firmware version" in export_script


def test_firmware_ota_rejects_incompatible_packages_before_download():
    ota_source = FIRMWARE_OTA.read_text()
    ota_header = FIRMWARE_OTA_HEADER.read_text()
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    export_script = FIRMWARE_EXPORT_SCRIPT.read_text()

    for field in [
        "application_type",
        "board_profile",
        "soc",
        "idf_target",
        "flash_size",
        "psram_size",
        "partition_schema",
        "app_slot_size",
        "firmware_api_version",
        "model_api_version",
        "asset_api_version",
        "calibration_schema_version",
        "release_channel",
        "security_policy",
    ]:
        assert f"const char *{field};" in ota_header
        assert f"char {field}[" in ota_source
        assert f'cJSON_GetObjectItem(payload, "{field}")' in backend_source
        assert f"manifest.{field} =" in backend_source

    for error_code in [
        "wrong_application_type",
        "board_profile_mismatch",
        "soc_mismatch",
        "idf_target_mismatch",
        "flash_geometry_mismatch",
        "psram_geometry_mismatch",
        "partition_schema_mismatch",
        "app_slot_size_mismatch",
        "image_too_large",
        "incompatible_firmware_api",
        "incompatible_model_api",
        "incompatible_asset_api",
        "incompatible_calibration_schema",
        "unsupported_release_channel",
        "unsupported_security_policy",
    ]:
        assert error_code in ota_source

    assert '#include "board_profile_pins.h"' in ota_source
    assert "parse_size_label_bytes(hexe::board::pins::kAppSlotSize" in ota_source
    assert 'string_in_set(request.release_channel, "dev", "stable")' in ota_source
    assert "signed_manifest_sha256_required" in ota_source
    assert "payload.append(request.application_type)" in ota_source
    assert "payload.append(request.security_policy)" in ota_source
    assert "verify_ota_manifest_signature(request)" in ota_source
    assert 'FIRMWARE_RELEASE_CHANNEL="${FIRMWARE_RELEASE_CHANNEL:-dev}"' in export_script
    assert 'FIRMWARE_SECURITY_POLICY="${FIRMWARE_SECURITY_POLICY:-signed_manifest_sha256_required}"' in export_script
    assert '"release_channel": "${FIRMWARE_RELEASE_CHANNEL}"' in export_script
    assert '"security_policy": "${FIRMWARE_SECURITY_POLICY}"' in export_script


def test_firmware_ota_boot_validation_marks_valid_only_after_local_self_tests():
    ota_source = FIRMWARE_OTA.read_text()
    ota_header = FIRMWARE_OTA_HEADER.read_text()
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert "validate_pending_boot_image()" in ota_source
    assert "local_startup_self_tests_pass" in ota_source
    assert "ESP_OTA_IMG_PENDING_VERIFY" in ota_source
    assert "ESP_OTA_IMG_NEW" in ota_source
    assert "esp_ota_mark_app_valid_cancel_rollback()" in ota_source
    assert "esp_ota_mark_app_invalid_rollback_and_reboot()" in ota_source
    assert "audio_input_ready()" in ota_source
    assert "audio_output_ready()" in ota_source
    assert "display_self_test_required()" in ota_source
    assert "wake_word_on_device_available()" in ota_source
    assert "playback_stop_word_on_device_available()" in ota_source
    assert "backend_connected" not in ota_source
    assert "voice_ws_connected" not in ota_source
    assert "esp_wifi_connect" not in ota_source
    assert "const char *ota_boot_validation_status()" in ota_header
    assert "bool ota_boot_pending_verification()" in ota_header
    assert "bool ota_boot_marked_valid()" in ota_header

    assert 'cJSON_AddStringToObject(ota, "boot_validation_status", hexe::system::ota_boot_validation_status())' in backend_source
    assert 'cJSON_AddStringToObject(ota, "running_partition_state", hexe::system::ota_running_partition_state())' in backend_source
    assert 'cJSON_AddBoolToObject(ota, "pending_verification", hexe::system::ota_boot_pending_verification())' in backend_source
    assert 'cJSON_AddBoolToObject(ota, "marked_valid_after_self_tests", hexe::system::ota_boot_marked_valid())' in backend_source
    assert 'cJSON_AddBoolToObject(ota, "rollback_available", hexe::system::ota_rollback_available())' in backend_source


def test_firmware_ota_failure_paths_abort_or_rollback_without_marking_valid():
    ota_source = FIRMWARE_OTA.read_text()

    assert ota_source.index("if (!validate_ota_manifest(request, error_code, error_code_size))") < ota_source.index(
        "xQueueSend(g_ota_queue, &request, 0)"
    )
    assert ota_source.index('"board_profile_mismatch"') < ota_source.index("xQueueSend(g_ota_queue, &request, 0)")
    assert ota_source.index('"partition_schema_mismatch"') < ota_source.index("xQueueSend(g_ota_queue, &request, 0)")
    assert ota_source.index('"image_too_large"') < ota_source.index("xQueueSend(g_ota_queue, &request, 0)")

    assert "http_config.timeout_ms = kOtaTimeoutMs" in ota_source
    assert "result = esp_https_ota_begin(&ota_config, &ota_handle)" in ota_source
    assert "result = esp_https_ota_perform(ota_handle)" in ota_source
    assert "result == ESP_ERR_HTTPS_OTA_IN_PROGRESS" in ota_source
    assert "!esp_https_ota_is_complete_data_received(ota_handle)" in ota_source
    assert "result = ESP_ERR_INVALID_SIZE" in ota_source
    assert ota_source.index("constant_time_equal(calculated_sha256, request.sha256)") < ota_source.index(
        "esp_https_ota_finish(ota_handle)"
    )
    assert "result = ESP_ERR_INVALID_CRC" in ota_source
    assert "esp_https_ota_abort(ota_handle)" in ota_source
    assert "app_state.ota_active = false" in ota_source
    assert "app_state.phase = hexe::AppPhase::kError" in ota_source

    assert ota_source.index("validate_pending_boot_image();") < ota_source.index(
        "g_ota_queue = xQueueCreate(kOtaQueueDepth, sizeof(OtaRequest))"
    )
    assert "ESP_OTA_IMG_PENDING_VERIFY" in ota_source
    assert "ESP_OTA_IMG_NEW" in ota_source
    assert "esp_ota_mark_app_invalid_rollback_and_reboot()" in ota_source
    assert "esp_ota_mark_app_valid_cancel_rollback()" in ota_source
    assert ota_source.index("esp_ota_mark_app_invalid_rollback_and_reboot()") < ota_source.index(
        "esp_ota_mark_app_valid_cancel_rollback()"
    )

    self_test_block = ota_source[
        ota_source.index("bool local_startup_self_tests_pass") : ota_source.index("void refresh_boot_validation_status")
    ]
    assert "backend_connected" not in self_test_block
    assert "voice_ws_connected" not in self_test_block
    assert "esp_wifi_connect" not in self_test_block


def test_firmware_heartbeat_reports_partition_geometry_and_api_versions():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    generator_source = Path("firmware/tools/generate_board_profile_config.py").read_text()
    dashboard_source = FRONTEND_ENDPOINT_DASHBOARD.read_text()

    assert 'cJSON_AddStringToObject(firmware, "application_type", kFirmwareApplicationType)' in backend_source
    assert 'cJSON_AddStringToObject(firmware, "partition_schema", hexe::board::pins::kPartitionSchema)' in backend_source
    assert 'cJSON_AddStringToObject(firmware, "flash_size", hexe::board::pins::kFlashSize)' in backend_source
    assert 'cJSON_AddStringToObject(firmware, "psram_size", hexe::board::pins::kPsramSize)' in backend_source
    assert 'cJSON_AddStringToObject(firmware, "app_slot_size", hexe::board::pins::kAppSlotSize)' in backend_source
    assert 'cJSON_AddStringToObject(firmware, "firmware_api_version", kFirmwareApiVersion)' in backend_source
    assert 'cJSON_AddStringToObject(firmware, "model_api_version", kModelApiVersion)' in backend_source
    assert 'constexpr const char *kPartitionSchema' in generator_source
    assert 'constexpr const char *kFlashSize' in generator_source
    assert "partitionSchema" in dashboard_source
    assert "Flash / PSRAM" in dashboard_source
    assert "App slot" in dashboard_source
    assert "firmwareApi" in dashboard_source


def test_firmware_build_uses_ota_safe_project_versions():
    build_script = FIRMWARE_BUILD_SCRIPT.read_text()

    assert 'printf \'z%s-%s\\n\'' in build_script
    assert 'PROJECT_VERSION="$(ota_safe_project_version)"' in build_script
    assert 'PROJECT_VERSION="$(minimal_project_version "${PROJECT_VERSION}")"' in build_script
    assert '-D "PROJECT_VER=${PROJECT_VERSION}"' in build_script
    assert "Refusing to build OTA firmware from tracked uncommitted changes." in build_script


def test_firmware_scaffold_modules_are_explicit_status_providers():
    app_main = FIRMWARE_APP_MAIN.read_text()
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    micro_wake_source = FIRMWARE_MICRO_WAKE_ENGINE.read_text()
    module_sources = {
        "wake_word": Path("firmware/components/endpoint_runtime/voice/wake_word.cpp").read_text(),
        "stt_stream": Path("firmware/components/endpoint_runtime/voice/stt_stream.cpp").read_text(),
        "assistant_client": Path("firmware/components/endpoint_runtime/voice/assistant_client.cpp").read_text(),
        "telemetry": Path("firmware/components/endpoint_runtime/system/telemetry.cpp").read_text(),
        "power": Path("firmware/components/endpoint_runtime/system/power.cpp").read_text(),
    }
    header_sources = {
        "wake_word": Path("firmware/components/endpoint_runtime/voice/wake_word.h").read_text(),
        "stt_stream": Path("firmware/components/endpoint_runtime/voice/stt_stream.h").read_text(),
        "assistant_client": Path("firmware/components/endpoint_runtime/voice/assistant_client.h").read_text(),
        "telemetry": Path("firmware/components/endpoint_runtime/system/telemetry.h").read_text(),
        "power": Path("firmware/components/endpoint_runtime/system/power.h").read_text(),
    }

    assert "Starting Hexe native firmware runtime" in app_main
    assert "Hexe firmware runtime initialized" in app_main
    assert "scaffold initialized" not in app_main.lower()

    assert "wake_word_runtime_mode" in module_sources["wake_word"]
    assert "wake_word_on_device_available" in header_sources["wake_word"]
    assert '"backend_streaming_with_micro_wake_word_manifest"' in module_sources["wake_word"]
    assert "Experimental endpoint microWakeWord provider configured" in module_sources["wake_word"]
    assert "missing_micro_wake_word_model_asset" in micro_wake_source
    assert "wake_word_election_capable" in module_sources["wake_word"]
    assert "wake_word_election_timeout_ms" in header_sources["wake_word"]
    assert '"endpoint_micro_wake_word"' in module_sources["wake_word"]
    assert "wake_word_primary_model" in header_sources["wake_word"]
    assert '"alexa"' in module_sources["wake_word"]
    assert '"Alexa"' in module_sources["wake_word"]
    assert '"Hexe"' in module_sources["wake_word"]
    assert "github://esphome/micro-wake-word-models/models/v2/alexa.json@main" in module_sources["wake_word"]
    assert "22348" in module_sources["wake_word"]

    assert "stt_stream_runtime_mode" in module_sources["stt_stream"]
    assert "stt_stream_local_decoder_available" in header_sources["stt_stream"]
    assert '"backend_pcm_stream"' in module_sources["stt_stream"]

    assert "assistant_client_runtime_mode" in module_sources["assistant_client"]
    assert "assistant_client_local_llm_available" in header_sources["assistant_client"]
    assert '"backend_voice_pipeline"' in module_sources["assistant_client"]

    assert "telemetry_runtime_mode" in module_sources["telemetry"]
    assert "telemetry_dedicated_channel_enabled" in header_sources["telemetry"]
    assert '"heartbeat_capabilities"' in module_sources["telemetry"]

    assert "power_runtime_mode" in module_sources["power"]
    assert "power_low_power_mode_available" in header_sources["power"]
    assert "power_shutdown_command_available" in header_sources["power"]
    assert '"board_defaults"' in module_sources["power"]

    assert '"modules"' in backend_source
    assert '"intentional_noop"' in backend_source
    assert '"wake_word"' in backend_source
    assert '"stt_stream"' in backend_source
    assert '"assistant_client"' in backend_source
    assert '"telemetry"' in backend_source
    assert '"power"' in backend_source


def test_firmware_reports_playback_stop_word_capability_with_backend_fallback():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    wake_source = Path("firmware/components/endpoint_runtime/voice/wake_word.cpp").read_text()
    wake_header = Path("firmware/components/endpoint_runtime/voice/wake_word.h").read_text()
    micro_wake_source = FIRMWARE_MICRO_WAKE_ENGINE.read_text()
    tts_sources = FIRMWARE_TTS_PLAYER.read_text() + FIRMWARE_TTS_PLAYER_HA_VOICE_PE.read_text()

    assert "playback_stop_word_runtime_mode" in wake_header
    assert "playback_stop_word_on_device_available" in wake_header
    assert "playback_stop_word_active" in wake_header
    assert "playback_stop_word_unavailable_reason" in wake_header
    assert "inspect_local_keyword_frame" in wake_header
    assert '"endpoint_stop_keyword_experimental_with_backend_stt_interrupt_fallback"' in wake_source
    assert '"backend_stt_interrupt_with_stop_keyword_manifest"' in wake_source
    assert '"missing_micro_wake_word_inference_engine"' in micro_wake_source
    assert '"missing_stop_keyword_model_asset"' in micro_wake_source
    assert '"playback_interrupt"' in backend_source
    assert '"passive_placement_calibration"' in backend_source
    assert '"metrics_only_periodic_ambient"' in backend_source
    assert '"raw_audio_persisted", false' in backend_source
    assert '"playback_stop_word"' in backend_source
    assert '"available", true' in backend_source
    assert '"backend_stt_interrupt"' in backend_source
    assert '"stop_word", "stop"' in backend_source
    assert '"stop_event_type", "playback.stop"' in backend_source
    assert '"stop_reason", "voice_stop"' in backend_source
    assert '"backend_available", true' in backend_source
    assert '"backend_fallback", true' in backend_source
    assert '"backend_fallback_mode", "backend_stt_interrupt"' in backend_source
    assert '"local_keyword_configured"' in backend_source
    assert '"local_keyword_available"' in backend_source
    assert '"local_keyword_reason"' in backend_source
    assert '"keyword_model"' in backend_source
    assert '"reason", hexe::voice::playback_stop_word_unavailable_reason()' not in backend_source
    assert 'stop_playback("voice_stop")' not in tts_sources
    assert 'send_playback_event(\n        "playback.stop"' in tts_sources


def test_firmware_posts_passive_placement_calibration_metrics_only_samples():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    box_audio_source = Path("firmware/components/endpoint_runtime/board/audio.cpp").read_text()
    pe_audio_source = Path("firmware/components/endpoint_runtime/board/audio_ha_voice_pe.cpp").read_text()

    assert "placement_calibrations_status_url" in backend_source
    assert "/api/voice/placement-calibrations?endpoint_id=%s" in backend_source
    assert "placement_calibration_sample_url" in backend_source
    assert "/api/voice/placement-calibrations/%s/samples" in backend_source
    assert "apply_passive_placement_calibration_status" in backend_source
    assert "maybe_post_passive_placement_sample" in backend_source
    assert "observe_passive_placement_frame" in backend_source
    assert "ambient_rms" in backend_source
    assert "peak" in backend_source
    assert "clipping_ratio" in backend_source
    assert "speech_like_activity_ratio" in backend_source
    assert "sample_duration_ms" in backend_source
    assert "audio_b64" not in backend_source
    assert "observe_passive_placement_frame(samples, kFrameSamples, level, frame_has_voice)" in box_audio_source
    assert "observe_passive_placement_frame(g_mono_samples.data(), stereo_frames, level, frame_has_voice)" in pe_audio_source


def test_firmware_vad_keeps_listening_window_after_wake_word():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    source = FIRMWARE_AUDIO.read_text()
    pe_source = FIRMWARE_AUDIO_HA_VOICE_PE.read_text()

    assert "kVadSilenceHoldMs = 2500" in source
    assert 'finish_audio_stream("vad_silence")' in source
    assert "notify_vad_speech_started(level)" in source
    assert "notify_vad_speech_started(level)" in pe_source
    assert "kPostTtsInputIgnoreUs = 800000" in backend_source
    assert "kSessionResetInputIgnoreUs = 2000000" in backend_source
    assert "kPreWakeStreamTimeoutUs = 10000000" in backend_source
    assert "kAcceptedCaptureTimeoutUs = 15000000" in backend_source
    assert 'finish_audio_stream("capture_timeout")' in backend_source
    vad_event_body = backend_source[
        backend_source.index("bool send_vad_speech_started_event") : backend_source.index(
            "void send_audio_frame"
        )
    ]
    audio_frame_body = backend_source[
        backend_source.index("void send_audio_frame") : backend_source.index(
            "bool submit_audio_frame"
        )
    ]
    assert 'ensure_session_started("openwakeword")' not in vad_event_body
    assert 'ensure_session_started("openwakeword")' not in audio_frame_body
    assert "if (!g_session_started) {\n    remember_preroll_frame(frame);" in audio_frame_body
    assert "start_session_reset_input_cooldown();" in backend_source
    assert "start_post_tts_input_cooldown();" in backend_source
    assert "post_tts_input_cooldown_active()" in backend_source
    assert "g_preroll_count = 0" in backend_source
    assert "play_wake_accepted_sound();" in backend_source
    assert '"micro_vad"' in backend_source
    assert '"max_pause_ms", 3000' in backend_source
    assert '"energy_threshold", hexe::system::micro_vad_energy_threshold()' in backend_source
    assert '"max_energy_threshold", 20000' in backend_source
    assert "set_micro_vad_energy_threshold" in backend_source
    assert "hexe::voice::post_tts_input_cooldown_active()" in pe_source
    assert "micro_vad_chunk_active = false" in pe_source


def test_firmware_wake_election_candidate_wait_and_fallback_contract():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    wake_source = Path("firmware/components/endpoint_runtime/voice/wake_word.cpp").read_text()
    wake_header = Path("firmware/components/endpoint_runtime/voice/wake_word.h").read_text()
    backend_header = FIRMWARE_BACKEND_CLIENT.with_suffix(".h").read_text()

    assert "struct WakeCandidateMetrics" in backend_header
    assert "bool submit_wake_candidate(const WakeCandidateMetrics &candidate);" in backend_header
    assert "submit_wake_candidate(const WakeCandidateMetrics &candidate)" in backend_source
    assert 'append_event_header(envelope, "wake.candidate", g_session_id.c_str(), g_sequence++);' in backend_source
    assert 'envelope.append(rendered);\n  envelope.append("}");' in backend_source
    assert 'ensure_session_started("unknown")' in backend_source
    assert '"candidate_id"' in backend_source
    assert '"firmware_timeout_policy"' in backend_source
    assert "stream_after_timeout_backend_fallback" in backend_source
    assert '"backend_wake_fallback", true' in backend_source
    assert '"backend_openwakeword"' in backend_source
    assert '"ambient_level"' in backend_source
    assert '"snr_db"' in backend_source
    assert "g_wake_election_waiting = true" in backend_source
    assert "g_wake_accepted_for_session = true;\n    set_audio_streaming(true);" in backend_source
    assert "entered local listening mode before backend election" in backend_source
    assert "wake_election_wait_timed_out()" in backend_source
    assert "Wake election timed out; streaming buffered audio to backend fallback" in backend_source
    assert "if (g_wake_election_waiting && !g_wake_accepted_for_session) {\n    if (!wake_election_wait_timed_out()) {\n      remember_preroll_frame(frame);" in backend_source
    assert "reset_wake_election_state();" in backend_source
    assert "wake_word_election_capable" in wake_header
    assert "wake_word_candidate_source" in wake_header
    assert "kWakeElectionTimeoutMs = 300" in wake_source


def test_firmware_has_experimental_alexa_micro_wake_word_provider_hook():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    wake_source = Path("firmware/components/endpoint_runtime/voice/wake_word.cpp").read_text()
    wake_header = Path("firmware/components/endpoint_runtime/voice/wake_word.h").read_text()
    micro_wake_source = FIRMWARE_MICRO_WAKE_ENGINE.read_text()
    micro_wake_header = FIRMWARE_MICRO_WAKE_ENGINE_HEADER.read_text()
    cmake_source = FIRMWARE_CMAKE.read_text()
    component_manifest = Path("firmware/components/endpoint_runtime/idf_component.yml").read_text()
    audio_source = FIRMWARE_AUDIO.read_text()
    pe_audio_source = FIRMWARE_AUDIO_HA_VOICE_PE.read_text()

    assert "struct LocalKeywordModel" in wake_header
    assert "struct LocalKeywordDetection" in wake_header
    assert "inspect_wake_word_frame" in wake_header
    assert "MicroWakeEngineStatus" in micro_wake_header
    assert "init_micro_wake_engine" in micro_wake_source
    assert "process_micro_wake_frame" in micro_wake_source
    assert "tensorflow/lite/schema/schema_generated.h" in micro_wake_source
    assert "espressif/esp-tflite-micro" in component_manifest
    assert "esp-tflite-micro" in cmake_source
    assert '"voice/models/audio_preprocessor_int8.tflite"' in cmake_source
    assert '"voice/models/alexa.tflite"' in cmake_source
    assert '"voice/models/stop.tflite"' in cmake_source
    assert "HEXE_MICRO_WAKE_WORD_TFLM_ENABLED=1" in cmake_source
    assert "HEXE_MICRO_WAKE_WORD_FEATURE_FRONTEND_ENABLED=1" in cmake_source
    assert "_binary_audio_preprocessor_int8_tflite_start" in micro_wake_source
    assert "AudioPreprocessorOpResolver" in micro_wake_source
    assert "register_audio_preprocessor_ops" in micro_wake_source
    assert "FrontendProcessSamples" not in micro_wake_source
    assert "perform_streaming_inference" in micro_wake_source
    assert "detection_from_runtime" in micro_wake_source
    assert "probability_cutoff_as_uint8" in micro_wake_source
    assert "kMinWindowsBeforeDetection = 100" in micro_wake_source
    assert "kStreamingArenaMultiplier = 2" in micro_wake_source
    assert '"endpoint_micro_wake_word_experimental"' in wake_source
    assert '"github://esphome/micro-wake-word-models/models/v2/alexa.tflite@main"' in wake_source
    assert ".id =" not in wake_source
    assert 'cJSON_AddStringToObject(primary_model, "id", wake_model.id)' in backend_source
    assert 'cJSON_AddStringToObject(primary_model, "wake_word", wake_model.wake_word)' in backend_source
    assert 'cJSON_AddStringToObject(primary_model, "alias", wake_model.alias)' in backend_source
    assert 'cJSON_AddNumberToObject(primary_model, "tensor_arena_size", wake_model.tensor_arena_size)' in backend_source
    assert 'cJSON_AddNumberToObject(primary_model, "model_version", wake_model.model_version)' in backend_source
    assert 'cJSON_AddStringToObject(primary_model, "manifest_sha256", wake_model.manifest_sha256)' in backend_source
    assert 'cJSON_AddStringToObject(primary_model, "tflite_sha256", wake_model.tflite_sha256)' in backend_source
    assert 'cJSON_AddNumberToObject(primary_model, "tflite_size_bytes", wake_engine.wake_model_asset_bytes)' in backend_source
    assert '"micro_wake_engine"' in backend_source
    assert '"tflm_linked"' in backend_source
    assert '"feature_frontend_linked"' in backend_source
    assert '"feature_frontend_ready"' in backend_source
    assert '"model_asset_available"' in backend_source
    assert '"model_asset_bytes"' in backend_source
    assert '"model_runtime_ready"' in backend_source
    assert '"runtime_arena_bytes"' in backend_source
    assert '"feature_frame_count"' in backend_source
    assert '"inference_count"' in backend_source
    assert '"detection_count"' in backend_source
    assert '"last_probability_raw"' in backend_source
    assert '"last_probability"' in backend_source
    assert '"last_average_probability_raw"' in backend_source
    assert '"best_average_probability_raw"' in backend_source
    assert '"last_detection_probability_raw"' in backend_source
    assert "experimental_provider_configured" in backend_source
    for source in (audio_source, pe_audio_source):
        assert "inspect_local_keyword_frame" in source
        assert "local_keywords.wake" in source
        assert "WakeCandidateMetrics candidate" in source
        assert "candidate.endpoint_audio_profile_version = \"firmware_audio_v1\"" in source
        assert "Local wake detected" in source


def test_firmware_has_experimental_stop_keyword_provider_hook():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    wake_source = Path("firmware/components/endpoint_runtime/voice/wake_word.cpp").read_text()
    wake_header = Path("firmware/components/endpoint_runtime/voice/wake_word.h").read_text()
    audio_source = FIRMWARE_AUDIO.read_text()
    pe_audio_source = FIRMWARE_AUDIO_HA_VOICE_PE.read_text()

    assert "inspect_playback_stop_word_frame" in wake_header
    assert "inspect_local_keyword_frame" in wake_header
    assert "playback_stop_word_model" in wake_header
    assert "playback_stop_word_experimental_provider_configured" in wake_header
    assert '"stop"' in wake_source
    assert '"Stop"' in wake_source
    assert '"kahrendt_microWakeWord_stop_beta_20241017_5"' in wake_source
    assert '"https://github.com/kahrendt/microWakeWord/releases/download/stop/stop.json"' in wake_source
    assert '"https://github.com/kahrendt/microWakeWord/releases/download/stop/stop.tflite"' in wake_source
    assert '"Kevin Ahrendt"' in wake_source
    assert '"2024.7.0"' in wake_source
    assert "0.50f" in wake_source
    assert "21000" in wake_source
    assert "kStopKeywordModelEnd),\n       true}" in wake_source
    assert '"endpoint_stop_keyword_experimental_with_backend_stt_interrupt_fallback"' in wake_source
    assert 'cJSON_AddStringToObject(stop_keyword_model, "id", stop_model.id)' in backend_source
    assert 'cJSON_AddStringToObject(stop_keyword_model, "wake_word", stop_model.wake_word)' in backend_source
    assert 'cJSON_AddStringToObject(stop_keyword_model, "alias", stop_model.alias)' in backend_source
    assert 'cJSON_AddStringToObject(stop_keyword_model, "manifest_url", stop_model.manifest_url)' in backend_source
    assert 'cJSON_AddStringToObject(stop_keyword_model, "tflite_url", stop_model.tflite_url)' in backend_source
    assert 'cJSON_AddNumberToObject(stop_keyword_model, "probability_cutoff", stop_model.probability_cutoff)' in backend_source
    assert 'cJSON_AddNumberToObject(stop_keyword_model, "sliding_window_size", stop_model.sliding_window_size)' in backend_source
    assert 'cJSON_AddNumberToObject(stop_keyword_model, "tensor_arena_size", stop_model.tensor_arena_size)' in backend_source
    assert 'cJSON_AddNumberToObject(stop_keyword_model, "model_version", stop_model.model_version)' in backend_source
    assert 'cJSON_AddStringToObject(stop_keyword_model, "manifest_sha256", stop_model.manifest_sha256)' in backend_source
    for source in (audio_source, pe_audio_source):
        assert "Local stop detected" in source
    assert 'cJSON_AddStringToObject(stop_keyword_model, "tflite_sha256", stop_model.tflite_sha256)' in backend_source
    assert 'cJSON_AddNumberToObject(stop_keyword_model, "tflite_size_bytes", stop_engine.stop_model_asset_bytes)' in backend_source
    for source in (audio_source, pe_audio_source):
        assert "inspect_local_keyword_frame" in source
        assert "local_keywords.playback_stop" in source
        assert 'stop_playback("voice_stop")' in source
        assert 'cancel_active_session("voice_stop")' in source


def test_firmware_bundles_micro_wake_word_model_assets():
    wake_source = Path("firmware/components/endpoint_runtime/voice/wake_word.cpp").read_text()
    micro_wake_source = FIRMWARE_MICRO_WAKE_ENGINE.read_text()
    cmake_source = FIRMWARE_CMAKE.read_text()
    model_readme = (FIRMWARE_MICRO_WAKE_MODELS / "README.md").read_text()

    expected_assets = {
        "audio_preprocessor_int8.tflite": ("278949d197166fb8b580c0bdc94e902fb709fec0569dcf5766816b28285440e5", 8772),
        "alexa.json": ("1d999798b35b1fe2606465b75ab840be51c1811d2909d5e620cefb6e96f8abd0", 377),
        "alexa.tflite": ("9011a8155b04de858c48038529235cbc0e42e9fca05a55bf588cb80a653a723b", 55856),
        "stop.json": ("bd13aeb1b83852649dc4fb6135cb160ff68716d14612b06f6a405342c57447aa", 375),
        "stop.tflite": ("b5a18c4ad681a89950dfade31011e1631bdcb333e93c84519a1a63ff4f071146", 45744),
    }
    for filename, (expected_sha256, expected_size) in expected_assets.items():
        data = (FIRMWARE_MICRO_WAKE_MODELS / filename).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected_sha256
        assert len(data) == expected_size
        assert expected_sha256 in model_readme

    assert 'EMBED_FILES' in cmake_source
    assert '"voice/models/audio_preprocessor_int8.tflite"' in cmake_source
    assert '"voice/models/alexa.tflite"' in cmake_source
    assert '"voice/models/stop.tflite"' in cmake_source
    assert '_binary_alexa_tflite_start' in wake_source
    assert '_binary_stop_tflite_start' in wake_source
    assert "active_model_bundle_models(" in wake_source
    assert "init_micro_wake_engine(models, selected_model_count)" in wake_source
    assert 'wake_model_asset_bytes' in micro_wake_source
    assert 'stop_model_asset_bytes' in micro_wake_source


def test_firmware_model_bundle_activation_uses_ab_banks_and_embedded_fallback():
    bundle_source = FIRMWARE_MODEL_BUNDLE.read_text()
    bundle_header = FIRMWARE_MODEL_BUNDLE_HEADER.read_text()
    wake_source = Path("firmware/components/endpoint_runtime/voice/wake_word.cpp").read_text()
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    micro_wake_source = FIRMWARE_MICRO_WAKE_ENGINE.read_text()
    micro_wake_header = FIRMWARE_MICRO_WAKE_ENGINE_HEADER.read_text()
    cmake_source = FIRMWARE_CMAKE.read_text()
    docs = Path("docs/firmware-model-bundles.md").read_text()

    assert "enum class ModelBundleStorageKind" in bundle_header
    assert "struct ModelBundleCandidate" in bundle_header
    assert "struct ModelBundleState" in bundle_header
    assert "activate_model_bundle_candidate" in bundle_header
    assert "rollback_model_bundle" in bundle_header
    assert "active_model_bundle_models" in bundle_header

    assert 'constexpr char kActiveBankKey[] = "active_bank"' in bundle_source
    assert 'constexpr char kPreviousBankKey[] = "previous_bank"' in bundle_source
    assert 'esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS, label)' in bundle_source
    assert 'find_model_partition("model_a")' in bundle_source
    assert 'find_model_partition("model_b")' in bundle_source
    assert 'std::strcmp(bank, "model_a") == 0 || std::strcmp(bank, "model_b") == 0' in bundle_source
    assert 'std::strncmp(bank, "/sdcard/hexe/models/", 20)' in bundle_source
    assert "test_load_micro_wake_model_assets(candidate.models, candidate.model_count" in bundle_source
    assert bundle_source.index("test_load_micro_wake_model_assets(candidate.models, candidate.model_count") < bundle_source.index(
        "if (!commit_active_bundle_pointer("
    )
    assert "nvs_set_str(handle, kActiveBankKey, active_bank)" in bundle_source
    assert "nvs_set_str(handle, kPreviousBankKey, previous_bank)" in bundle_source
    assert "nvs_commit(handle)" in bundle_source
    assert "active_bundle_assets_not_loaded" in bundle_source
    assert "embedded_fallback" in bundle_source

    assert "init_model_bundle_manager();" in wake_source
    assert "active_model_bundle_models(" in wake_source
    assert "init_micro_wake_engine(models, selected_model_count)" in wake_source
    assert "test_load_micro_wake_model_assets" in micro_wake_header
    assert "missing_wake_model" in micro_wake_source
    assert "missing_stop_model" in micro_wake_source
    assert '"endpoint.model_bundle.activate"' in backend_source
    assert "ModelBundleCandidate candidate = {}" in backend_source
    assert "candidate.storage_kind = storage_kind" in backend_source
    assert "activate_model_bundle_candidate(candidate, activation_error" in backend_source
    assert '"model_bundle"' in backend_source
    assert '"active_bank"' in backend_source
    assert '"previous_bank"' in backend_source
    assert '"embedded_fallback"' in backend_source
    assert '"endpoint.model_bundle.rollback"' in backend_source
    assert '"voice/model_bundle.cpp"' in cmake_source
    assert "esp_partition" in cmake_source

    assert "internal A/B banks named `model_a` and `model_b`" in docs
    assert "atomic NVS updates of active and previous bundle pointers" in docs


def test_firmware_honors_wake_election_stand_down_without_command_ack():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert 'std::strcmp(type, "wake.election.result") == 0' in backend_source
    assert "wake_election_result_requests_stand_down(payload)" in backend_source
    assert 'cJSON_GetObjectItem(payload, "stand_down")' in backend_source
    assert "stand_down_wake_candidate(wake_election_stand_down_reason(payload));" in backend_source
    assert "reset_voice_session_state(false);" in backend_source
    assert "set_audio_streaming(false);" in backend_source[
        backend_source.index("void reset_voice_session_state") : backend_source.index("void mark_voice_socket_disconnected")
    ]
    assert "Wake election stand-down received: %s" in backend_source
    assert "wake.election.result" not in backend_source[
        backend_source.index("bool is_backend_command_event") : backend_source.index("void acknowledge_command_received")
    ]


def test_firmware_heartbeat_reports_wake_election_capabilities():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert '"election_capable"' in backend_source
    assert '"election_timeout_ms"' in backend_source
    assert '"candidate_event_type", "wake.candidate"' in backend_source
    assert '"stand_down_event_type", "wake.election.result"' in backend_source
    assert '"candidate_source", hexe::voice::wake_word_candidate_source()' in backend_source
    assert '"backend_fallback", true' in backend_source
    assert '"fallback_source", "backend_openwakeword"' in backend_source
    assert '"timeout_policy", kWakeElectionFallbackPolicy' in backend_source


def test_firmware_audio_queue_waits_for_connected_websocket_transport():
    source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert "bool voice_transport_ready()" in source
    assert "backend_ready_for_voice() && g_ws_client != nullptr && g_ws_connected" in source
    assert "esp_websocket_client_is_connected(g_ws_client)" in source
    assert "samples == nullptr || sample_count == 0 || !voice_transport_ready()" in source
    assert "if (!voice_transport_ready()) {\n    app_state.phase = hexe::idle_or_connecting_phase();" in source
    assert "if (!voice_transport_ready()) {\n    return false;" in source
    assert 'ESP_LOGW(kTag, "Dropping audio frame because transport queue is full");' in source


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
    assert "played && !request.loop ? hexe::PlaybackLifecycleState::kFinished" in pe_tts_source
    assert "set_playback_lifecycle(hexe::PlaybackLifecycleState::kStopped, false)" in pe_tts_source
    assert "mic_paused_for_playback = true" in audio_source
    assert "mic_paused_for_playback = false" in pe_audio_source
    assert "current_ip_address()" in source
    assert "wifi_rssi" in source


def test_firmware_supports_persisted_endpoint_provisioning_contract():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    settings_source = FIRMWARE_SETTINGS.read_text()
    settings_header = FIRMWARE_SETTINGS_HEADER.read_text()
    wifi_source = FIRMWARE_WIFI.read_text()
    tts_sources = FIRMWARE_TTS_PLAYER.read_text() + FIRMWARE_TTS_PLAYER_HA_VOICE_PE.read_text()

    assert "struct EndpointProvisioningSettings" in settings_header
    assert "endpoint_id[64]" in settings_header
    assert "backend_host[96]" in settings_header
    assert "wifi_ssid[33]" in settings_header
    assert "save_endpoint_provisioning" in settings_header
    assert "reset_endpoint_provisioning" in settings_header
    assert "provisioning_configured" in settings_header
    assert "kEndpointIdKey" in settings_source
    assert "kBackendHostKey" in settings_source
    assert "kWifiSsidKey" in settings_source
    assert "kProvisionedKey" in settings_source
    assert "hexe::config::kEndpointBackendHost" in settings_source
    assert "hexe::secrets::kWifiSsid" in settings_source
    assert "nvs_set_str(handle, kBackendHostKey" in settings_source
    assert "nvs_erase_key(handle, kProvisionedKey)" in settings_source
    assert "hexe::system::wifi_ssid()" in wifi_source
    assert "hexe::system::wifi_password()" in wifi_source
    assert "hexe::system::endpoint_backend_host()" in backend_source
    assert "hexe::system::endpoint_http_port()" in backend_source
    assert "hexe::system::endpoint_ws_port()" in backend_source
    assert "hexe::system::endpoint_id()" in backend_source
    assert '"%s://%s:%d%s%sendpoint_id=%s"' in backend_source
    assert "query_separator" in backend_source
    assert '"endpoint.provisioning.apply"' in backend_source
    assert '"endpoint.provisioning.reset"' in backend_source
    assert '"provisioning"' in backend_source
    assert '"runtime_configurable", true' in backend_source
    assert '"discovery"' in backend_source
    assert "hexe::system::endpoint_backend_host()" in tts_sources


def test_firmware_export_can_flash_text_provisioning_file_to_nvs():
    export_source = FIRMWARE_EXPORT_SCRIPT.read_text()
    partitions = Path("firmware/partitions/s3_16m_recovery_v1.csv").read_text()

    assert "provisioning.env.example" in export_source
    assert "provisioning-env-to-nvs-csv.py" in export_source
    assert 'PROVISIONING_ENV="${PROVISIONING_ENV:-provisioning.env}"' in export_source
    assert "nvs_partition_gen.py" in export_source
    assert 'generate "${PROVISIONING_CSV}" "${PROVISIONING_BIN}" 0x4000' in export_source
    assert 'FLASH_ARGS+=(0x9000 "${PROVISIONING_BIN}")' in export_source
    assert 'cd "${SCRIPT_DIR}"' in export_source
    for key in (
        "ENDPOINT_ID",
        "DISPLAY_NAME",
        "BACKEND_HOST",
        "HTTP_PORT",
        "WS_PORT",
        "USE_TLS",
        "WIFI_SSID",
        "WIFI_PASSWORD",
    ):
        assert key in export_source
    assert "nvs,        data, nvs,     0x9000,   16K," in partitions


def test_provisioning_env_to_nvs_csv_writes_firmware_settings(tmp_path):
    env_path = tmp_path / "provisioning.env"
    csv_path = tmp_path / "provisioning.csv"
    env_path.write_text(
        "\n".join(
            [
                "ENDPOINT_ID=esp-box-1",
                "DISPLAY_NAME=Kitchen Box",
                "BACKEND_HOST=10.0.0.100",
                "HTTP_PORT=9004",
                "WS_PORT=9004",
                "USE_TLS=false",
                "WIFI_SSID=HexeNet",
                "WIFI_PASSWORD='pass,with,commas'",
            ]
        ),
        encoding="utf-8",
    )

    subprocess.run(["python", str(FIRMWARE_PROVISIONING_CSV_TOOL), str(env_path), str(csv_path)], check=True)

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["key", "type", "encoding", "value"]
    assert ["hexe_settings", "namespace", "", ""] in rows
    assert ["endpoint_id", "data", "string", "esp-box-1"] in rows
    assert ["display_name", "data", "string", "Kitchen Box"] in rows
    assert ["backend_host", "data", "string", "10.0.0.100"] in rows
    assert ["http_port", "data", "i32", "9004"] in rows
    assert ["ws_port", "data", "i32", "9004"] in rows
    assert ["use_tls", "data", "u8", "0"] in rows
    assert ["wifi_ssid", "data", "string", "HexeNet"] in rows
    assert ["wifi_password", "data", "string", "pass,with,commas"] in rows
    assert ["provisioned", "data", "u8", "1"] in rows


def test_firmware_supports_udp_endpoint_discovery_and_pairing():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert "kDiscoverySchemaVersion" in backend_source
    assert "try_endpoint_discovery" in backend_source
    assert "SO_BROADCAST" in backend_source
    assert "255.255.255.255" in backend_source
    assert "g_discovery_status" in backend_source
    assert "kEndpointDiscoveryUdpPort" in backend_source
    assert "hexe::config::kEndpointDiscoveryEnabled" in backend_source
    assert "apply_discovery_offer" in backend_source
    assert "save_endpoint_provisioning(settings)" in backend_source
    assert '"discovery"' in backend_source


def test_operator_dashboard_exposes_endpoint_provisioning_flow():
    api_source = FRONTEND_API_CLIENT.read_text()
    dashboard_source = FRONTEND_ENDPOINT_DASHBOARD.read_text()

    assert "applyEndpointProvisioning" in api_source
    assert '"/api/endpoint/provisioning/apply"' in api_source
    assert "resetEndpointProvisioning" in api_source
    assert '"/api/endpoint/provisioning/reset"' in api_source
    assert "function EndpointProvisioningPanel" in dashboard_source
    assert "applyEndpointProvisioning(endpointId, payload)" in dashboard_source
    assert "resetEndpointProvisioning(endpointId)" in dashboard_source
    assert "provisioned_endpoint_id" in dashboard_source
    assert "Endpoint Settings" in dashboard_source
    assert "Apply Settings" in dashboard_source
    assert "Reset Settings" in dashboard_source
    assert "Discovery port" in dashboard_source
    assert "Discovery status" in dashboard_source


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
    assert "play_wav(audio, request, report_first_frame)" in player_source
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
    assert "local_wake_waiting_for_backend" in source
    assert 'if (wake_accepted ||\n      (g_wake_accepted_for_session && std::strcmp(ux_state, "listening") == 0) ||' in source
    assert '(local_wake_waiting_for_backend && std::strcmp(ux_state, "idle") == 0))' in source
    assert 'g_wake_accepted_for_session && std::strcmp(ux_state, "thinking") == 0' in source
    assert "if (g_wake_accepted_for_session) {\n      hexe::state().phase = hexe::AppPhase::kThinking;" in source
    assert "event_requests_followup_listen" in source
    assert "resume_audio_stream_for_followup" in source
    assert '"listen_timeout_ms"' in source
    assert 'std::strcmp(ux_state, "replying") == 0' in source
    assert "hexe::idle_or_connecting_phase()" in source
    assert 'std::strcmp(type, "endpoint.replay") == 0' in source
    assert 'std::strcmp(type, "endpoint.listen") == 0' in source
    assert 'start_voice_session("manual")' in source
    assert 'g_tts_playback_session_id = session_id->valuestring' in source
    assert 'std::strcmp(type, "playback.stop") == 0' in source
    assert "hexe::voice::stop_playback" in source


def test_firmware_has_source_agnostic_playback_stop_events():
    source = FIRMWARE_TTS_PLAYER.read_text()
    pe_source = FIRMWARE_TTS_PLAYER_HA_VOICE_PE.read_text()
    header = FIRMWARE_TTS_PLAYER_HEADER.read_text()

    assert "void stop_playback(const char *reason);" in header
    for player_source in (source, pe_source):
        assert "g_current_playback_request" in player_source
        assert '"playback.stop"' in player_source
        assert 'reason == nullptr ? "operator_stop" : reason' in player_source
        assert 'stop_playback("tts_stop")' in player_source


def test_firmware_replay_can_loop_audio_until_playback_stop():
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()
    header = FIRMWARE_TTS_PLAYER_HEADER.read_text()
    box_player = FIRMWARE_TTS_PLAYER.read_text()
    pe_player = FIRMWARE_TTS_PLAYER_HA_VOICE_PE.read_text()
    player_sources = box_player + pe_player

    assert "bool loop = false" in header
    assert "bool keep_microphone_open = false" in header
    assert 'cJSON_GetObjectItem(payload, "loop")' in backend_source
    assert 'cJSON_GetObjectItem(payload, "mic_mode")' in backend_source
    assert "cJSON_IsBool(loop) && cJSON_IsTrue(loop)" in backend_source
    assert '"interrupt_only"' in backend_source
    assert "bool loop{false};" in player_sources
    assert "bool keep_microphone_open{false};" in player_sources
    assert "request.loop = loop" in player_sources
    assert "request.keep_microphone_open = keep_microphone_open" in player_sources
    assert "request.keep_microphone_open ? false : hexe::board::pause_microphone_for_playback()" in player_sources
    assert "while (request.loop && played && !g_stop_requested" in box_player
    assert "while (loaded && !g_stop_requested && !state.muted)" in pe_player
    assert 'played && !request.loop ? hexe::PlaybackLifecycleState::kFinished' in player_sources


def test_firmware_buttons_stop_active_playback():
    box_buttons = FIRMWARE_BUTTONS.read_text()
    pe_buttons = FIRMWARE_BUTTONS_HA_VOICE_PE.read_text()

    assert '#include "voice/tts_player.h"' in box_buttons
    assert 'hexe::voice::stop_playback("config_button")' in box_buttons
    assert 'hexe::voice::stop_playback("mute_button")' in box_buttons
    assert 'hexe::voice::tts_playback_active() || app_state.phase' in box_buttons
    assert 'hexe::voice::stop_playback("voice_pe_center_button")' in pe_buttons
    assert 'hexe::voice::stop_playback("voice_pe_center_long_press")' in pe_buttons
    assert 'hexe::voice::stop_playback("hardware_mute_switch")' in pe_buttons
    assert 'hexe::voice::tts_playback_active() || state.phase' in pe_buttons


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


def test_firmware_wifi_disconnect_does_not_abort_during_ota_shutdown():
    source = FIRMWARE_WIFI.read_text()
    disconnect_handler = source[
        source.index("WIFI_EVENT_STA_DISCONNECTED") : source.index("if (event_base == IP_EVENT")
    ]

    assert "if (state.ota_active)" in disconnect_handler
    assert "Wi-Fi disconnected during OTA/update shutdown; reconnect skipped" in disconnect_handler
    assert "const esp_err_t reconnect_result = esp_wifi_connect();" in disconnect_handler
    assert "ESP_ERROR_CHECK(esp_wifi_connect());" not in disconnect_handler


def test_firmware_supports_home_assistant_voice_pe_profile():
    cmake_source = FIRMWARE_CMAKE.read_text()
    pe_profile_source = Path("firmware/boards/ha_voice_pe/board.yaml").read_text()
    audio_source = FIRMWARE_AUDIO_HA_VOICE_PE.read_text()
    buttons_source = FIRMWARE_BUTTONS_HA_VOICE_PE.read_text()
    display_source = FIRMWARE_DISPLAY_NONE.read_text()
    storage_source = FIRMWARE_STORAGE_NVS_ONLY.read_text()
    tts_source = FIRMWARE_TTS_PLAYER_HA_VOICE_PE.read_text()
    backend_source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert "HEXE_BOARD_PROFILE" in cmake_source
    assert "generate_board_profile_config.py" in cmake_source
    assert "${HEXE_BOARD_SRCS}" in cmake_source
    assert "HEXE_BOARD_PROFILE_ROOT" in cmake_source
    assert "- HEXE_BOARD_PROFILE_HA_VOICE_PE=1" in pe_profile_source
    assert "- board/audio_ha_voice_pe.cpp" in pe_profile_source
    assert "- board/buttons_ha_voice_pe.cpp" in pe_profile_source
    assert "- board/display_none.cpp" in pe_profile_source
    assert "- board/storage_nvs_only.cpp" in pe_profile_source
    assert "- voice/tts_player_ha_voice_pe.cpp" in pe_profile_source
    assert "- voice/tts_player.cpp" not in pe_profile_source
    assert "esp_driver_i2c" in cmake_source
    assert "esp_driver_i2s" in cmake_source

    assert "I2S_ROLE_SLAVE" in audio_source
    assert "I2S_DATA_BIT_WIDTH_32BIT" in audio_source
    assert "I2S_SLOT_MODE_STEREO" in audio_source
    assert "voice_channel_sample" in audio_source
    assert "pins::kVoicePeMicBclk" in audio_source
    assert "pins::kVoicePeMicLrclk" in audio_source
    assert "pins::kVoicePeMicDin" in audio_source
    assert "pins::kVoicePeVoiceKitReset" in audio_source
    assert "pins::kVoicePeI2cSda" in audio_source
    assert "pins::kVoicePeI2cScl" in audio_source
    assert "pins::kVoicePeSpeakerAmp" in tts_source
    assert "kVoiceKitI2cAddress = static_cast<uint8_t>(hexe::board::pins::kVoicePeVoiceKitI2cAddress)" in audio_source
    assert "- name: voice_kit\n          address: 66" in pe_profile_source
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
    assert "kMaxMicroVadPauseMs = 3000" in FIRMWARE_SETTINGS.read_text()
    assert "speech_peak_level" in audio_source
    assert "update_noise_floor" in audio_source
    assert "noise_floor_level" in backend_source
    assert "pre_roll_duration_ms" in backend_source
    assert "contains_pre_roll" in backend_source
    assert "contains_speech" in backend_source
    assert "std::array<int32_t, kFrameSamples * 2> g_raw_samples" in audio_source
    assert "std::array<int16_t, kFrameSamples> g_mono_samples" in audio_source
    assert 'xTaskCreate(vad_task, "hexe_vpe_vad", kVadTaskStackBytes' in audio_source
    assert "return g_voice_kit_ready;" in audio_source[audio_source.index("bool audio_output_ready()") :]

    assert "pins::kVoicePeCenterButton" in buttons_source
    assert "pins::kVoicePeHardwareMute" in buttons_source
    assert "hardware_mute_active" in buttons_source
    assert 'start_voice_session("button")' in buttons_source

    assert "Display disabled for this board profile" in display_source
    assert 'return "none";' in display_source
    assert "NVS storage initialized; SD media storage disabled" in storage_source
    assert "kAic3204I2cAddress = static_cast<uint8_t>(hexe::board::pins::kVoicePeSpeakerCodecI2cAddress)" in tts_source
    assert "- name: speaker_codec\n          address: 24" in pe_profile_source
    assert "pins::kVoicePeSpeakerLrclk" in tts_source
    assert "pins::kVoicePeSpeakerBclk" in tts_source
    assert "pins::kVoicePeSpeakerDout" in tts_source
    assert "kSpeakerSampleRate = 48000" in tts_source
    assert "kPlaybackDmaDescNum = 4" in tts_source
    assert "kPlaybackFrameCapacity = 192" in tts_source
    assert "I2S_ROLE_SLAVE" in tts_source
    assert "I2S_DATA_BIT_WIDTH_32BIT" in tts_source
    assert "I2S_SLOT_MODE_STEREO" in tts_source
    assert "heap_caps_get_free_size(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL)" in tts_source
    assert "i2s_del_channel(g_tx_channel)" in tts_source
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
    pe_profile_source = Path("firmware/boards/ha_voice_pe/board.yaml").read_text()
    noop_source = FIRMWARE_LED_RING.read_text()
    led_source = FIRMWARE_LED_RING_HA_VOICE_PE.read_text()
    doc_source = Path("docs/voice-pe-led-ring.md").read_text()

    assert "${HEXE_BOARD_SRCS}" in cmake_source
    assert "- board/led_ring_ha_voice_pe.cpp" in pe_profile_source
    assert "esp_driver_rmt" in cmake_source
    assert "init_led_ring();" in app_source
    assert "update_led_ring_patterns();" in app_source

    assert "kLedDataGpio = gpio_pin(hexe::board::pins::kVoicePeLedData)" in led_source
    assert "kLedPowerGpio = gpio_pin(hexe::board::pins::kVoicePeLedPower)" in led_source
    assert "kLedCount = hexe::board::pins::kVoicePeLedCount" in led_source
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

    assert "kDialA = gpio_pin(hexe::board::pins::kVoicePeDialA)" in buttons_source
    assert "kDialB = gpio_pin(hexe::board::pins::kVoicePeDialB)" in buttons_source
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
    assert "buildable_profiles" in build_source
    assert 'build_profile "${profile}"' in build_source
    assert "hexe_firmware_${1}.bin" in build_source
    assert 'hexe_${FIRMWARE_APP}_${1}.bin' in build_source
    assert '\\"filename\\":\\"${filename}\\"' in build_source
    assert "partition_csv_for_schema" in build_source
    assert "SDKCONFIG_DEFAULTS" in build_source
    assert 'GENERATED_COMPONENT_NAME="$(runtime_component_for_app "${FIRMWARE_EXPORT_FLAVOR}")' in build_source

    assert "PROFILE_APP_FILENAME" in export_source
    assert "hexe_firmware_${BOARD_PROFILE}.bin" in export_source
    assert "manifest-${BOARD_PROFILE}.json" in export_source
    assert "manifest-${FIRMWARE_APPLICATION_TYPE}-${BOARD_PROFILE}.json" in export_source
    assert 'GENERATED_DIR="${BUILD_DIR}/esp-idf/${GENERATED_COMPONENT_NAME}/generated"' in export_source
    assert 'BOARD_SOC="$(cmake_config_string HEXE_BOARD_SOC)"' in export_source
    assert "write_bin_checksums()" in export_source
    assert 'find . -maxdepth 1 -type f -name "*.bin"' in export_source
    assert 'sha256sum "${bin_files[@]}" > "${output}"' in export_source
    assert 'write_bin_checksums "${EXPORT_DIR}" SHA256SUMS' in export_source
    assert 'write_bin_checksums "${COMMON_EXPORT_DIR}" SHA256SUMS.profiles' in export_source
    assert "cp \"${APP_SRC}\" \"${COMMON_EXPORT_DIR}/${PROFILE_APP_FILENAME}\"" in export_source
    assert "${APP_PROFILE_MANIFEST_FILENAME}" in export_source
    assert '"application_type": "${FIRMWARE_APPLICATION_TYPE}"' in export_source
    assert '"soc": "${BOARD_SOC}"' in export_source
    assert '"partition_schema": "${BOARD_PARTITION_SCHEMA}"' in export_source
    assert '"firmware_api_version": "${FIRMWARE_API_VERSION}"' in export_source
    assert '"model_api_version": "${MODEL_API_VERSION}"' in export_source
    assert '"asset_api_version": "${ASSET_API_VERSION}"' in export_source
    assert '"calibration_schema_version": "${CALIBRATION_SCHEMA_VERSION}"' in export_source
    assert '"signature_scope": "ota_payload_signed_by_backend_at_delivery"' in export_source
