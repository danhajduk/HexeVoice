import asyncio
import subprocess

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


class FakeCommandRunner:
    def __init__(self):
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        if command[:3] == ["docker", "inspect", "--format"]:
            return subprocess.CompletedProcess(command, 0, "running\n", "")
        return subprocess.CompletedProcess(command, 0, "ok\n", "")


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
    command_runner = FakeCommandRunner()
    store = trusted_ready_store(tmp_path / "state.json")
    service = NodeRuntimeService(
        settings=Settings(
            public_api_base_url="http://10.0.0.100:9004",
            public_ui_base_url="http://10.0.0.100:8082",
        ),
        onboarding_state_store=store,
        supervisor_client=client,
        service_command_runner=command_runner,
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
    services = registration["runtime_metadata"]["services"]
    openwakeword = next(service for service in services if service["service_id"] == "openwakeword")
    assert openwakeword["state"] == "running"
    assert openwakeword["managed_by"] == "core_supervisor_service_action_proxy"
    assert openwakeword["container_name"] == "hexevoice-openwakeword"
    assert not any(service["service_id"] == "piper_tts" for service in services)

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
    command_runner = FakeCommandRunner()
    store = trusted_ready_store(tmp_path / "state.json")
    service = NodeRuntimeService(
        settings=Settings(public_api_base_url="http://10.0.0.100:9004"),
        onboarding_state_store=store,
        supervisor_client=client,
        service_command_runner=command_runner,
    )

    assert asyncio.run(service.supervisor_heartbeat_once())["status"] == "ok"
    assert asyncio.run(service.supervisor_heartbeat_once())["status"] == "ok"

    assert len(client.register_payloads) == 1
    assert len(client.heartbeat_payloads) == 2


def test_openwakeword_service_status_and_action_use_control_script(tmp_path):
    command_runner = FakeCommandRunner()
    script = tmp_path / "openwakeword-control.sh"
    script.write_text("#!/usr/bin/env bash\n")
    service = NodeRuntimeService(
        settings=Settings(
            onboarding_state_path=tmp_path / "state.json",
            openwakeword_control_script=script,
        ),
        service_command_runner=command_runner,
    )

    status = service.service_status_payload()
    result = service.service_action(target="openwakeword", action="restart")

    assert status.openwakeword == "running"
    assert status.piper_tts == "disabled"
    assert result.accepted is True
    assert result.status == "running"
    assert [str(script), "restart"] in command_runner.commands
    assert any(component["component_id"] == "backend" for component in status.components)
    assert any(component["component_id"] == "stt" for component in status.components)
    assert status.resource_usage["process_cpu_percent"] >= 0
    assert status.supervisor["configured"] is False


def test_supervisor_runtime_registration_includes_piper_tts_when_configured(tmp_path):
    client = FakeSupervisorClient()
    command_runner = FakeCommandRunner()
    store = trusted_ready_store(tmp_path / "state.json")
    service = NodeRuntimeService(
        settings=Settings(
            public_api_base_url="http://10.0.0.100:9004",
            voice_tts_provider="piper",
            voice_tts_piper_voice="en_US-test",
            piper_tts_warm_voices="en_US-kathleen-low,en_US-hfc_female-medium",
        ),
        onboarding_state_store=store,
        supervisor_client=client,
        service_command_runner=command_runner,
    )

    result = asyncio.run(service.supervisor_heartbeat_once())

    assert result["status"] == "ok"
    registration = client.register_payloads[0]
    services = registration["runtime_metadata"]["services"]
    piper_tts = next(service for service in services if service["service_id"] == "piper_tts")
    assert piper_tts["service_name"] == "Piper TTS"
    assert piper_tts["state"] == "running"
    assert piper_tts["boot_order"] == 18
    assert piper_tts["managed_by"] == "core_supervisor_service_action_proxy"
    assert piper_tts["container_name"] == "hexevoice-piper-tts"
    assert piper_tts["control_script"] == "scripts/piper-tts-control.sh"
    assert piper_tts["base_url"] == "http://127.0.0.1:10200"
    assert piper_tts["synthesize_path"] == "/api/tts"
    assert piper_tts["voice"] == "en_US-test"
    assert piper_tts["warm_voices"] == ["en_US-kathleen-low", "en_US-hfc_female-medium"]
    assert service.service_status_payload().piper_tts == "running"


def test_piper_tts_status_model_uses_endpoint_voice_without_openai_fallback(tmp_path):
    command_runner = FakeCommandRunner()
    service = NodeRuntimeService(
        settings=Settings(
            onboarding_state_path=tmp_path / "state.json",
            voice_tts_provider="piper",
            voice_tts_endpoint_voices="esp-pe-1=en_GB-jenny_dioco-medium",
        ),
        service_command_runner=command_runner,
    )

    status = service.service_status_payload()
    tts_component = next(component for component in status.components if component["component_id"] == "tts")

    assert tts_component["provider"] == "piper"
    assert tts_component["model"] == "en_GB-jenny_dioco-medium"
    assert tts_component["model"] != "gpt-4o-mini-tts"


def test_piper_tts_service_action_uses_control_script_when_enabled(tmp_path):
    command_runner = FakeCommandRunner()
    script = tmp_path / "piper-tts-control.sh"
    script.write_text("#!/usr/bin/env bash\n")
    service = NodeRuntimeService(
        settings=Settings(
            onboarding_state_path=tmp_path / "state.json",
            voice_tts_provider="piper",
            piper_tts_control_script=script,
        ),
        service_command_runner=command_runner,
    )

    result = service.service_action(target="piper_tts", action="restart")

    assert result.accepted is True
    assert result.status == "running"
    assert [str(script), "restart"] in command_runner.commands


def test_tts_service_action_alias_restarts_piper_when_enabled(tmp_path):
    command_runner = FakeCommandRunner()
    script = tmp_path / "piper-tts-control.sh"
    script.write_text("#!/usr/bin/env bash\n")
    service = NodeRuntimeService(
        settings=Settings(
            onboarding_state_path=tmp_path / "state.json",
            voice_tts_provider="piper",
            piper_tts_control_script=script,
        ),
        service_command_runner=command_runner,
    )

    status = service.service_status_payload()
    result = service.service_action(target="tts", action="restart")

    tts_component = next(component for component in status.components if component["component_id"] == "tts")
    assert tts_component["restart_supported"] is True
    assert tts_component["restart_target"] == "piper_tts"
    assert result.accepted is True
    assert [str(script), "restart"] in command_runner.commands
