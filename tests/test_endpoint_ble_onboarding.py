from pathlib import Path
from datetime import datetime, timedelta, timezone

import httpx

from hexevoice.api.models import (
    EndpointBleIdentityRequest,
    EndpointBlePairingSessionApproveRequest,
    EndpointBlePairingSessionCancelRequest,
    EndpointBlePairingSessionStartRequest,
    EndpointBleProvisionWifiRequest,
    EndpointBleScanRequest,
    EndpointBleWifiCredentialsRequest,
)
from hexevoice.endpoint.ble_onboarding import EndpointBleOnboardingService
from hexevoice.persistence import EndpointRegistryRecord, EndpointRegistryStore, OnboardingStateStore, PersistedEndpointRegistry, PersistedOnboardingState


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
    def __init__(
        self,
        *,
        status: str = "granted",
        operations: list[str] | None = None,
        fleet_scan_result: dict | None = None,
        fleet_identity_result: dict | None = None,
        pairing_session: dict | None = None,
    ) -> None:
        self.status = status
        self.operations = operations or ["ble.provision_wifi", "ble.scan", "ble.read_identity", "ble.status"]
        self.requested_payloads = []
        self.released = []
        self.fleet_scan_result = fleet_scan_result
        self.fleet_identity_result = fleet_identity_result
        self.scan_payloads = []
        self.identity_payloads = []
        self.pairing_session = pairing_session or {
            "session_id": "blepair-test",
            "session_hint": "ABCD1234",
            "status": "waiting",
            "node_profile_id": "voice",
            "payload_schema_id": "hexe.voice_node.wifi_backend.v1",
            "claim_code_required": False,
            "adapter": "hci0",
            "expires_at": "2026-09-03T22:30:00+00:00",
            "supervisor_results": [{"supervisor_id": "sup-nearby", "status": "advertising", "adapter": "hci0"}],
        }
        self.pairing_calls = []

    def get_hardware_access_request_schema(self, *, core_base_url: str) -> dict:
        assert core_base_url == "http://core.local"
        return {"ok": True, "operations": self.operations}

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

    def scan_ble_devices(self, *, core_base_url: str, node_trust_token: str, payload: dict) -> dict:
        assert core_base_url == "http://core.local"
        assert node_trust_token == "node-token"
        self.scan_payloads.append(payload)
        if self.fleet_scan_result is None:
            request = httpx.Request("POST", f"{core_base_url}/api/system/nodes/hardware/bluetooth/ble/scan")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return self.fleet_scan_result

    def read_ble_identity(self, *, core_base_url: str, node_trust_token: str, payload: dict) -> dict:
        assert core_base_url == "http://core.local"
        assert node_trust_token == "node-token"
        self.identity_payloads.append(payload)
        if self.fleet_identity_result is None:
            request = httpx.Request("POST", f"{core_base_url}/api/system/nodes/hardware/bluetooth/ble/identity")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return self.fleet_identity_result

    def create_ble_pairing_session(self, *, core_base_url: str, node_trust_token: str, payload: dict) -> dict:
        assert core_base_url == "http://core.local"
        assert node_trust_token == "node-token"
        self.pairing_calls.append({"operation": "create", "payload": payload})
        session = dict(self.pairing_session)
        session.update({"status": "waiting", "adapter": payload.get("adapter")})
        self.pairing_session = session
        return {"ok": True, "pairing_session": session}

    def get_ble_pairing_session(self, *, core_base_url: str, node_trust_token: str, node_id: str, session_id: str, refresh: bool = True) -> dict:
        assert core_base_url == "http://core.local"
        assert node_trust_token == "node-token"
        assert node_id == "voice-node-main"
        self.pairing_calls.append({"operation": "get", "session_id": session_id, "refresh": refresh})
        return {"ok": True, "pairing_session": dict(self.pairing_session)}

    def approve_ble_pairing_session(self, *, core_base_url: str, node_trust_token: str, session_id: str, payload: dict) -> dict:
        assert core_base_url == "http://core.local"
        assert node_trust_token == "node-token"
        assert payload["node_id"] == "voice-node-main"
        self.pairing_calls.append({"operation": "approve", "session_id": session_id, "payload": payload})
        session = dict(self.pairing_session)
        session.update({"status": "approved", "approved_device_id": payload["device_id"]})
        self.pairing_session = session
        return {"ok": True, "pairing_session": session}

    def cancel_ble_pairing_session(self, *, core_base_url: str, node_trust_token: str, session_id: str, payload: dict) -> dict:
        assert core_base_url == "http://core.local"
        assert node_trust_token == "node-token"
        assert payload["node_id"] == "voice-node-main"
        self.pairing_calls.append({"operation": "cancel", "session_id": session_id, "payload": payload})
        session = dict(self.pairing_session)
        session.update({"status": "canceled"})
        self.pairing_session = session
        return {"ok": True, "pairing_session": session}


