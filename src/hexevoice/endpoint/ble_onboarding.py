from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
import httpx

from hexevoice.api.models import (
    EndpointBleIdentityRequest,
    EndpointBleIdentityResponse,
    EndpointBleProvisionWifiRequest,
    EndpointBleProvisionWifiResponse,
    EndpointBleScanRequest,
    EndpointBleScanResponse,
)
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.persistence import OnboardingStateStore
from hexevoice.supervisor.client import SupervisorApiClient


BLE_PROVISIONING_OPERATION = "ble.provision_wifi"
BLE_SCAN_OPERATION = "ble.scan"
BLE_IDENTITY_OPERATION = "ble.read_identity"
BLE_PROVISIONING_SERVICE_UUID = "7f9c0000-5f04-4d8b-9a46-7c0f7a100000"
BLE_PROVISIONING_CONTRACT_VERSION = "1.0"
BLE_PROVISIONING_ENVELOPE_SCHEMA_VERSION = "1.0"
VOICE_PROVISIONING_PAYLOAD_SCHEMA_ID = "hexe.voice_node.wifi_backend.v1"
REDACTED = "[REDACTED]"
REDACTED_KEYS = {
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_expires_at() -> str:
    return (_utc_now() + timedelta(minutes=10)).replace(microsecond=0).isoformat()


def _clean_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in REDACTED_KEYS and item is not None and item != "":
                redacted[key] = REDACTED
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _http_error_detail(exc: httpx.HTTPStatusError, fallback: str) -> str:
    try:
        payload = exc.response.json()
    except ValueError:
        return exc.response.text.strip() or fallback
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, dict):
            return str(detail.get("error") or detail.get("message") or fallback)
    return fallback


def _fleet_has_no_bluetooth_supervisors(response: dict[str, Any]) -> bool:
    error = str(response.get("error") or "").strip()
    has_supervisor_count = "supervisor_count" in response
    try:
        supervisor_count = int(response.get("supervisor_count") or 0)
    except (TypeError, ValueError):
        supervisor_count = 0
    return error == "bluetooth_supervisor_unavailable" or (response.get("mode") == "fleet" and has_supervisor_count and supervisor_count == 0)


