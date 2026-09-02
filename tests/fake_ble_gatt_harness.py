from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


BLE_PROVISIONING_OPERATION = "ble.provision_wifi"
BLE_PROVISIONING_SCOPE = "hardware.bluetooth.ble.provision_wifi"
VOICE_PAYLOAD_SCHEMA_ID = "hexe.voice_node.wifi_backend.v1"
REDACTED = "[REDACTED]"
SECRET_KEYS = {
    "aad",
    "ciphertext",
    "claim_code",
    "claim_code_ref",
    "derived_key",
    "decrypted_payload",
    "endpoint_ephemeral_public_key",
    "lease_token",
    "nonce",
    "pairing_nonce",
    "supervisor_ephemeral_public_key",
    "tag",
    "wifi_password",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: REDACTED if key in SECRET_KEYS and item else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def contains_secret(payload: Any, *secrets: str) -> bool:
    encoded = json.dumps(payload, sort_keys=True)
    return any(secret and secret in encoded for secret in secrets)


@dataclass(frozen=True)
class FakeBleLease:
    operation: str = BLE_PROVISIONING_OPERATION
    scope: str = BLE_PROVISIONING_SCOPE
    adapter: str = "hci0"
    status: str = "granted"
    expires_at: str = "2099-01-01T00:00:00+00:00"
    lease_token: str = "secret-lease-token"


class FakeBleGattEndpoint:
    def __init__(
        self,
        *,
        target_node_id: str = "voice-endpoint-1",
        onboarding_session_id: str = "ble-session-1",
        pairing_nonce: str = "nonce-123456",
        mode: str = "endpoint",
        supports_ble: bool = True,
        wifi_result: str = "connected",
        backend_result: str = "reachable",
    ) -> None:
        self.target_node_id = target_node_id
        self.onboarding_session_id = onboarding_session_id
        self.pairing_nonce = pairing_nonce
        self.mode = mode
        self.supports_ble = supports_ble
        self.wifi_result = wifi_result
        self.backend_result = backend_result
        self.last_sequence = 0
        self.saved_payload: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.last_ack: str | None = None

    def read_identity(self) -> dict[str, Any]:
        return {
            "operation": BLE_PROVISIONING_OPERATION,
            "contract_version": "1.0",
            "target_node_id": self.target_node_id,
            "node_profile_id": "voice",
            "payload_schema_id": VOICE_PAYLOAD_SCHEMA_ID,
            "endpoint_ephemeral_public_key": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE",
        }

    def read_pairing_nonce(self) -> dict[str, Any]:
        return {
            "onboarding_session_id": self.onboarding_session_id,
            "target_node_id": self.target_node_id,
            "pairing_nonce": self.pairing_nonce,
            "expires_at_unix_ms": 4_071_007_200_000,
        }

    def read_status(self) -> dict[str, Any]:
        return redact(
            {
                "supported": self.supports_ble,
                "mode": self.mode,
                "state": "provisioned" if self.saved_payload else "awaiting_credentials",
                "last_ack": self.last_ack,
                "last_error": self.last_error,
                "saved_payload": self.saved_payload,
            }
        )

    def write_credentials_json(self, raw_body: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return self._fail("malformed_payload")
        return self.write_credentials(payload)

    def write_credentials(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.supports_ble:
            return self._fail("gatt_backend_unavailable")

        if self.mode == "recovery" and payload.get("mode") != "local_recovery":
            return self._fail("core_governed_requires_endpoint_app")

        for key, expected in (
            ("contract_version", "1.0"),
            ("schema_version", "1.0"),
            ("payload_schema_id", VOICE_PAYLOAD_SCHEMA_ID),
            ("target_node_id", self.target_node_id),
            ("onboarding_session_id", self.onboarding_session_id),
            ("pairing_nonce", self.pairing_nonce),
        ):
            if payload.get(key) != expected:
                return self._fail(f"invalid_{key}")

        sequence = int(payload.get("sequence") or 0)
        if sequence <= self.last_sequence:
            return self._fail("replay_detected")
        if _expired(payload.get("expires_at")):
            return self._fail("lease_expired")

        credential_payload = payload.get("credential_payload")
        if not _valid_voice_payload(credential_payload):
            return self._fail("malformed_payload")
        if self.wifi_result != "connected":
            return self._fail("wifi_apply_failed")
        if self.backend_result != "reachable":
            return self._fail("backend_unreachable")

        self.last_sequence = sequence
        self.saved_payload = deepcopy(credential_payload)
        self.last_ack = "completed"
        self.last_error = None
        return {"ok": True, "status": "completed", "ack": self.last_ack}

    def _fail(self, error: str) -> dict[str, Any]:
        self.last_error = error
        self.last_ack = None
        return {"ok": False, "status": "failed", "error": error}


class FakeSupervisorBleBroker:
    def __init__(self, *, adapter_present: bool = True) -> None:
        self.adapter_present = adapter_present
        self.calls: list[dict[str, Any]] = []

    def provision_wifi(
        self,
        *,
        lease: FakeBleLease,
        adapter: str,
        target: FakeBleGattEndpoint,
        provisioning_context: dict[str, Any],
        credential_payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "lease": lease,
                "adapter": adapter,
                "provisioning_context": deepcopy(provisioning_context),
                "credential_payload": deepcopy(credential_payload),
            }
        )
        if not self.adapter_present:
            return {"ok": False, "status": "failed", "error": "bluetooth_adapter_absent"}
        if lease.status != "granted":
            return {"ok": False, "status": lease.status, "error": f"lease_{lease.status}"}
        if lease.operation != BLE_PROVISIONING_OPERATION or lease.scope != BLE_PROVISIONING_SCOPE:
            return {"ok": False, "status": "failed", "error": "lease_scope_mismatch"}
        if lease.adapter != adapter:
            return {"ok": False, "status": "failed", "error": "wrong_adapter"}
        if _expired(lease.expires_at):
            return {"ok": False, "status": "failed", "error": "lease_expired"}

        write_payload = dict(provisioning_context)
        write_payload["credential_payload"] = credential_payload
        result = target.write_credentials(write_payload)
        return redact({**result, "credential_payload": credential_payload})


def endpoint_provisioning_context(target: FakeBleGattEndpoint, *, sequence: int = 1, **overrides: Any) -> dict[str, Any]:
    identity = target.read_identity()
    nonce = target.read_pairing_nonce()
    context = {
        "contract_version": identity["contract_version"],
        "schema_version": "1.0",
        "onboarding_session_id": nonce["onboarding_session_id"],
        "target_node_id": identity["target_node_id"],
        "node_profile_id": identity["node_profile_id"],
        "payload_schema_id": identity["payload_schema_id"],
        "endpoint_ephemeral_public_key": identity["endpoint_ephemeral_public_key"],
        "pairing_nonce": nonce["pairing_nonce"],
        "sequence": sequence,
        "expires_at": "2099-01-01T00:00:00+00:00",
    }
    context.update(overrides)
    return context


def voice_credential_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "wifi_ssid": "KitchenNet",
        "wifi_password": "correct-password",
        "backend_host": "hexe.local",
        "http_port": 9004,
        "ws_port": 9004,
        "use_tls": False,
        "endpoint_name": "voice-endpoint-1",
        "display_name": "Kitchen Voice",
    }
    payload.update(overrides)
    return payload


def _expired(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return True
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed <= datetime.now(timezone.utc)


def _valid_voice_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("wifi_ssid"), str)
        and bool(value["wifi_ssid"])
        and (value.get("wifi_password") is None or isinstance(value.get("wifi_password"), str))
        and isinstance(value.get("backend_host"), str)
        and bool(value["backend_host"])
        and isinstance(value.get("http_port"), int)
        and 1 <= value["http_port"] <= 65535
        and isinstance(value.get("ws_port"), int)
        and 1 <= value["ws_port"] <= 65535
        and isinstance(value.get("use_tls"), bool)
    )
