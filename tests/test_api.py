from fastapi.testclient import TestClient
import httpx

from hexevoice.api.models import AssistantTurnRequest
from hexevoice.assistant import AiNodeAssistantAdapter, LocalEchoAssistantAdapter
from hexevoice.main import create_app
from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def test_status_endpoint(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    response = client.get("/api/node/status")
    assert response.status_code == 200
    assert response.json()["trust_state"] == "untrusted"
    assert response.json()["lifecycle_state"] == "unconfigured"
    assert response.json()["current_step_id"] == "node_identity"
    assert response.json()["capability_status"] == "missing"
    assert response.json()["governance_sync_status"] == "pending_capability"


def test_standard_route_groups_exist(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    onboarding = client.get("/api/onboarding/status")
    assert onboarding.status_code == 200
    assert onboarding.json()["current_step_id"] == "node_identity"
    assert len(onboarding.json()["steps"]) == 10
    assert onboarding.json()["capability_setup"]["readiness_flags"]["trust_state_valid"] is False
    assert client.get("/api/onboarding/local-setup").status_code == 200
    assert client.get("/api/onboarding/bootstrap-discovery").status_code == 200
    assert client.post("/api/onboarding/session/start").status_code == 400
    assert client.post("/api/onboarding/session/poll").status_code == 400
    assert client.post("/api/onboarding/trust-activation/finalize").status_code == 400
    assert client.post("/api/onboarding/trust-status/refresh").status_code == 400
    assert client.get("/api/providers/setup").status_code == 200
    assert client.get("/api/endpoint/status/box-1").status_code == 404
    assert client.post("/api/endpoint/heartbeat", json={"endpoint_id": "box-1"}).status_code == 200
    assert client.get("/api/firmware/manifest").status_code in {200, 404}
    assert client.get("/api/capabilities").status_code == 200
    assert client.post("/api/capabilities/declaration").status_code == 400
    assert client.get("/api/governance/current").status_code == 400
    assert client.post("/api/governance/refresh").status_code == 400
    assert client.get("/api/governance/readiness").status_code == 200
    assert client.get("/api/node/operational-status").status_code == 400
    assert client.get("/api/services/status").status_code == 200
    assert client.get("/api/providers/voice/status").status_code == 200
    assistant_turn = client.post("/api/assistant/turn", json={"endpoint_id": "box-1", "text": "hello"})
    assert assistant_turn.status_code == 200
    assert assistant_turn.json()["heard_text"] == "hello"


def test_endpoint_heartbeat_records_latest_status(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    heartbeat = client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "device_state": "listening",
            "session_id": "session-voice-1",
            "firmware_version": "0.1.0",
            "ip_address": "10.0.0.55",
            "rssi_dbm": -58,
        },
    )

    assert heartbeat.status_code == 200
    heartbeat_payload = heartbeat.json()
    assert heartbeat_payload["accepted"] is True
    assert heartbeat_payload["endpoint_id"] == "esp-box-1"
    assert heartbeat_payload["device_state"] == "listening"
    assert heartbeat_payload["session_id"] == "session-voice-1"
    assert heartbeat_payload["last_seen_at"]

    status = client.get("/api/endpoint/status/esp-box-1")
    assert status.status_code == 200
    status_payload = status.json()
    assert status_payload["endpoint_id"] == "esp-box-1"
    assert status_payload["device_state"] == "listening"
    assert status_payload["session_id"] == "session-voice-1"
    assert status_payload["firmware_version"] == "0.1.0"
    assert status_payload["ip_address"] == "10.0.0.55"
    assert status_payload["rssi_dbm"] == -58


def test_firmware_manifest_serves_runtime_artifact(tmp_path):
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    (firmware_dir / "hexe_firmware.bin").write_bytes(b"firmware-bin")
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                firmware_artifact_dir=firmware_dir,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    manifest = client.get("/api/firmware/manifest")
    artifact = client.get("/api/firmware/artifacts/hexe_firmware.bin")

    assert manifest.status_code == 200
    assert manifest.json()["url"] == "http://voice-node.local:9004/api/firmware/artifacts/hexe_firmware.bin"
    assert manifest.json()["size_bytes"] == len(b"firmware-bin")
    assert artifact.status_code == 200
    assert artifact.content == b"firmware-bin"


def test_firmware_ota_push_sends_update_event_to_connected_endpoint(tmp_path):
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    (firmware_dir / "hexe_firmware.bin").write_bytes(b"firmware-bin")
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                firmware_artifact_dir=firmware_dir,
                public_api_base_url="http://voice-node.local:9004",
            )
        )
    )

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "session_id": "esp-box-1-1",
                "payload": {"firmware_version": "0.1.0"},
            }
        )
        websocket.receive_json()
        response = client.post(
            "/api/firmware/ota/push",
            json={"endpoint_id": "esp-box-1", "version": "0.1.1"},
        )
        event = websocket.receive_json()

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert event["event_type"] == "ota.update"
    assert event["endpoint_id"] == "esp-box-1"
    assert event["payload"]["url"] == "http://voice-node.local:9004/api/firmware/artifacts/hexe_firmware.bin"
    assert event["payload"]["version"] == "0.1.1"
    assert event["payload"]["size_bytes"] == len(b"firmware-bin")