class FakeSupervisorClient:
    def __init__(self, *, result: dict | None = None) -> None:
        self.result = result or {"ok": True, "status": "completed", "credential_payload": {"wifi_password": "[REDACTED]"}}
        self.calls = []
        self.scan_result = {
            "ok": True,
            "operation": "ble.scan",
            "adapter": "hci0",
            "service_uuid": "7f9c0000-5f04-4d8b-9a46-7c0f7a100000",
            "scan_seconds": 60,
            "devices": [
                {
                    "address": "11:22:33:44:55:66",
                    "name": "Other Sensor",
                    "transport": "ble",
                    "service_uuid_match": False,
                },
                {
                    "address": "AA:BB:CC:DD:EE:FF",
                    "name": "Hexe Voice PE",
                    "transport": "ble",
                    "uuids": ["7f9c0000-5f04-4d8b-9a46-7c0f7a100000"],
                    "service_uuid_match": True,
                    "matched_service_uuid": "7f9c0000-5f04-4d8b-9a46-7c0f7a100000",
                },
            ],
            "matching_devices": [
                {
                    "address": "AA:BB:CC:DD:EE:FF",
                    "name": "Hexe Voice PE",
                    "transport": "ble",
                    "uuids": ["7f9c0000-5f04-4d8b-9a46-7c0f7a100000"],
                    "service_uuid_match": True,
                    "matched_service_uuid": "7f9c0000-5f04-4d8b-9a46-7c0f7a100000",
                }
            ],
        }
        self.scan_calls = []
        self.identity_calls = []

    def provision_ble_wifi(self, payload: dict) -> dict | None:
        self.calls.append(payload)
        return self.result

    def scan_ble(self, payload: dict) -> dict | None:
        self.scan_calls.append(payload)
        return self.scan_result

    def read_ble_identity(self, payload: dict) -> dict | None:
        self.identity_calls.append(payload)
        return {
            "ok": True,
            "operation": "ble.read_identity",
            "adapter": "hci0",
            "target_address": payload["target_address"],
            "onboarding": {
                "target_node_id": "voice-endpoint-1",
                "onboarding_session_id": "recovery-ble-1234",
                "pairing_nonce": "nonce-123456",
                "board_profile": "ha_voice_pe",
                "provisioning_mode": "local_recovery",
            },
        }


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


def scan_payload(**overrides) -> EndpointBleScanRequest:
    payload = {
        "adapter": "hci0",
        "scan_seconds": 60,
    }
    payload.update(overrides)
    return EndpointBleScanRequest.model_validate(payload)


def identity_payload(**overrides) -> EndpointBleIdentityRequest:
    payload = {
        "adapter": "hci0",
        "target_address": "AA:BB:CC:DD:EE:FF",
        "timeout_s": 20,
    }
    payload.update(overrides)
    return EndpointBleIdentityRequest.model_validate(payload)


