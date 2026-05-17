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
    assert response.supported_providers == ["voice", "openwakeword"]
    assert response.blocking_reasons == ["provider_selection_required", "wake_provider_required"]
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

    assert voice_provider_ids(settings) == ["voice", "faster_whisper", "piper", "openwakeword"]
    assert response.supported_providers == ["voice", "faster_whisper", "piper", "openwakeword"]
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
    assert response.declaration_allowed is False
    assert response.blocking_reasons == ["wake_provider_required"]
    assert persisted.provider_setup.default_provider == "voice"
    assert persisted.resume.current_step_id == "provider_setup"
    assert persisted.resume.last_completed_step_id == "trust_activation"


def test_provider_setup_save_advances_after_required_runtime_providers_selected(tmp_path):
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
    settings = Settings(
        onboarding_state_path=tmp_path / "onboarding-state.json",
        voice_stt_provider="external_faster_whisper",
        voice_tts_provider="piper",
        voice_wake_provider="supervised_openwakeword",
    )
    service = ProviderSetupService(settings=settings, onboarding_state_store=store)

    response = service.save_setup(
        payload=ProviderSetupRequest(
            enabled_providers=["voice", "external_faster_whisper", "piper", "openwakeword"],
            default_provider="voice",
        )
    )
    persisted = store.load()

    assert response.declaration_allowed is True
    assert response.blocking_reasons == []
    assert response.enabled_providers == ["voice", "external_faster_whisper", "piper", "supervised_openwakeword"]
    assert persisted.resume.current_step_id == "capability_declaration"
    assert persisted.resume.last_completed_step_id == "provider_setup"


def test_provider_setup_saves_provider_configs_from_setup_request(tmp_path):
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
    settings = Settings(
        onboarding_state_path=tmp_path / "onboarding-state.json",
        voice_stt_provider="external_faster_whisper",
        voice_tts_provider="piper",
    )
    service = ProviderSetupService(settings=settings, onboarding_state_store=store)

    response = service.save_setup(
        payload=ProviderSetupRequest(
            enabled_providers=["voice", "external_faster_whisper", "piper", "openwakeword"],
            default_provider="voice",
            provider_configs={
                "external_faster_whisper": {
                    "profile": "cuda_fast_intent",
                    "model": "small.en",
                    "language": "en",
                    "device": "cuda",
                    "cuda_mode": "cuda",
                    "compute_type": "float16",
                },
                "piper": {
                    "default_voice": "en_US-kathleen-low",
                    "language": "en_US",
                    "warm_models": ["en_US-kathleen-low"],
                },
                "openwakeword": {
                    "default_wakeword": "Hexe",
                    "threshold": 0.65,
                },
            },
        )
    )

    assert response.provider_configs["external_faster_whisper"]["device"] == "cuda"
    assert response.provider_configs["external_faster_whisper"]["cuda_mode"] == "cuda"
    assert response.provider_configs["external_faster_whisper"]["language"] == "en"
    assert response.provider_configs["piper"]["default_voice"] == "en_US-kathleen-low"
    assert response.provider_configs["openwakeword"]["threshold"] == 0.65


def test_provider_setup_change_marks_accepted_capability_manifest_stale(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "provider_setup": {
                    "supported_providers": ["voice", "external_faster_whisper"],
                    "enabled_providers": ["voice", "external_faster_whisper"],
                    "default_provider": "voice",
                    "provider_configs": {
                        "external_faster_whisper": {
                            "model": "base.en",
                            "device": "cpu",
                        }
                    },
                    "declaration_allowed": True,
                    "blocking_reasons": [],
                },
                "capability_declaration": {
                    "capability_status": "accepted",
                    "declared_task_families": ["voice.inference"],
                    "declared_capabilities": ["voice.inference"],
                },
                "governance_sync": {
                    "governance_sync_status": "issued",
                    "governance_version": "gov-1",
                    "governance_bundle": {"policy": "ok"},
                },
                "operational_status": {
                    "capability_status": "accepted",
                    "governance_status": "issued",
                    "operational_ready": True,
                },
                "resume": {
                    "current_step_id": "capability_declaration",
                    "last_completed_step_id": "provider_setup",
                },
            }
        )
    )
    service = ProviderSetupService(
        settings=Settings(onboarding_state_path=state_path, voice_stt_provider="external_faster_whisper"),
        onboarding_state_store=store,
    )

    service.save_setup(
        payload=ProviderSetupRequest(
            enabled_providers=["voice", "external_faster_whisper"],
            default_provider="voice",
            provider_configs={
                "external_faster_whisper": {
                    "model": "small.en",
                    "device": "cpu",
                }
            },
        )
    )
    persisted = store.load()

    assert persisted.capability_declaration.capability_status == "manifest_stale"
    assert persisted.governance_sync.governance_sync_status == "pending_capability"
    assert persisted.governance_sync.governance_outdated is True
    assert persisted.operational_status.operational_ready is False
    assert persisted.operational_status.capability_status == "manifest_stale"