class EndpointBleOnboardingService:
    def __init__(
        self,
        *,
        onboarding_state_store: OnboardingStateStore,
        core_client: CoreOnboardingClient | None = None,
        supervisor_client: SupervisorApiClient | None = None,
    ) -> None:
        self._store = onboarding_state_store
        self._core_client = core_client or CoreOnboardingClient()
        self._supervisor_client = supervisor_client or SupervisorApiClient()

    def scan(self, payload: EndpointBleScanRequest) -> EndpointBleScanResponse:
        core_base_url, node_id, node_trust_token = self._trusted_node_context()
        schema_status = self._discover_hardware_access_contract(core_base_url, BLE_SCAN_OPERATION)
        if not schema_status["operation_supported"]:
            return EndpointBleScanResponse(
                ok=False,
                status="failed",
                node_id=node_id,
                access_request={},
                devices=[],
                scan_seconds=payload.scan_seconds,
                error="core_ble_scan_contract_unavailable",
            )

        fleet_response = self._request_fleet_ble_scan(
            core_base_url=core_base_url,
            node_trust_token=node_trust_token,
            node_id=node_id,
            payload=payload,
        )
        if fleet_response is not None:
            if not _fleet_has_no_bluetooth_supervisors(fleet_response):
                fleet_ok = bool(fleet_response.get("ok"))
                fleet_status = str(fleet_response.get("status") or "").strip().lower()
                response_status = "completed" if fleet_ok else "pending" if fleet_status == "pending" else "failed"
                return self._scan_response(
                    status=response_status,
                    node_id=node_id,
                    payload=payload,
                    access_request={},
                    supervisor_result=fleet_response,
                    error=None if fleet_ok or response_status == "pending" else str(fleet_response.get("error") or "core_ble_scan_failed"),
                )

        access_response = self._request_hardware_access(
            core_base_url=core_base_url,
            node_trust_token=node_trust_token,
            payload=_clean_dict(
                {
                    "node_id": node_id,
                    "resource_type": "bluetooth",
                    "operation": BLE_SCAN_OPERATION,
                    "supervisor_id": payload.supervisor_id,
                    "adapter": payload.adapter,
                    "duration_s": max(30, min(24 * 60 * 60, int(payload.scan_seconds) + 60)),
                    "reason": payload.operator_reason or "Discover nearby BLE endpoints",
                }
            ),
        )
        access_request = deepcopy(access_response.get("access_request") if isinstance(access_response, dict) else {})
        access_status = str(access_request.get("status") or "failed").strip().lower()
        if access_status == "pending":
            return self._scan_response(
                status="pending",
                node_id=node_id,
                payload=payload,
                access_request=access_request,
                error=None,
            )
        if access_status != "granted":
            return self._scan_response(
                status="denied" if access_status == "denied" else "failed",
                node_id=node_id,
                payload=payload,
                access_request=access_request,
                error=str(access_request.get("decision_reason") or access_status or "hardware_access_not_granted"),
            )

        supervisor_result: dict[str, Any] | None = None
        release_result: dict[str, Any] | None = None
        error: str | None = None
        try:
            lease_token = str(access_request.get("lease_token") or "")
            if not lease_token:
                error = "hardware_access_lease_token_missing"
            else:
                supervisor_result = self._supervisor_client.scan_ble(
                    _clean_dict(
                        {
                            "node_id": node_id,
                            "lease_token": lease_token,
                            "adapter": access_request.get("adapter") or payload.adapter,
                            "service_uuid": payload.service_uuid or BLE_PROVISIONING_SERVICE_UUID,
                            "scan_seconds": payload.scan_seconds,
                        }
                    )
                )
                if supervisor_result is None:
                    error = "supervisor_ble_scan_unavailable"
        finally:
            lease_id = str(access_request.get("lease_id") or "")
            if lease_id:
                release_result = self._release_lease(
                    core_base_url=core_base_url,
                    node_trust_token=node_trust_token,
                    lease_id=lease_id,
                    node_id=node_id,
                )

        completed = bool(supervisor_result and supervisor_result.get("ok"))
        if supervisor_result and not completed:
            error = str(supervisor_result.get("error") or supervisor_result.get("status") or error or "supervisor_ble_scan_failed")
        return self._scan_response(
            status="completed" if completed else "failed",
            node_id=node_id,
            payload=payload,
            access_request=access_request,
            supervisor_result=supervisor_result,
            release_result=release_result,
            error=None if completed else error,
        )

    def read_identity(self, payload: EndpointBleIdentityRequest) -> EndpointBleIdentityResponse:
        core_base_url, node_id, node_trust_token = self._trusted_node_context()
        schema_status = self._discover_hardware_access_contract(core_base_url, BLE_IDENTITY_OPERATION)
        if not schema_status["operation_supported"]:
            return EndpointBleIdentityResponse(
                ok=False,
                status="failed",
                node_id=node_id,
                target_address=payload.target_address,
                access_request={},
                identity={},
                error="core_ble_identity_contract_unavailable",
            )

        fleet_response = self._request_fleet_ble_identity(
            core_base_url=core_base_url,
            node_trust_token=node_trust_token,
            node_id=node_id,
            payload=payload,
        )
        if fleet_response is not None:
            if not _fleet_has_no_bluetooth_supervisors(fleet_response):
                fleet_ok = bool(fleet_response.get("ok"))
                fleet_status = str(fleet_response.get("status") or "").strip().lower()
                response_status = "completed" if fleet_ok else "pending" if fleet_status == "pending" else "failed"
                return self._identity_response(
                    status=response_status,
                    node_id=node_id,
                    payload=payload,
                    access_request={},
                    supervisor_result=fleet_response,
                    error=None if fleet_ok or response_status == "pending" else str(fleet_response.get("error") or "core_ble_identity_failed"),
                )

        access_response = self._request_ble_identity_access(
            core_base_url=core_base_url,
            node_trust_token=node_trust_token,
            node_id=node_id,
            payload=payload,
        )
        access_request = deepcopy(access_response.get("access_request") if isinstance(access_response, dict) else {})
        access_status = str(access_request.get("status") or "failed").strip().lower()
        if access_status == "pending":
            return self._identity_response(
                status="pending",
                node_id=node_id,
                payload=payload,
                access_request=access_request,
                error=None,
            )
        if access_status != "granted":
            return self._identity_response(
                status="denied" if access_status == "denied" else "failed",
                node_id=node_id,
                payload=payload,
                access_request=access_request,
                error=str(access_request.get("decision_reason") or access_status or "hardware_access_not_granted"),
            )

        supervisor_result: dict[str, Any] | None = None
        release_result: dict[str, Any] | None = None
        error: str | None = None
        try:
            lease_token = str(access_request.get("lease_token") or "")
            if not lease_token:
                error = "hardware_access_lease_token_missing"
            else:
                supervisor_result = self._supervisor_client.read_ble_identity(
                    self._supervisor_identity_payload(
                        node_id=node_id,
                        lease_token=lease_token,
                        access_request=access_request,
                        payload=payload,
                    )
                )
                if supervisor_result is None:
                    error = "supervisor_ble_identity_unavailable"
        finally:
            lease_id = str(access_request.get("lease_id") or "")
            if lease_id:
                release_result = self._release_lease(
                    core_base_url=core_base_url,
                    node_trust_token=node_trust_token,
                    lease_id=lease_id,
                    node_id=node_id,
                )

        completed = bool(supervisor_result and supervisor_result.get("ok"))
        if supervisor_result and not completed:
            error = str(supervisor_result.get("error") or supervisor_result.get("status") or error or "supervisor_ble_identity_failed")
        return self._identity_response(
            status="completed" if completed else "failed",
            node_id=node_id,
            payload=payload,
            access_request=access_request,
            supervisor_result=supervisor_result,
            release_result=release_result,
            error=None if completed else error,
        )

    def provision_wifi(self, payload: EndpointBleProvisionWifiRequest) -> EndpointBleProvisionWifiResponse:
        core_base_url, node_id, node_trust_token = self._trusted_node_context()

        schemas = self._discover_contract(core_base_url)
        if not schemas["operation_supported"] or not schemas["voice_payload_supported"]:
            return EndpointBleProvisionWifiResponse(
                ok=False,
                status="failed",
                node_id=node_id,
                target_node_id=payload.target_node_id,
                schema_status=schemas,
                error="core_ble_provisioning_contract_unavailable",
            )

        provisioning = self._provisioning_context(payload)
        credential_payload = self._credential_payload(payload)
        access_response = self._request_access(
            core_base_url=core_base_url,
            node_trust_token=node_trust_token,
            node_id=node_id,
            payload=payload,
            provisioning=provisioning,
        )
        access_request = deepcopy(access_response.get("access_request") if isinstance(access_response, dict) else {})
        access_status = str(access_request.get("status") or "failed").strip().lower()

        if access_status == "pending":
            return self._response(
                status="pending",
                node_id=node_id,
                payload=payload,
                provisioning=provisioning,
                credential_payload=credential_payload,
                access_request=access_request,
                schema_status=schemas,
            )
        if access_status != "granted":
            return self._response(
                status="denied" if access_status == "denied" else "failed",
                node_id=node_id,
                payload=payload,
                provisioning=provisioning,
                credential_payload=credential_payload,
                access_request=access_request,
                schema_status=schemas,
                error=str(access_request.get("decision_reason") or access_status or "hardware_access_not_granted"),
            )

        supervisor_result: dict[str, Any] | None = None
        release_result: dict[str, Any] | None = None
        error: str | None = None
        try:
            lease_token = str(access_request.get("lease_token") or "")
            if not lease_token:
                error = "hardware_access_lease_token_missing"
            else:
                supervisor_result = self._supervisor_client.provision_ble_wifi(
                    self._supervisor_payload(
                        node_id=node_id,
                        lease_token=lease_token,
                        access_request=access_request,
                        payload=payload,
                        provisioning=provisioning,
                        credential_payload=credential_payload,
                    )
                )
                if supervisor_result is None:
                    error = "supervisor_ble_provision_wifi_unavailable"
        finally:
            lease_id = str(access_request.get("lease_id") or "")
            if lease_id:
                release_result = self._release_lease(
                    core_base_url=core_base_url,
                    node_trust_token=node_trust_token,
                    lease_id=lease_id,
                    node_id=node_id,
                )

        completed = bool(supervisor_result and supervisor_result.get("ok") and supervisor_result.get("status") == "completed")
        if supervisor_result and not completed:
            error = str(supervisor_result.get("error") or supervisor_result.get("status") or error or "supervisor_ble_provision_wifi_failed")
        return self._response(
            status="completed" if completed else "failed",
            node_id=node_id,
            payload=payload,
            provisioning=provisioning,
            credential_payload=credential_payload,
            access_request=access_request,
            schema_status=schemas,
            supervisor_result=supervisor_result,
            release_result=release_result,
            error=None if completed else error,
        )

    def _discover_contract(self, core_base_url: str) -> dict[str, Any]:
        access_schema = self._get_hardware_access_schema(core_base_url)
        voice_schema = self._get_voice_ble_provisioning_schema(core_base_url)

        operations = access_schema.get("operations") if isinstance(access_schema, dict) else []
        raw_voice_payload = voice_schema.get("payload_schema") if isinstance(voice_schema, dict) else {}
        voice_payload = raw_voice_payload if isinstance(raw_voice_payload, dict) else {}
        return {
            "hardware_access_schema": bool(isinstance(access_schema, dict) and access_schema.get("ok")),
            "voice_payload_schema": bool(isinstance(voice_schema, dict) and voice_schema.get("ok")),
            "operation_supported": BLE_PROVISIONING_OPERATION in operations,
            "voice_payload_supported": (
                isinstance(voice_schema, dict)
                and voice_schema.get("operation") == BLE_PROVISIONING_OPERATION
                and voice_schema.get("node_profile_id") == "voice"
                and voice_schema.get("schema_version") == BLE_PROVISIONING_ENVELOPE_SCHEMA_VERSION
                and voice_payload.get("schema_id") == VOICE_PROVISIONING_PAYLOAD_SCHEMA_ID
            ),
            "encryption_model": _redact(voice_schema.get("encryption_model") if isinstance(voice_schema, dict) else {}),
        }

    def _discover_hardware_access_contract(self, core_base_url: str, operation: str) -> dict[str, Any]:
        access_schema = self._get_hardware_access_schema(core_base_url)
        operations = access_schema.get("operations") if isinstance(access_schema, dict) else []
        return {
            "hardware_access_schema": bool(isinstance(access_schema, dict) and access_schema.get("ok")),
            "operation_supported": operation in operations,
        }

    def _trusted_node_context(self) -> tuple[str, str, str]:
        state = self._store.load()
        core_base_url = state.pre_trust.core_base_url
        node_id = state.trust_activation.node_id
        node_trust_token = state.trust_activation.node_trust_token
        if not core_base_url:
            raise HTTPException(status_code=400, detail="core_connection_not_configured")
        if not node_id or not node_trust_token:
            raise HTTPException(status_code=400, detail="trust_not_configured")
        return core_base_url, node_id, node_trust_token

    def _get_hardware_access_schema(self, core_base_url: str) -> dict[str, Any]:
        try:
            return self._core_client.get_hardware_access_request_schema(core_base_url=core_base_url)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=_http_error_detail(exc, "core_schema_discovery_failed")) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="core_schema_discovery_timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"core_schema_discovery_failed: {exc}") from exc

    def _get_voice_ble_provisioning_schema(self, core_base_url: str) -> dict[str, Any]:
        try:
            return self._core_client.get_voice_ble_provisioning_schema(core_base_url=core_base_url)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=_http_error_detail(exc, "core_schema_discovery_failed")) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="core_schema_discovery_timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"core_schema_discovery_failed: {exc}") from exc

    def _provisioning_context(self, payload: EndpointBleProvisionWifiRequest) -> dict[str, Any]:
        return _clean_dict(
            {
                "contract_version": BLE_PROVISIONING_CONTRACT_VERSION,
                "schema_version": BLE_PROVISIONING_ENVELOPE_SCHEMA_VERSION,
                "onboarding_session_id": payload.onboarding_session_id,
                "target_node_id": payload.target_node_id,
                "node_profile_id": payload.node_profile_id,
                "payload_schema_id": VOICE_PROVISIONING_PAYLOAD_SCHEMA_ID,
                "endpoint_ephemeral_public_key": payload.endpoint_ephemeral_public_key,
                "pairing_nonce": payload.pairing_nonce,
                "claim_code_ref": payload.claim_code_ref,
                "sequence": payload.sequence,
                "expires_at": payload.expires_at or _default_expires_at(),
            }
        )

    def _credential_payload(self, payload: EndpointBleProvisionWifiRequest) -> dict[str, Any]:
        return _clean_dict(
            {
                "wifi_ssid": payload.wifi_ssid,
                "wifi_password": payload.wifi_password,
                "backend_host": payload.backend_host,
                "http_port": payload.http_port,
                "ws_port": payload.ws_port,
                "use_tls": payload.use_tls,
                "endpoint_name": payload.provisioned_endpoint_id or payload.target_node_id,
                "display_name": payload.display_name,
            }
        )

    def _request_hardware_access(
        self,
        *,
        core_base_url: str,
        node_trust_token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self._core_client.request_hardware_access(
                core_base_url=core_base_url,
                node_trust_token=node_trust_token,
                payload=payload,
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=_http_error_detail(exc, "hardware_access_request_failed")) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="hardware_access_request_timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"hardware_access_request_failed: {exc}") from exc

    def _request_fleet_ble_scan(
        self,
        *,
        core_base_url: str,
        node_trust_token: str,
        node_id: str,
        payload: EndpointBleScanRequest,
    ) -> dict[str, Any] | None:
        try:
            return self._core_client.scan_ble_devices(
                core_base_url=core_base_url,
                node_trust_token=node_trust_token,
                payload=_clean_dict(
                    {
                        "node_id": node_id,
                        "supervisor_id": payload.supervisor_id,
                        "adapter": payload.adapter,
                        "service_uuid": payload.service_uuid or BLE_PROVISIONING_SERVICE_UUID,
                        "scan_seconds": payload.scan_seconds,
                        "reason": payload.operator_reason or "Discover nearby BLE endpoints",
                    }
                ),
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 405}:
                return None
            raise HTTPException(status_code=exc.response.status_code, detail=_http_error_detail(exc, "core_ble_scan_failed")) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="core_ble_scan_timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"core_ble_scan_failed: {exc}") from exc

    def _request_fleet_ble_identity(
        self,
        *,
        core_base_url: str,
        node_trust_token: str,
        node_id: str,
        payload: EndpointBleIdentityRequest,
    ) -> dict[str, Any] | None:
        try:
            return self._core_client.read_ble_identity(
                core_base_url=core_base_url,
                node_trust_token=node_trust_token,
                payload=_clean_dict(
                    {
                        "node_id": node_id,
                        "supervisor_id": payload.supervisor_id,
                        "adapter": payload.adapter,
                        "target_address": payload.target_address,
                        "timeout_s": payload.timeout_s,
                        "reason": payload.operator_reason or "Read BLE endpoint onboarding identity",
                    }
                ),
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 405}:
                return None
            raise HTTPException(status_code=exc.response.status_code, detail=_http_error_detail(exc, "core_ble_identity_failed")) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="core_ble_identity_timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"core_ble_identity_failed: {exc}") from exc

    def _request_ble_identity_access(
        self,
        *,
        core_base_url: str,
        node_trust_token: str,
        node_id: str,
        payload: EndpointBleIdentityRequest,
    ) -> dict[str, Any]:
        return self._request_hardware_access(
            core_base_url=core_base_url,
            node_trust_token=node_trust_token,
            payload=_clean_dict(
                {
                    "node_id": node_id,
                    "resource_type": "bluetooth",
                    "operation": BLE_IDENTITY_OPERATION,
                    "supervisor_id": payload.supervisor_id,
                    "adapter": payload.adapter,
                    "duration_s": max(30, min(24 * 60 * 60, int(payload.timeout_s) + 60)),
                    "reason": payload.operator_reason or "Read BLE endpoint onboarding identity",
                }
            ),
        )

    def _request_access(
        self,
        *,
        core_base_url: str,
        node_trust_token: str,
        node_id: str,
        payload: EndpointBleProvisionWifiRequest,
        provisioning: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_hardware_access(
            core_base_url=core_base_url,
            node_trust_token=node_trust_token,
            payload=_clean_dict(
                {
                    "node_id": node_id,
                    "resource_type": "bluetooth",
                    "operation": BLE_PROVISIONING_OPERATION,
                    "supervisor_id": payload.supervisor_id,
                    "adapter": payload.adapter,
                    "duration_s": max(30, min(24 * 60 * 60, int(payload.timeout_s) + 60)),
                    "reason": payload.operator_reason or f"Provision Voice endpoint {payload.target_node_id} over BLE",
                    "provisioning": provisioning,
                }
            ),
        )

    def _supervisor_payload(
        self,
        *,
        node_id: str,
        lease_token: str,
        access_request: dict[str, Any],
        payload: EndpointBleProvisionWifiRequest,
        provisioning: dict[str, Any],
        credential_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return _clean_dict(
            {
                "node_id": node_id,
                "lease_token": lease_token,
                "adapter": access_request.get("adapter") or payload.adapter,
                "contract_version": provisioning["contract_version"],
                "schema_version": provisioning["schema_version"],
                "onboarding_session_id": provisioning["onboarding_session_id"],
                "target_node_id": provisioning["target_node_id"],
                "node_profile_id": provisioning["node_profile_id"],
                "payload_schema_id": provisioning["payload_schema_id"],
                "endpoint_ephemeral_public_key": provisioning["endpoint_ephemeral_public_key"],
                "pairing_nonce": provisioning.get("pairing_nonce"),
                "claim_code_ref": provisioning.get("claim_code_ref"),
                "sequence": provisioning["sequence"],
                "expires_at": provisioning["expires_at"],
                "target_address": payload.target_address,
                "credential_payload": credential_payload,
                "timeout_s": payload.timeout_s,
            }
        )

    def _supervisor_identity_payload(
        self,
        *,
        node_id: str,
        lease_token: str,
        access_request: dict[str, Any],
        payload: EndpointBleIdentityRequest,
    ) -> dict[str, Any]:
        return _clean_dict(
            {
                "node_id": node_id,
                "lease_token": lease_token,
                "adapter": access_request.get("adapter") or payload.adapter,
                "target_address": payload.target_address,
                "timeout_s": payload.timeout_s,
            }
        )

    def _release_lease(self, *, core_base_url: str, node_trust_token: str, lease_id: str, node_id: str) -> dict[str, Any] | None:
        try:
            return self._core_client.release_hardware_lease(
                core_base_url=core_base_url,
                node_trust_token=node_trust_token,
                lease_id=lease_id,
                node_id=node_id,
            )
        except httpx.HTTPError:
            return {"ok": False, "error": "hardware_access_lease_release_failed"}

    def _response(
        self,
        *,
        status: str,
        node_id: str,
        payload: EndpointBleProvisionWifiRequest,
        provisioning: dict[str, Any],
        credential_payload: dict[str, Any],
        access_request: dict[str, Any],
        schema_status: dict[str, Any],
        supervisor_result: dict[str, Any] | None = None,
        release_result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> EndpointBleProvisionWifiResponse:
        return EndpointBleProvisionWifiResponse(
            ok=status == "completed",
            status=status,  # type: ignore[arg-type]
            node_id=node_id,
            target_node_id=payload.target_node_id,
            access_request=_redact(access_request),
            provisioning=_redact(provisioning),
            credential_payload=_redact(credential_payload),
            supervisor_result=_redact(supervisor_result) if supervisor_result is not None else None,
            schema_status=_redact(schema_status),
            release_result=_redact(release_result) if release_result is not None else None,
            error=error,
        )

    def _identity_response(
        self,
        *,
        status: str,
        node_id: str,
        payload: EndpointBleIdentityRequest,
        access_request: dict[str, Any],
        supervisor_result: dict[str, Any] | None = None,
        release_result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> EndpointBleIdentityResponse:
        identity: dict[str, Any] = {}
        adapters: list[dict[str, Any]] = []
        adapter = payload.adapter
        if isinstance(supervisor_result, dict):
            raw_identity = supervisor_result.get("identity") or supervisor_result.get("onboarding")
            if isinstance(raw_identity, dict):
                identity = deepcopy(raw_identity)
            raw_adapters = supervisor_result.get("adapters")
            if isinstance(raw_adapters, list):
                adapters = [item for item in raw_adapters if isinstance(item, dict)]
            adapter_value = supervisor_result.get("adapter")
            if isinstance(adapter_value, str):
                adapter = adapter_value
            elif isinstance(adapter_value, dict):
                adapter = str(adapter_value.get("adapter") or adapter or "")
        return EndpointBleIdentityResponse(
            ok=status == "completed",
            status=status,  # type: ignore[arg-type]
            node_id=node_id,
            target_address=payload.target_address,
            access_request=_redact(access_request),
            supervisor_result=_redact(supervisor_result) if supervisor_result is not None else None,
            identity=identity,
            adapter=adapter,
            adapters=adapters,
            error=error,
            release_result=_redact(release_result) if release_result is not None else None,
        )

    def _scan_response(
        self,
        *,
        status: str,
        node_id: str,
        payload: EndpointBleScanRequest,
        access_request: dict[str, Any],
        supervisor_result: dict[str, Any] | None = None,
        release_result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> EndpointBleScanResponse:
        devices = []
        adapters = []
        adapter = payload.adapter
        service_uuid = payload.service_uuid or BLE_PROVISIONING_SERVICE_UUID
        scan_seconds = payload.scan_seconds
        if isinstance(supervisor_result, dict):
            raw_devices = supervisor_result.get("matching_devices") or supervisor_result.get("devices")
            if isinstance(raw_devices, list):
                devices = [item for item in raw_devices if isinstance(item, dict)]
            raw_adapters = supervisor_result.get("adapters")
            if isinstance(raw_adapters, list):
                adapters = [item for item in raw_adapters if isinstance(item, dict)]
            adapter_value = supervisor_result.get("adapter")
            if isinstance(adapter_value, str):
                adapter = adapter_value
            elif isinstance(adapter_value, dict):
                adapter = str(adapter_value.get("adapter") or adapter or "")
            if isinstance(supervisor_result.get("service_uuid"), str):
                service_uuid = str(supervisor_result["service_uuid"])
            scan_seconds = int(supervisor_result.get("scan_seconds") or scan_seconds)
        return EndpointBleScanResponse(
            ok=status == "completed",
            status=status,  # type: ignore[arg-type]
            node_id=node_id,
            access_request=_redact(access_request),
            supervisor_result=_redact(supervisor_result) if supervisor_result is not None else None,
            devices=devices,
            adapter=adapter,
            adapters=adapters,
            service_uuid=service_uuid,
            scan_seconds=scan_seconds,
            error=error,
            release_result=_redact(release_result) if release_result is not None else None,
        )
