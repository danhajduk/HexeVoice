from hexevoice.api.models import ProviderSetupRequest
from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState
from hexevoice.providers.setup import ProviderSetupService, voice_provider_ids


def test_provider_setup_status_reports_blocker_until_provider_selected(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "resume": {
                    "current_step_id": "provider_setup",
                    "last_completed_step_id": "trust_activation",
                },
            }
        )
    )

    service = ProviderSetupService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )
    response = service.status_payload()

    assert response.configured is False
    assert response.supported_providers == ["voice"]
    assert response.blocking_reasons == ["provider_selection_required"]
    assert response.declaration_allowed is False


def test_provider_setup_surfaces_runtime_stt_and_tts_providers(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "provider_setup": {
                    "supported_providers": ["voice"],
                    "enabled_providers": ["voice"],
                    "default_provider": "voice",
                },
                "resume": {
                    "current_step_id": "provider_setup",
                    "last_completed_step_id": "trust_activation",
                },
            }
        )
    )
    settings = Settings(
        onboarding_state_path=tmp_path / "onboarding-state.json",
        voice_stt_provider="faster_whisper",
        voice_tts_provider="piper",
    )
    service = ProviderSetupService(settings=settings, onboarding_state_store=store)

    response = service.status_payload()

    assert voice_provider_ids(settings) == ["voice", "faster_whisper", "piper"]
    assert response.supported_providers == ["voice", "faster_whisper", "piper"]
    assert response.enabled_providers == ["voice"]


def test_provider_setup_save_advances_to_capability_declaration(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "resume": {
                    "current_step_id": "provider_setup",
                    "last_completed_step_id": "trust_activation",
                },
            }
        )
    )

    service = ProviderSetupService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )
    response = service.save_setup(
        payload=ProviderSetupRequest(
            enabled_providers=["voice"],
            default_provider="voice",
        )
    )
    persisted = store.load()

    assert response.configured is True
    assert response.enabled_providers == ["voice"]
    assert response.declaration_allowed is True
    assert persisted.provider_setup.default_provider == "voice"
    assert persisted.resume.current_step_id == "capability_declaration"
    assert persisted.resume.last_completed_step_id == "provider_setup"
