from __future__ import annotations

import httpx


class CoreOnboardingClient:
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
