import httpx

from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState
from hexevoice.setup_reauth import SetupReauthService


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
