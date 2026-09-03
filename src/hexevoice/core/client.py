from __future__ import annotations

import httpx


class CoreOnboardingClient:
    def get_hardware_access_request_schema(self, *, core_base_url: str) -> dict:
        response = httpx.get(
            f"{core_base_url.rstrip('/')}/api/system/nodes/hardware/access-requests/schema",
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def get_voice_ble_provisioning_schema(self, *, core_base_url: str) -> dict:
        response = httpx.get(
            f"{core_base_url.rstrip('/')}/api/system/nodes/hardware/ble/provisioning/schemas/voice",
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def request_hardware_access(self, *, core_base_url: str, node_trust_token: str, payload: dict) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/nodes/hardware/access-requests",
            headers={"X-Node-Trust-Token": node_trust_token},
            json=payload,
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def release_hardware_lease(self, *, core_base_url: str, node_trust_token: str, lease_id: str, node_id: str) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/nodes/hardware/leases/{lease_id}/release",
            headers={"X-Node-Trust-Token": node_trust_token},
            json={"node_id": node_id},
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def scan_ble_devices(self, *, core_base_url: str, node_trust_token: str, payload: dict) -> dict:
        try:
            scan_seconds = float(payload.get("scan_seconds") or 5.0)
        except (TypeError, ValueError):
            scan_seconds = 5.0
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/nodes/hardware/bluetooth/ble/scan",
            headers={"X-Node-Trust-Token": node_trust_token},
            json=payload,
            timeout=min(max(scan_seconds + 15.0, 20.0), 90.0),
        )
        response.raise_for_status()
        return response.json()

    def read_ble_identity(self, *, core_base_url: str, node_trust_token: str, payload: dict) -> dict:
        try:
            timeout_s = float(payload.get("timeout_s") or 20.0)
        except (TypeError, ValueError):
            timeout_s = 20.0
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/nodes/hardware/bluetooth/ble/identity",
            headers={"X-Node-Trust-Token": node_trust_token},
            json=payload,
            timeout=min(max(timeout_s * 4.0 + 30.0, 45.0), 180.0),
        )
        response.raise_for_status()
        return response.json()

    def create_ble_pairing_session(self, *, core_base_url: str, admin_token: str, payload: dict) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/hardware/bluetooth/ble/pairing-sessions",
            headers={"X-Admin-Token": admin_token},
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    def get_ble_pairing_session(self, *, core_base_url: str, admin_token: str, session_id: str, refresh: bool = True) -> dict:
        response = httpx.get(
            f"{core_base_url.rstrip('/')}/api/system/hardware/bluetooth/ble/pairing-sessions/{session_id}",
            headers={"X-Admin-Token": admin_token},
            params={"refresh": str(bool(refresh)).lower()},
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    def approve_ble_pairing_session(self, *, core_base_url: str, admin_token: str, session_id: str, payload: dict) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/hardware/bluetooth/ble/pairing-sessions/{session_id}/approve",
            headers={"X-Admin-Token": admin_token},
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    def cancel_ble_pairing_session(self, *, core_base_url: str, admin_token: str, session_id: str, payload: dict) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/hardware/bluetooth/ble/pairing-sessions/{session_id}/cancel",
            headers={"X-Admin-Token": admin_token},
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    def start_onboarding_session(self, *, core_base_url: str, payload: dict) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/nodes/onboarding/sessions",
            json=payload,
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def finalize_onboarding_session(self, *, core_base_url: str, session_id: str, node_nonce: str) -> dict:
        response = httpx.get(
            f"{core_base_url.rstrip('/')}/api/system/nodes/onboarding/sessions/{session_id}/finalize",
            params={"node_nonce": node_nonce},
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def get_trust_status(self, *, core_base_url: str, node_id: str, node_trust_token: str) -> dict:
        response = httpx.get(
            f"{core_base_url.rstrip('/')}/api/system/nodes/trust-status/{node_id}",
            headers={"X-Node-Trust-Token": node_trust_token},
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def start_reauth_session(self, *, core_base_url: str, payload: dict) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/nodes/reauth/sessions",
            json=payload,
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def finalize_reauth_session(self, *, core_base_url: str, session_id: str, node_nonce: str) -> dict:
        response = httpx.get(
            f"{core_base_url.rstrip('/')}/api/system/nodes/reauth/sessions/{session_id}/finalize",
            params={"node_nonce": node_nonce},
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def submit_capability_declaration(self, *, core_base_url: str, node_trust_token: str, payload: dict) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/nodes/capabilities/declaration",
            headers={"X-Node-Trust-Token": node_trust_token},
            json=payload,
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def submit_budget_declaration(self, *, core_base_url: str, node_trust_token: str, payload: dict) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/nodes/budgets/declaration",
            headers={"X-Node-Trust-Token": node_trust_token},
            json=payload,
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def get_governance_current(self, *, core_base_url: str, node_id: str, node_trust_token: str) -> dict:
        response = httpx.get(
            f"{core_base_url.rstrip('/')}/api/system/nodes/governance/current",
            headers={"X-Node-Trust-Token": node_trust_token},
            params={"node_id": node_id},
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def refresh_governance(self, *, core_base_url: str, node_trust_token: str, payload: dict) -> dict:
        response = httpx.post(
            f"{core_base_url.rstrip('/')}/api/system/nodes/governance/refresh",
            headers={"X-Node-Trust-Token": node_trust_token},
            json=payload,
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def get_operational_status(self, *, core_base_url: str, node_id: str, node_trust_token: str) -> dict:
        response = httpx.get(
            f"{core_base_url.rstrip('/')}/api/system/nodes/operational-status/{node_id}",
            headers={"X-Node-Trust-Token": node_trust_token},
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def update_registration_metadata(
        self,
        *,
        core_base_url: str,
        node_id: str,
        admin_token: str,
        payload: dict,
    ) -> dict:
        response = httpx.put(
            f"{core_base_url.rstrip('/')}/api/system/nodes/registrations/{node_id}/metadata",
            headers={"X-Admin-Token": admin_token},
            json=payload,
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