def test_ble_scan_uses_core_fleet_scan_when_available(tmp_path):
    core = FakeCoreClient(
        fleet_scan_result={
            "ok": True,
            "status": "partial",
            "operation": "ble.scan",
            "mode": "fleet",
            "node_id": "voice-node-main",
            "service_uuid": "7f9c0000-5f04-4d8b-9a46-7c0f7a100000",
            "scan_seconds": 60,
            "matching_devices": [
                {
                    "address": "AA:BB:CC:DD:EE:FF",
                    "name": "Hexe Voice PE",
                    "transport": "ble",
                    "service_uuid_match": True,
                    "matched_service_uuid": "7f9c0000-5f04-4d8b-9a46-7c0f7a100000",
                    "supervisor_id": "sup-nearby",
                }
            ],
            "devices": [
                {
                    "address": "AA:BB:CC:DD:EE:FF",
                    "name": "Hexe Voice PE",
                    "transport": "ble",
                    "service_uuid_match": True,
                    "matched_service_uuid": "7f9c0000-5f04-4d8b-9a46-7c0f7a100000",
                    "supervisor_id": "sup-nearby",
                }
            ],
            "supervisor_results": [],
        }
    )
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.scan(scan_payload())

    assert response.ok is True
    assert response.status == "completed"
    assert response.supervisor_result["mode"] == "fleet"
    assert core.scan_payloads == [
        {
            "node_id": "voice-node-main",
            "adapter": "hci0",
            "service_uuid": "7f9c0000-5f04-4d8b-9a46-7c0f7a100000",
            "scan_seconds": 60,
            "reason": "Discover nearby BLE endpoints",
        }
    ]
    assert core.requested_payloads == []
    assert core.released == []
    assert supervisor.scan_calls == []
    assert response.devices[0]["supervisor_id"] == "sup-nearby"


def test_ble_scan_granted_calls_supervisor_and_releases_lease(tmp_path):
    core = FakeCoreClient(status="granted")
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.scan(scan_payload())

    assert response.ok is True
    assert response.status == "completed"
    assert core.requested_payloads[0]["operation"] == "ble.scan"
    assert core.requested_payloads[0]["resource_type"] == "bluetooth"
    assert supervisor.scan_calls == [
        {
            "node_id": "voice-node-main",
            "lease_token": "secret-lease-token",
            "adapter": "hci0",
            "service_uuid": "7f9c0000-5f04-4d8b-9a46-7c0f7a100000",
            "scan_seconds": 60,
        }
    ]
    assert core.released == [{"lease_id": "lease-1", "node_id": "voice-node-main"}]
    assert response.access_request["lease_token"] == "[REDACTED]"
    assert response.service_uuid == "7f9c0000-5f04-4d8b-9a46-7c0f7a100000"
    assert response.devices == [
        {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "Hexe Voice PE",
            "transport": "ble",
            "uuids": ["7f9c0000-5f04-4d8b-9a46-7c0f7a100000"],
            "service_uuid_match": True,
            "matched_service_uuid": "7f9c0000-5f04-4d8b-9a46-7c0f7a100000",
        }
    ]


def test_ble_scan_falls_back_to_local_when_core_fleet_has_no_bluetooth_supervisors(tmp_path):
    core = FakeCoreClient(
        status="granted",
        fleet_scan_result={
            "ok": False,
            "status": "failed",
            "operation": "ble.scan",
            "mode": "fleet",
            "supervisor_count": 0,
            "completed_supervisor_count": 0,
            "matching_devices": [],
            "devices": [],
            "supervisor_results": [],
            "error": "bluetooth_supervisor_unavailable",
        },
    )
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.scan(scan_payload())

    assert response.ok is True
    assert core.scan_payloads
    assert core.requested_payloads[0]["operation"] == "ble.scan"
    assert supervisor.scan_calls


def test_ble_scan_pending_stops_before_supervisor_call(tmp_path):
    core = FakeCoreClient(status="pending")
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.scan(scan_payload())

    assert response.ok is False
    assert response.status == "pending"
    assert supervisor.scan_calls == []
    assert core.released == []


def test_ble_scan_rejects_overlong_scan_window():
    try:
        scan_payload(scan_seconds=61)
    except ValueError as exc:
        assert "less than or equal to 60" in str(exc)
    else:
        raise AssertionError("expected scan_seconds over 60 to be rejected")


