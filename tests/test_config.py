from hexevoice.config.settings import Settings


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
