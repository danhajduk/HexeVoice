from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
import httpx

from hexevoice.api.models import EndpointBleProvisionWifiRequest, EndpointBleProvisionWifiResponse
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.persistence import OnboardingStateStore
from hexevoice.supervisor.client import SupervisorApiClient


BLE_PROVISIONING_OPERATION = "ble.provision_wifi"
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

    def provision_wifi(self, payload: EndpointBleProvisionWifiRequest) -> EndpointBleProvisionWifiResponse:
        state = self._store.load()
        core_base_url = state.pre_trust.core_base_url
        node_id = state.trust_activation.node_id
        node_trust_token = state.trust_activation.node_trust_token
        if not core_base_url:
            raise HTTPException(status_code=400, detail="core_connection_not_configured")
        if not node_id or not node_trust_token:
            raise HTTPException(status_code=400, detail="trust_not_configured")

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
        try:
            access_schema = self._core_client.get_hardware_access_request_schema(core_base_url=core_base_url)
            voice_schema = self._core_client.get_voice_ble_provisioning_schema(core_base_url=core_base_url)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=_http_error_detail(exc, "core_schema_discovery_failed")) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="core_schema_discovery_timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"core_schema_discovery_failed: {exc}") from exc

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

    def _request_access(
        self,
        *,
        core_base_url: str,
        node_trust_token: str,
        node_id: str,
        payload: EndpointBleProvisionWifiRequest,
        provisioning: dict[str, Any],
    ) -> dict[str, Any]:
        request_payload = _clean_dict(
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
        )
        try:
            return self._core_client.request_hardware_access(
                core_base_url=core_base_url,
                node_trust_token=node_trust_token,
                payload=request_payload,
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=_http_error_detail(exc, "hardware_access_request_failed")) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="hardware_access_request_timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"hardware_access_request_failed: {exc}") from exc

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