def test_ble_identity_uses_core_fleet_identity_when_available(tmp_path):
    core = FakeCoreClient(
        fleet_identity_result={
            "ok": True,
            "status": "completed",
            "operation": "ble.read_identity",
            "mode": "fleet",
            "node_id": "voice-node-main",
            "target_address": "AA:BB:CC:DD:EE:FF",
            "identity": {
                "target_node_id": "voice-endpoint-1",
                "onboarding_session_id": "recovery-ble-1234",
                "pairing_nonce": "nonce-123456",
                "board_profile": "ha_voice_pe",
                "provisioning_mode": "local_recovery",
            },
            "supervisor_results": [],
        }
    )
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.read_identity(identity_payload())

    assert response.ok is True
    assert response.status == "completed"
    assert response.supervisor_result["mode"] == "fleet"
    assert response.identity["board_profile"] == "ha_voice_pe"
    assert response.identity["onboarding_session_id"] == "recovery-ble-1234"
    assert response.identity["pairing_nonce"] == "nonce-123456"
    assert core.identity_payloads == [
        {
            "node_id": "voice-node-main",
            "adapter": "hci0",
            "target_address": "AA:BB:CC:DD:EE:FF",
            "timeout_s": 20,
            "reason": "Read BLE endpoint onboarding identity",
        }
    ]
    assert core.requested_payloads == []
    assert supervisor.identity_calls == []


def test_ble_identity_fallback_reads_supervisor_and_releases_lease(tmp_path):
    core = FakeCoreClient(status="granted")
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.read_identity(identity_payload(supervisor_id="sup-nearby"))

    assert response.ok is True
    assert response.status == "completed"
    assert core.requested_payloads[0]["operation"] == "ble.read_identity"
    assert core.requested_payloads[0]["resource_type"] == "bluetooth"
    assert core.requested_payloads[0]["supervisor_id"] == "sup-nearby"
    assert supervisor.identity_calls == [
        {
            "node_id": "voice-node-main",
            "lease_token": "secret-lease-token",
            "adapter": "hci0",
            "target_address": "AA:BB:CC:DD:EE:FF",
            "timeout_s": 20,
        }
    ]
    assert core.released == [{"lease_id": "lease-1", "node_id": "voice-node-main"}]
    assert response.access_request["lease_token"] == "[REDACTED]"
    assert response.identity["board_profile"] == "ha_voice_pe"
    assert response.identity["pairing_nonce"] == "nonce-123456"


def test_ble_identity_falls_back_to_local_when_core_fleet_has_no_bluetooth_supervisors(tmp_path):
    core = FakeCoreClient(
        status="granted",
        fleet_identity_result={
            "ok": False,
            "status": "failed",
            "operation": "ble.read_identity",
            "mode": "fleet",
            "supervisor_count": 0,
            "completed_supervisor_count": 0,
            "identity": {},
            "supervisor_results": [],
            "error": "bluetooth_supervisor_unavailable",
        },
    )
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.read_identity(identity_payload())

    assert response.ok is True
    assert core.identity_payloads
    assert core.requested_payloads[0]["operation"] == "ble.read_identity"
    assert supervisor.identity_calls
    assert response.identity["board_profile"] == "ha_voice_pe"


def test_ble_pairing_session_lifecycle_uses_node_trust_and_redacts_identity(tmp_path):
    core = FakeCoreClient(
        pairing_session={
            "session_id": "blepair-test",
            "session_hint": "ABCD1234",
            "status": "found",
            "node_profile_id": "voice",
            "payload_schema_id": "hexe.voice_node.wifi_backend.v1",
            "adapter": "hci0",
            "expires_at": "2026-09-03T22:30:00+00:00",
            "endpoint_identity": {
                "device_id": "esp-pe-1",
                "target_node_id": "esp-pe-1",
                "board_profile": "ha_voice_pe",
                "firmware_version": "min-fw-test",
                "application_type": "recovery",
                "provisioning_mode": "core_published_pairing",
                "endpoint_ephemeral_public_key": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE",
                "pairing_nonce": "nonce-123456",
            },
        }
    )
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=FakeSupervisorClient(),
    )

    created = service.start_pairing_session(EndpointBlePairingSessionStartRequest(adapter="hci0", duration_s=300))
    core.pairing_session = {
        **core.pairing_session,
        "status": "found",
        "endpoint_identity": {
            "device_id": "esp-pe-1",
            "target_node_id": "esp-pe-1",
            "board_profile": "ha_voice_pe",
            "firmware_version": "min-fw-test",
            "application_type": "recovery",
            "provisioning_mode": "core_published_pairing",
            "endpoint_ephemeral_public_key": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE",
            "pairing_nonce": "nonce-123456",
        },
    }
    found = service.get_pairing_session("blepair-test")
    approved = service.approve_pairing_session("blepair-test", EndpointBlePairingSessionApproveRequest(device_id="esp-pe-1"))
    canceled = service.cancel_pairing_session("blepair-test", EndpointBlePairingSessionCancelRequest(operator_reason="closed"))

    assert created.status == "waiting"
    assert core.pairing_calls[0]["payload"]["node_id"] == "voice-node-main"
    assert core.pairing_calls[0]["payload"]["payload_schema_id"] == "hexe.voice_node.wifi_backend.v1"
    assert found.status == "found"
    assert found.ui_state == "ready_to_provision"
    assert found.identity["device_id"] == "esp-pe-1"
    assert found.identity["board_profile"] == "ha_voice_pe"
    assert found.identity["endpoint_ephemeral_public_key"] == "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"
    assert found.identity["pairing_nonce"] == "nonce-123456"
    assert approved.status == "approved"
    assert approved.ui_state == "waiting_for_endpoint_online"
    assert canceled.status == "canceled"


