import logging
import json

from hexevoice.config.settings import Settings
from hexevoice.main import configure_backend_logging
from hexevoice.voice.records import record_voice_event


def test_settings_defaults():
    settings = Settings()
    assert settings.node_name == "hexevoice"
    assert settings.api_port == 9000
    assert settings.provider_id == "voice"


def test_onboarding_state_path_defaults_under_runtime_dir():
    settings = Settings()
    assert settings.resolved_onboarding_state_path().name == "onboarding_state.json"


def test_voice_intent_registry_path_follows_onboarding_state_dir(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json")
    assert settings.resolved_voice_intent_registry_path() == tmp_path / "voice_intents.json"


def test_backend_log_path_defaults_under_runtime_logs():
    settings = Settings()
    assert settings.resolved_backend_log_path().as_posix() == "runtime/logs/hexevoice-backend.log"


def test_voice_record_log_path_defaults_under_runtime_logs():
    settings = Settings()
    assert settings.resolved_voice_record_log_path().as_posix() == "runtime/logs/hexevoice-voice-records.log"


def test_faster_whisper_stt_settings_defaults():
    settings = Settings(voice_stt_provider="faster_whisper")

    assert settings.voice_stt_provider == "faster_whisper"
    assert settings.voice_stt_preload is True
    assert settings.voice_stt_faster_whisper_model == "base.en"
    assert settings.voice_stt_faster_whisper_device == "cpu"
    assert settings.voice_stt_faster_whisper_compute_type == "int8"
    assert settings.resolved_faster_whisper_temp_dir().as_posix() == "runtime/stt/faster-whisper"


def test_assistant_settings_default_to_local_echo():
    settings = Settings()

    assert settings.voice_assistant_provider == "local_echo"
    assert settings.voice_assistant_ai_node_base_url is None
    assert settings.voice_assistant_ai_node_turn_path == "/api/assistant/turn"
    assert settings.voice_assistant_timeout_s == 20.0
    assert settings.voice_conversation_context_turns == 6
    assert settings.voice_domain_events_enabled is True
    assert settings.voice_domain_events_mqtt_timeout_s == 5.0
    assert settings.voice_timer_announcements_enabled is True
    assert settings.voice_timer_success_mqtt_topic == "hexe/events/timer/create_succeeded"


def test_piper_tts_settings_default_to_supervised_local_service():
    settings = Settings(voice_tts_provider="piper")

    assert settings.voice_tts_provider == "piper"
    assert settings.voice_tts_piper_base_url is None
    assert settings.voice_tts_piper_service_host == "127.0.0.1"
    assert settings.voice_tts_piper_service_port == 10200
    assert settings.voice_tts_piper_synthesize_path == "/api/tts"
    assert settings.voice_tts_piper_voice is None
    assert settings.voice_tts_output_sample_rate_hz == 16000
    assert settings.piper_tts_warm_voices == ""
    assert settings.piper_tts_service_id == "piper_tts"
    assert settings.piper_tts_container_name == "hexevoice-piper-tts"
    assert settings.piper_tts_control_script.as_posix() == "scripts/piper-tts-control.sh"
    assert settings.resolved_voice_tts_piper_base_url() == "http://127.0.0.1:10200"
    assert settings.resolved_piper_tts_model_dir().as_posix() == "runtime/piper-tts/models"
    assert settings.resolved_piper_tts_warm_voices() == []


def test_piper_tts_explicit_base_url_overrides_service_host():
    settings = Settings(
        voice_tts_provider="piper",
        voice_tts_piper_base_url="http://piper.test:10200/",
    )

    assert settings.resolved_voice_tts_piper_base_url() == "http://piper.test:10200"


def test_tts_output_sample_rate_can_be_disabled_for_native_voices():
    settings = Settings(voice_tts_provider="piper", voice_tts_output_sample_rate_hz=0)

    assert settings.voice_tts_output_sample_rate_hz == 0


def test_backend_logging_uses_midnight_archive(tmp_path):
    log_path = tmp_path / "logs" / "backend.log"
    record_log_path = tmp_path / "logs" / "voice-records.log"
    settings = Settings(
        backend_log_path=log_path,
        voice_record_log_path=record_log_path,
        backend_log_level="DEBUG",
        backend_log_backup_days=5,
    )

    result = configure_backend_logging(settings)

    assert result == log_path
    handler = next(
        handler
        for handler in logging.getLogger("hexevoice").handlers
        if getattr(handler, "_hexevoice_backend_handler", False) and hasattr(handler, "when")
    )
    assert handler.when == "MIDNIGHT"
    assert handler.backupCount == 5
    assert log_path.exists()
    record_handler = next(
        handler
        for handler in logging.getLogger("hexevoice.voice.records").handlers
        if getattr(handler, "_hexevoice_voice_record_handler", False)
    )
    assert record_handler.when == "MIDNIGHT"
    assert record_handler.backupCount == 5
    assert record_log_path.exists()


def test_voice_records_write_json_lines_to_dedicated_log(tmp_path):
    record_log_path = tmp_path / "logs" / "voice-records.log"
    settings = Settings(backend_log_path=tmp_path / "logs" / "backend.log", voice_record_log_path=record_log_path)
    configure_backend_logging(settings)

    record_voice_event(
        "wake.accepted",
        endpoint_id="esp-box-1",
        session_id="voice-session-1",
        model="Hexa",
        confidence=1.0,
    )

    line = record_log_path.read_text().strip()
    payload = json.loads(line.split(": ", 1)[1])
    assert payload["event_type"] == "wake.accepted"
    assert payload["endpoint_id"] == "esp-box-1"
    assert payload["session_id"] == "voice-session-1"
    assert payload["model"] == "Hexa"