def test_assistant_turn_echoes_transcript_without_ai(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "resume": {
                    "current_step_id": "ready",
                },
                "operational_status": {
                    "operational_ready": True,
                    "active_governance_version": "gov-1",
                    "governance_freshness_state": "fresh",
                },
            }
        )
    )
    client = TestClient(create_app(Settings(onboarding_state_path=state_path, node_name="kitchen-voice")))

    response = client.post("/api/assistant/turn", json={"endpoint_id": "box-1", "text": "Hexa, status"})

    assert response.status_code == 200
    assert response.json()["heard_text"] == "status"
    assert response.json()["command"] is None
    assert response.json()["handled_locally"] is False
    assert response.json()["reply_text"] == "I heard status, no AI added yet."
    assert response.json()["device_state"] == "speaking"


def test_assistant_turn_fallback_reply_uses_session_id_if_provided():
    client = TestClient(create_app(Settings(node_name="lab-voice")))

    response = client.post(
        "/api/assistant/turn",
        json={
            "endpoint_id": "box-9",
            "session_id": "session-abc",
            "text": "turn on the lights",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "session-abc"
    assert payload["handled_locally"] is False
    assert payload["reply_text"] == "I heard turn on the lights, no AI added yet."


def test_assistant_turn_can_route_to_configured_ai_node():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read()
        return httpx.Response(
            200,
            json={
                "endpoint_id": "box-9",
                "session_id": "session-abc",
                "heard_text": "turn on the lights",
                "reply_text": "AI Node heard turn on the lights.",
                "spoken_text": "AI Node heard turn on the lights.",
                "handled_locally": False,
                "command": None,
                "device_state": "speaking",
            },
        )

    adapter = AiNodeAssistantAdapter(
        base_url="https://ai-node.test",
        turn_path="/api/assistant/turn",
        timeout_s=5,
        fallback=LocalEchoAssistantAdapter(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    response = adapter.handle_turn(
        AssistantTurnRequest(endpoint_id="box-9", session_id="session-abc", text="turn on the lights"),
        session_id="session-abc",
    )

    assert captured["url"] == "https://ai-node.test/api/assistant/turn"
    assert b"turn on the lights" in captured["json"]
    assert response.reply_text == "AI Node heard turn on the lights."
    assert response.heard_text == "turn on the lights"
    assert adapter.status()["healthy"] is True


def test_assistant_ai_node_adapter_falls_back_to_local_echo_when_unconfigured():
    adapter = AiNodeAssistantAdapter(
        base_url=None,
        turn_path="/api/assistant/turn",
        timeout_s=5,
        fallback=LocalEchoAssistantAdapter(),
    )

    response = adapter.handle_turn(
        AssistantTurnRequest(endpoint_id="box-9", session_id="session-abc", text="turn on the lights"),
        session_id="session-abc",
    )

    assert response.reply_text == "I heard turn on the lights, no AI added yet."
    assert adapter.status()["healthy"] is False
    assert adapter.status()["last_error"] == "missing_ai_node_base_url"


def test_status_endpoint_reads_persisted_onboarding_state(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "onboarding_session": {
                    "session_id": "session-123",
                    "session_state": "approved",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "trust_status": "trusted",
                },
                "resume": {
                    "current_step_id": "provider_setup",
                },
            }
        )
    )

    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    response = client.get("/api/node/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["node_id"] == "node-voice-123"
    assert payload["trust_state"] == "trusted"
    assert payload["current_step_id"] == "provider_setup"
    assert payload["lifecycle_state"] == "capability_setup_pending"
    assert payload["capability_status"] == "missing"


def test_local_setup_endpoints_persist_node_identity_and_core_connection(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    identity_response = client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "1.0",
            "node_nonce": "voice-node-nonce",
            "hostname": "kitchen-voice.local",
            "api_base_url": "http://10.0.0.22:9000",
        },
    )
    assert identity_response.status_code == 200
    assert identity_response.json()["configured"] is True

    connection_response = client.put(
        "/api/onboarding/local-setup/core-connection",
        json={"core_base_url": "http://10.0.0.100:9001"},
    )
    assert connection_response.status_code == 200
    assert connection_response.json()["configured"] is True

    setup_state = client.get("/api/onboarding/local-setup")
    assert setup_state.status_code == 200
    assert setup_state.json()["node_identity"]["node_name"] == "kitchen-voice"
    assert setup_state.json()["core_connection"]["core_base_url"] == "http://10.0.0.100:9001/"

    status_response = client.get("/api/node/status")
    assert status_response.status_code == 200
    assert status_response.json()["node_name"] == "kitchen-voice"
    assert status_response.json()["current_step_id"] == "core_connection"
    assert status_response.json()["lifecycle_state"] == "bootstrap_connecting"


def test_restart_setup_clears_onboarding_state(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "1.0",
            "node_nonce": "voice-node-nonce",
        },
    )
    client.put(
        "/api/onboarding/local-setup/core-connection",
        json={"core_base_url": "http://10.0.0.100:9001"},
    )

    restart_response = client.post("/api/onboarding/restart")
    assert restart_response.status_code == 200
    assert restart_response.json()["node_identity"]["configured"] is False
    assert restart_response.json()["core_connection"]["configured"] is False

    status_response = client.get("/api/node/status")
    assert status_response.status_code == 200
    assert status_response.json()["current_step_id"] == "node_identity"
    assert status_response.json()["lifecycle_state"] == "unconfigured"


def test_bootstrap_discovery_advertisement_validation_advances_to_registration(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "1.0",
            "node_nonce": "voice-node-nonce",
        },
    )
    client.put(
        "/api/onboarding/local-setup/core-connection",
        json={"core_base_url": "http://10.0.0.100:9001"},
    )

    response = client.put(
        "/api/onboarding/bootstrap-discovery/advertisement",
        json={
            "topic": "hexe/bootstrap/core",
            "api_base": "http://10.0.0.100:9001",
            "mqtt_host": "10.0.0.100",
            "mqtt_port": 1884,
            "onboarding_mode": "api",
            "onboarding_contract": "global-node-v1",
            "onboarding_endpoints": {
                "register_session": "/api/system/nodes/onboarding/sessions",
                "registrations": "/api/system/nodes/registrations",
                "register": "/api/system/nodes/onboarding/sessions",
                "ai_node_register": "/api/system/ai-nodes/onboarding/sessions",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["advertisement_valid"] is True
    assert payload["onboarding_mode"] == "api"
    assert payload["onboarding_contract"] == "global-node-v1"

    status_response = client.get("/api/node/status")
    assert status_response.status_code == 200
    assert status_response.json()["current_step_id"] == "registration"
    assert status_response.json()["lifecycle_state"] == "registration_pending"


def test_onboarding_session_start_persists_core_session_metadata(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "1.0",
            "node_nonce": "voice-node-nonce",
            "hostname": "kitchen-voice.local",
            "api_base_url": "http://10.0.0.22:9000",
        },
    )
    client.put("/api/onboarding/local-setup/core-connection", json={"core_base_url": "http://10.0.0.100:9001"})
    client.put(
        "/api/onboarding/bootstrap-discovery/advertisement",
        json={
            "topic": "hexe/bootstrap/core",
            "api_base": "http://10.0.0.100:9001",
            "mqtt_host": "10.0.0.100",
            "mqtt_port": 1884,
            "onboarding_mode": "api",
            "onboarding_contract": "global-node-v1",
            "onboarding_endpoints": {
                "register_session": "/api/system/nodes/onboarding/sessions",
                "registrations": "/api/system/nodes/registrations",
            },
        },
    )

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "node_name": "kitchen-voice",
                "node_type": "voice-node",
                "node_software_version": "0.1.0",
                "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123&state=abc",
                "session_id": "session-123",
                "expires_at": "2026-04-08T01:00:00+00:00",
                "finalize": "/api/system/nodes/onboarding/sessions/session-123/finalize?node_nonce=voice-node-nonce",
            }

    def fake_post(url, json, timeout):
        assert url == "http://10.0.0.100:9001/api/system/nodes/onboarding/sessions"
        assert json["node_name"] == "kitchen-voice"
        assert json["protocol_version"] == "1.0"
        assert json["node_nonce"] == "voice-node-nonce"
        return DummyResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    response = client.post("/api/onboarding/session/start")
    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "session-123"
    assert payload["approval_url"].startswith("http://10.0.0.100/onboarding/nodes/approve")

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["session_id"] == "session-123"
    assert onboarding_status.json()["approval_url"].startswith("http://10.0.0.100/onboarding/nodes/approve")
    assert onboarding_status.json()["session_state"] == "pending"

    node_status = client.get("/api/node/status")
    assert node_status.status_code == 200
    assert node_status.json()["current_step_id"] == "approval"
    assert node_status.json()["lifecycle_state"] == "pending_approval"


def test_onboarding_session_poll_surfaces_approved_outcome(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    client.put(
        "/api/onboarding/local-setup/node-identity",
        json={
            "node_name": "kitchen-voice",
            "protocol_version": "1.0",
            "node_nonce": "voice-node-nonce",
            "hostname": "kitchen-voice.local",
            "api_base_url": "http://10.0.0.22:9000",
        },
    )
    client.put("/api/onboarding/local-setup/core-connection", json={"core_base_url": "http://10.0.0.100:9001"})
    client.put(
        "/api/onboarding/bootstrap-discovery/advertisement",
        json={
            "topic": "hexe/bootstrap/core",
            "api_base": "http://10.0.0.100:9001",
            "mqtt_host": "10.0.0.100",
            "mqtt_port": 1884,
            "onboarding_mode": "api",
            "onboarding_contract": "global-node-v1",
            "onboarding_endpoints": {
                "register_session": "/api/system/nodes/onboarding/sessions",
                "registrations": "/api/system/nodes/registrations",
            },
        },
    )

    class SessionStartResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "node_name": "kitchen-voice",
                "node_type": "voice-node",
                "node_software_version": "0.1.0",
                "approval_url": "http://10.0.0.100/onboarding/nodes/approve?sid=session-123&state=abc",
                "session_id": "session-123",
                "expires_at": "2026-04-08T01:00:00+00:00",
                "finalize": "/api/system/nodes/onboarding/sessions/session-123/finalize?node_nonce=voice-node-nonce",
            }

    class SessionPollResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "approved",
                "activation": {
                    "node_id": "node-voice-123",
                    "paired_core_id": "core-main",
                    "node_trust_token": "trust-token-123",
                    "baseline_policy_version": "2026.04",
                    "operational_mqtt_identity": "node-voice-123",
                    "operational_mqtt_host": "10.0.0.100",
                    "operational_mqtt_port": 1883,
                    "trust_status": "trusted",
                },
            }

    def fake_post(url, json, timeout):
        return SessionStartResponse()

    def fake_get(url, params, timeout):
        assert url == "http://10.0.0.100:9001/api/system/nodes/onboarding/sessions/session-123/finalize"
        assert params == {"node_nonce": "voice-node-nonce"}
        return SessionPollResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)

    start_response = client.post("/api/onboarding/session/start")
    assert start_response.status_code == 200

    poll_response = client.post("/api/onboarding/session/poll")
    assert poll_response.status_code == 200
    assert poll_response.json()["session_state"] == "approved"
    assert poll_response.json()["activation_received"] is True

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["session_state"] == "approved"
    assert onboarding_status.json()["last_terminal_outcome"] == "approved"
    assert onboarding_status.json()["current_step_id"] == "trust_activation"