def test_ble_pairing_approval_status_requires_matching_session_identity(tmp_path):
    core = FakeCoreClient(
        pairing_session={
            "session_id": "blepair-test",
            "status": "approved",
            "approved_device_id": "esp-pe-1",
            "endpoint_identity": {
                "device_id": "esp-pe-1",
                "target_node_id": "esp-pe-1",
                "board_profile": "ha_voice_pe",
            },
        }
    )
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=FakeSupervisorClient(),
    )

    assert service.pairing_session_approval_status("blepair-test", "esp-pe-1")["approved"] is True
    assert service.pairing_session_approval_status("blepair-test", "other-device")["approved"] is False


def test_ble_pairing_status_reports_approved_recovery_handoff(tmp_path):
    registry_store = EndpointRegistryStore(path=tmp_path / "endpoint-registry.json")
    now = datetime.now(timezone.utc).isoformat()
    registry_store.save(
        PersistedEndpointRegistry(
            endpoints={
                "esp-pe-1": EndpointRegistryRecord(
                    endpoint_id="esp-pe-1",
                    hardware_id="esp32-a085e3f0e16c",
                    device_state="idle",
                    firmware_version="z20260904015431-19e8059",
                    ip_address="10.0.0.171",
                    capabilities={
                        "device_id": "esp-pe-1",
                        "onboarding_session_id": "blepair-test",
                        "board_profile": "ha_voice_pe",
                        "application_type": "recovery",
                    },
                    first_seen_at=now,
                    last_seen_at=now,
                    updated_at=now,
                )
            }
        )
    )
    core = FakeCoreClient(
        pairing_session={
            "session_id": "blepair-test",
            "status": "approved",
            "approved_device_id": "esp-pe-1",
            "endpoint_identity": {
                "device_id": "esp-pe-1",
                "target_node_id": "esp-pe-1",
                "board_profile": "ha_voice_pe",
            },
        }
    )
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=FakeSupervisorClient(),
        endpoint_registry_store=registry_store,
    )

    response = service.get_pairing_session("blepair-test")

    assert response.status == "approved"
    assert response.ui_state == "firmware_update_needed"
    assert response.handoff["state"] == "firmware_update_needed"
    assert response.handoff["endpoint_id"] == "esp-pe-1"
    assert response.handoff["ip_address"] == "10.0.0.171"
    assert response.handoff["onboarding_session_id"] == "blepair-test"


