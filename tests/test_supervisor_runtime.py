import asyncio

from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState
from hexevoice.runtime.service import NodeRuntimeService


class FakeSupervisorClient:
    def __init__(self, *, health=None):
        self.health_payload = health or {"status": "ok"}
        self.register_payloads = []
        self.heartbeat_payloads = []

    def health(self):
        return self.health_payload

    def register_runtime(self, payload):
        self.register_payloads.append(payload)
        return {"runtime": payload}

    def heartbeat_runtime(self, payload):
        self.heartbeat_payloads.append(payload)
        return {"runtime": payload}


def trusted_ready_store(path):
    store = OnboardingStateStore(path=path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "voice-node",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "capability_declaration": {
                    "capability_status": "accepted",
                },
                "governance_sync": {
                    "governance_sync_status": "issued",
                    "governance_freshness_state": "fresh",
                },
                "operational_status": {
                    "operational_ready": True,
                    "governance_freshness_state": "fresh",
                },
                "resume": {
                    "current_step_id": "ready",
                },
            }
        )
    )
    return store


def test_supervisor_runtime_registration_is_skipped_without_node_id(tmp_path):
    client = FakeSupervisorClient()
    service = NodeRuntimeService(
        settings=Settings(onboarding_state_path=tmp_path / "state.json"),
        supervisor_client=client,
    )

    result = asyncio.run(service.supervisor_heartbeat_once())

    assert result == {"status": "skipped", "reason": "missing_node_id"}
    assert client.register_payloads == []
    assert client.heartbeat_payloads == []


def test_supervisor_runtime_registers_before_heartbeat_with_core_contract_fields(tmp_path):
    client = FakeSupervisorClient()
    store = trusted_ready_store(tmp_path / "state.json")
    service = NodeRuntimeService(
        settings=Settings(
            public_api_base_url="http://10.0.0.100:9004",
            public_ui_base_url="http://10.0.0.100:8082",
        ),
        onboarding_state_store=store,
        supervisor_client=client,
    )

    result = asyncio.run(service.supervisor_heartbeat_once())

    assert result["status"] == "ok"
    assert len(client.register_payloads) == 1
    assert len(client.heartbeat_payloads) == 1

    registration = client.register_payloads[0]
    assert registration["node_id"] == "node-voice-123"
    assert registration["node_name"] == "voice-node"
    assert registration["node_type"] == "voice-node"
    assert registration["api_base_url"] == "http://10.0.0.100:9004"
    assert registration["ui_base_url"] == "http://10.0.0.100:8082"
    assert registration["desired_state"] == "running"
    assert registration["runtime_state"] == "running"
    assert registration["lifecycle_state"] == "running"
    assert registration["health_status"] == "healthy"
    assert registration["running"] is True
    assert "runtime_metadata" in registration

    heartbeat = client.heartbeat_payloads[0]
    assert heartbeat["node_id"] == "node-voice-123"
    assert heartbeat["api_base_url"] == "http://10.0.0.100:9004"
    assert heartbeat["ui_base_url"] == "http://10.0.0.100:8082"
    assert heartbeat["runtime_state"] == "running"
    assert heartbeat["lifecycle_state"] == "running"
    assert heartbeat["health_status"] == "healthy"
    assert heartbeat["running"] is True


def test_supervisor_runtime_registration_is_not_repeated_after_success(tmp_path):
    client = FakeSupervisorClient()
    store = trusted_ready_store(tmp_path / "state.json")
    service = NodeRuntimeService(
        settings=Settings(public_api_base_url="http://10.0.0.100:9004"),
        onboarding_state_store=store,
        supervisor_client=client,
    )

    assert asyncio.run(service.supervisor_heartbeat_once())["status"] == "ok"
    assert asyncio.run(service.supervisor_heartbeat_once())["status"] == "ok"

    assert len(client.register_payloads) == 1
    assert len(client.heartbeat_payloads) == 2
