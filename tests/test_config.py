import logging

from hexevoice.config.settings import Settings
from hexevoice.main import configure_backend_logging


def test_settings_defaults():
    settings = Settings()
    assert settings.node_name == "hexevoice"
    assert settings.api_port == 9000
    assert settings.provider_id == "voice"


def test_onboarding_state_path_defaults_under_runtime_dir():
    settings = Settings()
    assert settings.resolved_onboarding_state_path().name == "onboarding_state.json"


def test_backend_log_path_defaults_under_runtime_logs():
    settings = Settings()
    assert settings.resolved_backend_log_path().as_posix() == "runtime/logs/hexevoice-backend.log"


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


def test_backend_logging_uses_midnight_archive(tmp_path):
    log_path = tmp_path / "logs" / "backend.log"
    settings = Settings(backend_log_path=log_path, backend_log_level="DEBUG", backend_log_backup_days=5)

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