def test_trust_activation_finalize_persists_trusted_state(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))

    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "onboarding_session": {
                    "session_id": "session-123",
                    "session_state": "approved",
                    "pending_activation": {
                        "node_id": "node-voice-123",
                        "node_type": "voice-node",
                        "paired_core_id": "core-main",
                        "node_trust_token": "trust-token-123",
                        "initial_baseline_policy": {"version": "2026.04"},
                        "baseline_policy_version": "2026.04",
                        "activation_profile": {"voice": {"default_provider": "mock"}},
                        "operational_mqtt_identity": "node-voice-123",
                        "operational_mqtt_token": "mqtt-token-123",
                        "operational_mqtt_host": "10.0.0.100",
                        "operational_mqtt_port": 1883,
                        "issued_at": "2026-04-08T01:00:00+00:00",
                        "source_session_id": "session-123",
                        "trust_status": "trusted",
                    },
                },
                "resume": {
                    "current_step_id": "trust_activation",
                    "last_completed_step_id": "approval",
                },
            }
        )
    )

    response = client.post("/api/onboarding/trust-activation/finalize")
    assert response.status_code == 200
    payload = response.json()
    assert payload["node_id"] == "node-voice-123"
    assert payload["trust_state"] == "trusted"
    assert payload["operational_mqtt_host"] == "10.0.0.100"

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["current_step_id"] == "provider_setup"
    assert onboarding_status.json()["trust_state"] == "trusted"

    node_status = client.get("/api/node/status")
    assert node_status.status_code == 200
    assert node_status.json()["node_id"] == "node-voice-123"
    assert node_status.json()["trust_state"] == "trusted"
    assert node_status.json()["current_step_id"] == "provider_setup"


