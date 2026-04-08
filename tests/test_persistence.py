from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def test_onboarding_state_store_roundtrip(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    state = PersistedOnboardingState.model_validate(
        {
            "pre_trust": {
                "core_base_url": "http://10.0.0.100:9001",
                "node_nonce": "voice-node-nonce",
            },
            "onboarding_session": {
                "session_id": "session-123",
                "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123",
                "session_state": "pending",
            },
            "trust_activation": {
                "node_id": "node-voice-123",
                "paired_core_id": "core-main",
                "trust_status": "trusted",
                "operational_mqtt_host": "10.0.0.100",
                "operational_mqtt_port": 1883,
            },
            "resume": {
                "current_step_id": "provider_setup",
                "last_completed_step_id": "trust_activation",
            },
        }
    )

    saved = store.save(state)
    loaded = store.load()

    assert saved.updated_at
    assert loaded.pre_trust.core_base_url == "http://10.0.0.100:9001"
    assert loaded.onboarding_session.session_id == "session-123"
    assert loaded.trust_activation.node_id == "node-voice-123"
    assert loaded.normalized_current_step_id() == "provider_setup"
    assert loaded.bootstrap_discovery.bootstrap_topic == "hexe/bootstrap/core"