def test_ble_pairing_status_waits_when_recovery_registry_ping_is_stale(tmp_path):
    registry_store = EndpointRegistryStore(path=tmp_path / "endpoint-registry.json")
    stale_seen = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    registry_store.save(
        PersistedEndpointRegistry(
            endpoints={
                "esp-pe-1": EndpointRegistryRecord(
                    endpoint_id="esp-pe-1",
                    hardware_id="esp32-a085e3f0e16c",
                    device_state="idle",
                    firmware_version="z20260904015431-19e8059",
                    ip_address="10.0.0.171",
                    capabilities={
                        "device_id": "esp-pe-1",
                        "onboarding_session_id": "blepair-test",
                        "board_profile": "ha_voice_pe",
                        "application_type": "recovery",
                    },
                    first_seen_at=stale_seen,
                    last_seen_at=stale_seen,
                    updated_at=stale_seen,
                )
            }
        )
    )
    core = FakeCoreClient(
        pairing_session={
            "session_id": "blepair-test",
            "status": "approved",
            "approved_device_id": "esp-pe-1",
            "endpoint_identity": {
                "device_id": "esp-pe-1",
                "target_node_id": "esp-pe-1",
                "board_profile": "ha_voice_pe",
            },
        }
    )
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=FakeSupervisorClient(),
        endpoint_registry_store=registry_store,
    )

    response = service.get_pairing_session("blepair-test")

    assert response.status == "approved"
    assert response.ui_state == "waiting_for_endpoint_online"
    assert response.handoff["state"] == "waiting_for_endpoint_online"
    assert response.handoff["connection_state"] == "stale"
    assert "ui_state" not in response.handoff


def test_ble_wifi_credentials_are_saved_encrypted_and_redacted(tmp_path):
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=FakeCoreClient(),
        supervisor_client=FakeSupervisorClient(),
    )

    status = service.save_wifi_credentials(
        EndpointBleWifiCredentialsRequest(
            wifi_ssid="KitchenNet",
            wifi_password="correct-password",
            backend_host="hexe.local",
            http_port=9004,
            ws_port=9004,
            use_tls=False,
        )
    )
    saved = service.wifi_credentials_status()
    raw_file = (tmp_path / "endpoint_ble_wifi_credentials.json").read_text(encoding="utf-8")

    assert status.ok is True
    assert saved.wifi_ssid == "KitchenNet"
    assert saved.wifi_password_saved is True
    assert saved.backend_host == "hexe.local"
    assert "correct-password" not in raw_file
    assert "encrypted_wifi_password" in raw_file
    assert (tmp_path / "endpoint_ble_wifi_credentials.key").exists()


def test_ble_onboarding_uses_saved_wifi_password_when_browser_does_not_echo_it(tmp_path):
    core = FakeCoreClient(status="granted")
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )
    service.save_wifi_credentials(
        EndpointBleWifiCredentialsRequest(
            wifi_ssid="KitchenNet",
            wifi_password="correct-password",
            backend_host="hexe.local",
            http_port=9004,
            ws_port=9004,
            use_tls=False,
        )
    )

    response = service.provision_wifi(request_payload(wifi_password=None, save_wifi_credentials=True))

    assert response.ok is True
    assert supervisor.calls[0]["credential_payload"]["wifi_password"] == "correct-password"
    assert response.credential_payload["wifi_password"] == "[REDACTED]"


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


def test_ble_onboarding_refreshes_latest_pairing_identity_before_addressless_provisioning(tmp_path):
    latest_key = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
    latest_nonce = "latest-nonce-1234"
    core = FakeCoreClient(
        status="granted",
        pairing_session={
            "session_id": "ble-session-1",
            "status": "approved",
            "approved_device_id": "voice-endpoint-1",
            "endpoint_identity": {
                "device_id": "voice-endpoint-1",
                "target_node_id": "voice-endpoint-1",
                "endpoint_ephemeral_public_key": latest_key,
                "pairing_nonce": latest_nonce,
                "adapter": "hci1",
                "supervisor_id": "sup-nearby",
            },
        },
    )
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.provision_wifi(
        request_payload(
            target_address=None,
            endpoint_ephemeral_public_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            pairing_nonce="stale-nonce-1234",
        )
    )

    assert response.ok is True
    assert core.pairing_calls[0] == {"operation": "get", "session_id": "ble-session-1", "refresh": True}
    assert core.requested_payloads[0]["provisioning"]["endpoint_ephemeral_public_key"] == latest_key
    assert core.requested_payloads[0]["provisioning"]["pairing_nonce"] == latest_nonce
    assert core.requested_payloads[0]["adapter"] == "hci1"
    assert core.requested_payloads[0]["supervisor_id"] == "sup-nearby"
    assert supervisor.calls[0]["endpoint_ephemeral_public_key"] == latest_key
    assert supervisor.calls[0]["pairing_nonce"] == latest_nonce
    assert supervisor.calls[0]["adapter"] == "hci1"


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