def test_trust_status_refresh_surfaces_removed_state_and_reonboarding(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "node_trust_token": "trust-token-123",
                    "trust_status": "trusted",
                    "operational_mqtt_token": "mqtt-token-123",
                },
                "resume": {
                    "current_step_id": "ready",
                    "last_completed_step_id": "governance_sync",
                },
            }
        )
    )

    class TrustStatusResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "node_id": "node-voice-123",
                "trust_status": "revoked",
                "supported": False,
                "support_state": "removed",
                "registry_present": False,
                "registry_state": None,
                "revoked_at": "2026-04-08T02:00:00+00:00",
                "revocation_reason": "node_removed_by_admin",
                "revocation_action": "remove",
                "message": "This node was removed by Core and is no longer trusted.",
            }

    def fake_get(url, headers, timeout):
        assert url == "http://10.0.0.100:9001/api/system/nodes/trust-status/node-voice-123"
        assert headers == {"X-Node-Trust-Token": "trust-token-123"}
        return TrustStatusResponse()

    monkeypatch.setattr(httpx, "get", fake_get)

    response = client.post("/api/onboarding/trust-status/refresh")
    assert response.status_code == 200
    assert response.json()["support_state"] == "removed"
    assert response.json()["trust_state"] == "revoked"

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["current_step_id"] == "registration"
    assert onboarding_status.json()["trust_state"] == "revoked"
    assert onboarding_status.json()["support_state"] == "removed"
    assert "no longer trusted" in onboarding_status.json()["trust_message"]

    node_status = client.get("/api/node/status")
    assert node_status.status_code == 200
    assert node_status.json()["current_step_id"] == "registration"
    assert node_status.json()["blocking_reasons"] == ["node_removed_by_core", "re_onboarding_required"]


