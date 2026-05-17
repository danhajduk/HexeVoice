import httpx

from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState
from hexevoice.setup_reauth import SetupReauthService
from hexevoice.setup_trust import SetupTrustRecoveryService
from hexevoice.config.settings import Settings


class FakeCoreClient:
    def __init__(self):
        self.node_nonce = None

    def start_reauth_session(self, *, core_base_url, payload):
        self.node_nonce = payload["node_nonce"]
        return {
            "reauth_status": "pending_approval",
            "approval_url": f"{core_base_url.rstrip('/')}/reauth/nodes/approve?rid=reauth-1&state=state-1",
            "finalize": {"path": "/api/system/nodes/reauth/sessions/reauth-1/finalize"},
        }

    def finalize_reauth_session(self, *, core_base_url, session_id, node_nonce):
        assert session_id == "reauth-1"
        assert node_nonce == self.node_nonce
        return {
            "status": "approved",
            "activation": {
                "node_id": "node-voice-123",
                "node_type": "voice-node",
                "paired_core_id": "core-1",
                "trust_status": "trusted",
                "node_trust_token": "fresh-trust-token",
                "operational_mqtt_token": "fresh-mqtt-token",
            },
        }


class OfflineCoreClient:
    def start_reauth_session(self, *, core_base_url, payload):
        raise httpx.ConnectError("core offline")

    def finalize_reauth_session(self, *, core_base_url, session_id, node_nonce):
        raise httpx.ConnectError("core offline")


def seed_migrated_state(path):
    store = OnboardingStateStore(path=path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {"core_base_url": "http://core.local:9001"},
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "reauth_required",
                    "node_trust_token": None,
                },
            }
        )
    )
    return store


def test_setup_reauth_start_and_finalize_saves_fresh_trust(tmp_path):
    store = seed_migrated_state(tmp_path / "onboarding-state.json")
    service = SetupReauthService(onboarding_state_store=store, core_client=FakeCoreClient())

    started = service.start()
    finalized = service.finalize()

    assert started.started is True
    assert started.session_id == "reauth-1"
    assert "reauth/nodes/approve" in (started.approval_url or "")
    assert finalized.approved is True
    assert finalized.trust_state == "trusted"
    persisted = store.load()
    assert persisted.trust_activation.node_trust_token == "fresh-trust-token"
    assert persisted.trust_activation.operational_mqtt_token == "fresh-mqtt-token"
    assert persisted.resume.current_step_id == "provider_setup"


def test_setup_reauth_core_offline_returns_retryable_status(tmp_path):
    store = seed_migrated_state(tmp_path / "onboarding-state.json")
    service = SetupReauthService(onboarding_state_store=store, core_client=OfflineCoreClient())

    started = service.start()

    assert started.started is False
    assert started.status == "core_unreachable"
    assert started.warnings
    assert store.load().trust_activation.trust_status == "reauth_required"


def test_setup_trust_recovery_clears_terminal_session(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "voice-node",
                    "protocol_version": "1.0",
                    "node_nonce": "nonce",
                    "core_base_url": "http://core.local:9001",
                },
                "bootstrap_discovery": {"advertisement_valid": True},
                "onboarding_session": {
                    "session_id": "session-1",
                    "approval_url": "http://core.local/approve",
                    "session_state": "expired",
                    "last_terminal_outcome": "expired",
                },
                "resume": {"current_step_id": "approval", "last_completed_step_id": "registration"},
            }
        )
    )
    service = SetupTrustRecoveryService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )

    response = service.run_action("clear-expired-sessions")

    assert response.accepted is True
    assert response.message == "expired_session_cleared"
    persisted = store.load()
    assert persisted.onboarding_session.session_id is None
    assert persisted.resume.current_step_id == "registration"


def test_setup_trust_recovery_restarts_onboarding_without_losing_core_setup(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "voice-node",
                    "protocol_version": "1.0",
                    "node_nonce": "nonce",
                    "core_base_url": "http://core.local:9001",
                },
                "bootstrap_discovery": {"advertisement_valid": True},
                "onboarding_session": {"session_id": "session-1", "session_state": "pending"},
                "trust_activation": {"node_id": "node-old", "trust_status": "reauth_required"},
                "resume": {"current_step_id": "approval", "last_completed_step_id": "registration"},
            }
        )
    )
    service = SetupTrustRecoveryService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )

    response = service.run_action("restart-onboarding")

    assert response.accepted is True
    persisted = store.load()
    assert persisted.pre_trust.core_base_url == "http://core.local:9001"
    assert persisted.onboarding_session.session_id is None
    assert persisted.trust_activation.trust_status == "untrusted"
    assert persisted.resume.current_step_id == "registration"


def test_setup_trust_recovery_reopens_core_approval(tmp_path):
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "onboarding_session": {
                    "session_id": "session-1",
                    "approval_url": "http://core.local/approve",
                    "session_state": "pending",
                },
            }
        )
    )
    service = SetupTrustRecoveryService(
        settings=Settings(onboarding_state_path=tmp_path / "onboarding-state.json"),
        onboarding_state_store=store,
    )

    response = service.run_action("reopen-core-approval")

    assert response.accepted is True
    assert response.approval_url == "http://core.local/approve"