def test_ble_onboarding_scan_status_scope_cannot_be_reused_for_provisioning(tmp_path):
    core = FakeCoreClient(operations=["ble.scan", "ble.status"])
    supervisor = FakeSupervisorClient()
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.provision_wifi(request_payload())

    assert response.ok is False
    assert response.status == "failed"
    assert response.error == "core_ble_provisioning_contract_unavailable"
    assert core.requested_payloads == []
    assert supervisor.calls == []
    assert response.schema_status["operation_supported"] is False


def test_ble_onboarding_supervisor_failure_is_redacted_and_releases_lease(tmp_path):
    core = FakeCoreClient(status="granted")
    supervisor = FakeSupervisorClient(
        result={
            "ok": False,
            "status": "failed",
            "error": "wrong_adapter",
            "lease_token": "secret-lease-token",
            "credential_payload": {"wifi_ssid": "KitchenNet", "wifi_password": "correct-password"},
            "envelope": {
                "nonce": "secret-nonce",
                "ciphertext": "secret-ciphertext",
                "tag": "secret-tag",
                "aad": "secret-aad",
            },
        }
    )
    service = EndpointBleOnboardingService(
        onboarding_state_store=trusted_store(tmp_path),
        core_client=core,
        supervisor_client=supervisor,
    )

    response = service.provision_wifi(request_payload())
    encoded = response.model_dump_json()

    assert response.ok is False
    assert response.status == "failed"
    assert response.error == "wrong_adapter"
    assert core.released == [{"lease_id": "lease-1", "node_id": "voice-node-main"}]
    assert response.supervisor_result["lease_token"] == "[REDACTED]"
    assert response.supervisor_result["credential_payload"]["wifi_password"] == "[REDACTED]"
    assert response.supervisor_result["envelope"]["ciphertext"] == "[REDACTED]"
    assert "correct-password" not in encoded
    assert "secret-lease-token" not in encoded
    assert "secret-ciphertext" not in encoded


def test_frontend_exposes_core_governed_ble_operator_flow():
    client_source = Path("frontend/src/api/client.js").read_text(encoding="utf-8")
    dashboard_source = Path("frontend/src/features/dashboard/VoiceEndpointDashboardSection.jsx").read_text(encoding="utf-8")

    assert "provisionEndpointBleWifi" in client_source
    assert "scanEndpointBleDevices" in client_source
    assert "readEndpointBleIdentity" in client_source
    assert "startEndpointBlePairingSession" in client_source
    assert "getEndpointBlePairingSession" in client_source
    assert "startEndpointBleFirmwareHandoff" in client_source
    assert "approveEndpointBlePairingSession" in client_source
    assert "cancelEndpointBlePairingSession" in client_source
    assert "getEndpointBleWifiCredentials" in client_source
    assert "saveEndpointBleWifiCredentials" in client_source
    assert '"/api/endpoint/ble/provision-wifi"' in client_source
    assert '"/api/endpoint/ble/wifi-credentials"' in client_source
    assert '"/api/endpoint/ble/scan"' in client_source
    assert '"/api/endpoint/ble/identity"' in client_source
    assert '"/api/endpoint/ble/pairing-sessions"' in client_source
    assert "/firmware-handoff" in client_source
    assert "EndpointBleOnboardingPanel" in dashboard_source
    assert "Core-Governed BLE" in dashboard_source
    assert "Start Pairing" in dashboard_source
    assert "Approve Device" in dashboard_source
    assert "Send Wi-Fi" in dashboard_source
    assert "Save Wi-Fi" in dashboard_source
    assert "Retry Firmware" in dashboard_source
    assert "Installing full endpoint firmware now." in dashboard_source
    assert "Advanced fallback scan" in dashboard_source
    assert "Scan BLE" in dashboard_source
    assert "board_profile" in dashboard_source
    assert "Provision over BLE" in dashboard_source
    assert "endpoint_ephemeral_public_key" in dashboard_source
    assert "wifi_password" in dashboard_source