def test_provider_setup_enables_provider_and_advances_to_capability_declaration(tmp_path):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    store = OnboardingStateStore(path=state_path)
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

    response = client.put(
        "/api/providers/setup",
        json={
            "enabled_providers": ["voice"],
            "default_provider": "voice",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["declaration_allowed"] is True
    assert payload["enabled_providers"] == ["voice"]

    onboarding_status = client.get("/api/onboarding/status")
    assert onboarding_status.status_code == 200
    assert onboarding_status.json()["current_step_id"] == "capability_declaration"
    assert onboarding_status.json()["capability_setup"]["provider_selection"]["enabled"] == ["voice"]
    assert onboarding_status.json()["capability_setup"]["declaration_allowed"] is True

    capability_status = client.get("/api/capabilities")
    assert capability_status.status_code == 200
    assert capability_status.json()["configured"] == ["voice"]


def test_capability_declaration_governance_and_operational_status_flow(tmp_path, monkeypatch):
    state_path = tmp_path / "onboarding-state.json"
    client = TestClient(create_app(Settings(onboarding_state_path=state_path)))
    store = OnboardingStateStore(path=state_path)
    store.save(
        PersistedOnboardingState.model_validate(
            {
                "pre_trust": {
                    "node_name": "kitchen-voice",
                    "core_base_url": "http://10.0.0.100:9001",
                },
                "trust_activation": {
                    "node_id": "node-voice-123",
                    "node_type": "voice-node",
                    "node_trust_token": "trust-token-123",
                    "trust_status": "trusted",
                },
                "provider_setup": {
                    "supported_providers": ["voice"],
                    "enabled_providers": ["voice"],
                    "default_provider": "voice",
                    "declaration_allowed": True,
                    "blocking_reasons": [],
                },
                "resume": {
                    "current_step_id": "capability_declaration",
                    "last_completed_step_id": "provider_setup",
                },
            }
        )
    )

    class CapabilityResponse:
        status_code = 200
        def raise_for_status(self): return None
        def json(self):
            return {
                "acceptance_status": "accepted",
                "node_id": "node-voice-123",
                "manifest_version": "1.0",
                "accepted_at": "2026-04-08T03:00:00+00:00",
                "declared_capabilities": ["voice.inference"],
                "enabled_providers": ["voice"],
                "capability_profile_id": "profile-123",
                "governance_version": "gov-2026.04",
                "governance_issued_at": "2026-04-08T03:00:05+00:00",
            }

    class GovernanceCurrentResponse:
        status_code = 200
        def raise_for_status(self): return None
        def json(self):
            return {
                "node_id": "node-voice-123",
                "capability_profile_id": "profile-123",
                "governance_version": "gov-2026.04",
                "issued_timestamp": "2026-04-08T03:00:05+00:00",
                "refresh_interval_s": 3600,
                "governance_bundle": {"telemetry_requirements": {"interval_s": 60}},
            }

    class GovernanceRefreshResponseObj:
        status_code = 200
        def raise_for_status(self): return None
        def json(self):
            return {
                "updated": False,
                "governance_version": "gov-2026.04",
                "refresh_interval_s": 3600,
            }

    class OperationalStatusResponseObj:
        status_code = 200
        def raise_for_status(self): return None
        def json(self):
            return {
                "node_id": "node-voice-123",
                "lifecycle_state": "operational",
                "trust_status": "trusted",
                "capability_status": "accepted",
                "governance_status": "issued",
                "operational_ready": True,
                "active_governance_version": "gov-2026.04",
                "last_governance_issued_at": "2026-04-08T03:00:05+00:00",
                "last_governance_refresh_request_at": "2026-04-08T03:10:00+00:00",
                "governance_freshness_state": "fresh",
                "governance_freshness_changed_at": "2026-04-08T03:10:00+00:00",
                "governance_stale_for_s": 0,
                "governance_outdated": False,
                "last_telemetry_timestamp": "2026-04-08T03:11:00+00:00",
                "updated_at": "2026-04-08T03:11:00+00:00",
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        if url.endswith("/api/system/nodes/capabilities/declaration"):
            return CapabilityResponse()
        if url.endswith("/api/system/nodes/governance/refresh"):
            return GovernanceRefreshResponseObj()
        raise AssertionError(url)

    def fake_get(url, headers=None, params=None, timeout=None):
        if url.endswith("/api/system/nodes/governance/current"):
            return GovernanceCurrentResponse()
        if url.endswith("/api/system/nodes/operational-status/node-voice-123"):
            return OperationalStatusResponseObj()
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)

    declaration = client.post("/api/capabilities/declaration")
    assert declaration.status_code == 200
    assert declaration.json()["capability_status"] == "accepted"

    governance_current = client.get("/api/governance/current")
    assert governance_current.status_code == 200
    assert governance_current.json()["governance_version"] == "gov-2026.04"

    governance_refresh = client.post("/api/governance/refresh")
    assert governance_refresh.status_code == 200
    assert governance_refresh.json()["updated"] is False

    operational = client.get("/api/node/operational-status")
    assert operational.status_code == 200
    assert operational.json()["operational_ready"] is True

    node_status = client.get("/api/node/status")
    assert node_status.status_code == 200
    assert node_status.json()["current_step_id"] == "ready"
    assert node_status.json()["operational_ready"] is True
    assert node_status.json()["capability_status"] == "accepted"
    assert node_status.json()["governance_sync_status"] == "issued"
