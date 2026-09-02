from pathlib import Path

from hexevoice.api.models import EndpointBleProvisionWifiRequest
from hexevoice.endpoint.ble_onboarding import EndpointBleOnboardingService
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


def trusted_store(tmp_path) -> OnboardingStateStore:
    store = OnboardingStateStore(path=tmp_path / "onboarding-state.json")
    state = PersistedOnboardingState.model_validate(
        {
            "pre_trust": {"core_base_url": "http://core.local"},
            "trust_activation": {
                "node_id": "voice-node-main",
                "node_trust_token": "node-token",
                "trust_status": "trusted",
            },
        }
    )
    store.save(state)
    return store


class FakeCoreClient:
    def __init__(self, *, status: str = "granted") -> None:
        self.status = status
        self.requested_payloads = []
        self.released = []

    def get_hardware_access_request_schema(self, *, core_base_url: str) -> dict:
        assert core_base_url == "http://core.local"
        return {"ok": True, "operations": ["ble.provision_wifi", "ble.scan", "ble.status"]}

    def get_voice_ble_provisioning_schema(self, *, core_base_url: str) -> dict:
        assert core_base_url == "http://core.local"
        return {
            "ok": True,
            "operation": "ble.provision_wifi",
            "node_profile_id": "voice",
            "schema_version": "1.0",
            "payload_schema": {"schema_id": "hexe.voice_node.wifi_backend.v1"},
            "encryption_model": {"key_agreement": "x25519-hkdf-sha256", "algorithm": "aes-256-gcm"},
        }

    def request_hardware_access(self, *, core_base_url: str, node_trust_token: str, payload: dict) -> dict:
        assert node_trust_token == "node-token"
        self.requested_payloads.append(payload)
        access_request = {
            "request_id": "hwreq-1",
            "lease_id": "lease-1" if self.status == "granted" else None,
            "lease_token": "secret-lease-token" if self.status == "granted" else None,
            "status": self.status,
            "adapter": payload.get("adapter"),
            "provisioning": payload.get("provisioning"),
        }
        if self.status == "denied":
            access_request["decision_reason"] = "bluetooth_policy_disabled"
        return {"ok": True, "access_request": access_request}

    def release_hardware_lease(self, *, core_base_url: str, node_trust_token: str, lease_id: str, node_id: str) -> dict:
        self.released.append({"lease_id": lease_id, "node_id": node_id})
        return {"ok": True, "access_request": {"status": "released", "lease_id": lease_id}}


class FakeSupervisorClient:
    def __init__(self, *, result: dict | None = None) -> None:
        self.result = result or {"ok": True, "status": "completed", "credential_payload": {"wifi_password": "[REDACTED]"}}
        self.calls = []

    def provision_ble_wifi(self, payload: dict) -> dict | None:
        self.calls.append(payload)
        return self.result


def request_payload(**overrides) -> EndpointBleProvisionWifiRequest:
    payload = {
        "target_node_id": "voice-endpoint-1",
        "onboarding_session_id": "ble-session-1",
        "endpoint_ephemeral_public_key": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE",
        "pairing_nonce": "nonce-123456",
        "sequence": 1,
        "adapter": "hci0",
        "target_address": "AA:BB:CC:DD:EE:FF",
        "backend_host": "hexe.local",
        "http_port": 9004,
        "ws_port": 9004,
        "use_tls": False,
        "wifi_ssid": "KitchenNet",
        "wifi_password": "correct-password",
        "display_name": "Kitchen Voice",
    }
    payload.update(overrides)
    return EndpointBleProvisionWifiRequest.model_validate(payload)


def test_ble_onboarding_granted_calls_supervisor_and_releases_lease(tmp_path):
    core = FakeCoreClient(status="granted")
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.provision_wifi(request_payload())

    assert response.ok is True
    assert response.status == "completed"
    assert core.requested_payloads[0]["operation"] == "ble.provision_wifi"
    assert core.requested_payloads[0]["provisioning"]["payload_schema_id"] == "hexe.voice_node.wifi_backend.v1"
    assert supervisor.calls[0]["lease_token"] == "secret-lease-token"
    assert supervisor.calls[0]["credential_payload"]["wifi_password"] == "correct-password"
    assert core.released == [{"lease_id": "lease-1", "node_id": "voice-node-main"}]
    assert response.access_request["lease_token"] == "[REDACTED]"
    assert response.credential_payload["wifi_password"] == "[REDACTED]"
    assert response.provisioning["endpoint_ephemeral_public_key"] == "[REDACTED]"


def test_ble_onboarding_pending_stops_before_supervisor_call(tmp_path):
    core = FakeCoreClient(status="pending")
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.provision_wifi(request_payload())

    assert response.ok is False
    assert response.status == "pending"
    assert supervisor.calls == []
    assert core.released == []


def test_ble_onboarding_denied_does_not_send_credentials_to_supervisor(tmp_path):
    core = FakeCoreClient(status="denied")
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.provision_wifi(request_payload())

    assert response.ok is False
    assert response.status == "denied"
    assert response.error == "bluetooth_policy_disabled"
    assert supervisor.calls == []
    assert response.credential_payload["wifi_password"] == "[REDACTED]"


def test_frontend_exposes_core_governed_ble_operator_flow():
    client_source = Path("frontend/src/api/client.js").read_text(encoding="utf-8")
    dashboard_source = Path("frontend/src/features/dashboard/VoiceEndpointDashboardSection.jsx").read_text(encoding="utf-8")

    assert "provisionEndpointBleWifi" in client_source
    assert '"/api/endpoint/ble/provision-wifi"' in client_source
    assert "EndpointBleOnboardingPanel" in dashboard_source
    assert "Core-Governed BLE" in dashboard_source
    assert "Provision over BLE" in dashboard_source
    assert "endpoint_ephemeral_public_key" in dashboard_source
    assert "wifi_password" in dashboard_source
