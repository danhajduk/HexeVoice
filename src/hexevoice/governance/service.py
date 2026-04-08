from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from hexevoice.api.models import GovernanceBundleResponse, GovernanceRefreshResponse, OperationalStatusResponse
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.persistence import OnboardingStateStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GovernanceService:
    def __init__(
        self,
        *,
        onboarding_state_store: OnboardingStateStore,
        core_onboarding_client: CoreOnboardingClient | None = None,
    ) -> None:
        self._store = onboarding_state_store
        self._core_client = core_onboarding_client or CoreOnboardingClient()

    def current(self) -> GovernanceBundleResponse:
        state = self._store.load()
        self._assert_ready(state)
        response = self._core_client.get_governance_current(
            core_base_url=state.pre_trust.core_base_url,
            node_id=state.trust_activation.node_id,
            node_trust_token=state.trust_activation.node_trust_token,
        )
        updated = self._update_governance_state(state, response=response, updated=True)
        return GovernanceBundleResponse(
            node_id=updated.trust_activation.node_id or "",
            capability_profile_id=updated.capability_declaration.capability_profile_id,
            governance_version=updated.governance_sync.governance_version,
            issued_timestamp=updated.governance_sync.issued_timestamp,
            refresh_interval_s=updated.governance_sync.refresh_interval_s,
            governance_bundle=updated.governance_sync.governance_bundle or {},
        )

    def refresh(self) -> GovernanceRefreshResponse:
        state = self._store.load()
        self._assert_ready(state)
        response = self._core_client.refresh_governance(
            core_base_url=state.pre_trust.core_base_url,
            node_trust_token=state.trust_activation.node_trust_token,
            payload={
                "node_id": state.trust_activation.node_id,
                "current_governance_version": state.governance_sync.governance_version,
            },
        )
        updated = self._update_governance_state(state, response=response, updated=response.get("updated", False))
        return GovernanceRefreshResponse(
            updated=bool(response.get("updated", False)),
            governance_version=updated.governance_sync.governance_version,
            refresh_interval_s=updated.governance_sync.refresh_interval_s,
            governance_bundle=updated.governance_sync.governance_bundle,
        )

    def operational_status(self) -> OperationalStatusResponse:
        state = self._store.load()
        self._assert_ready(state)
        response = self._core_client.get_operational_status(
            core_base_url=state.pre_trust.core_base_url,
            node_id=state.trust_activation.node_id,
            node_trust_token=state.trust_activation.node_trust_token,
        )
        updated_at = _utc_now()
        updated = state.model_copy(
            update={
                "operational_status": state.operational_status.model_copy(
                    update={
                        "lifecycle_state": response.get("lifecycle_state"),
                        "trust_status": response.get("trust_status"),
                        "capability_status": response.get("capability_status", "missing"),
                        "governance_status": response.get("governance_status", "pending_capability"),
                        "operational_ready": bool(response.get("operational_ready", False)),
                        "active_governance_version": response.get("active_governance_version"),
                        "last_governance_issued_at": response.get("last_governance_issued_at"),
                        "last_governance_refresh_request_at": response.get("last_governance_refresh_request_at"),
                        "governance_freshness_state": response.get("governance_freshness_state"),
                        "governance_freshness_changed_at": response.get("governance_freshness_changed_at"),
                        "governance_stale_for_s": response.get("governance_stale_for_s"),
                        "governance_outdated": bool(response.get("governance_outdated", False)),
                        "last_telemetry_timestamp": response.get("last_telemetry_timestamp"),
                        "updated_at": response.get("updated_at") or updated_at,
                    }
                ),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": "ready" if response.get("operational_ready") else "governance_sync",
                        "last_completed_step_id": "governance_sync" if response.get("operational_ready") else "capability_declaration",
                    }
                ),
            }
        )
        self._store.save(updated)

        return OperationalStatusResponse(
            node_id=updated.trust_activation.node_id or "",
            lifecycle_state=updated.operational_status.lifecycle_state or "trusted",
            trust_status=updated.operational_status.trust_status or updated.trust_activation.trust_status,
            capability_status=updated.operational_status.capability_status,
            governance_status=updated.operational_status.governance_status,
            operational_ready=updated.operational_status.operational_ready,
            active_governance_version=updated.operational_status.active_governance_version,
            last_governance_issued_at=updated.operational_status.last_governance_issued_at,
            last_governance_refresh_request_at=updated.operational_status.last_governance_refresh_request_at,
            governance_freshness_state=updated.operational_status.governance_freshness_state,
            governance_freshness_changed_at=updated.operational_status.governance_freshness_changed_at,
            governance_stale_for_s=updated.operational_status.governance_stale_for_s,
            governance_outdated=updated.operational_status.governance_outdated,
            last_telemetry_timestamp=updated.operational_status.last_telemetry_timestamp,
            updated_at=updated.operational_status.updated_at,
        )

    def _assert_ready(self, state) -> None:
        if not state.pre_trust.core_base_url:
            raise HTTPException(status_code=400, detail="core_connection_not_configured")
        if not state.trust_activation.node_id or not state.trust_activation.node_trust_token:
            raise HTTPException(status_code=400, detail="trust_not_configured")
        if state.capability_declaration.capability_status == "missing":
            raise HTTPException(status_code=400, detail="capability_declaration_not_started")

    def _update_governance_state(self, state, *, response: dict, updated: bool):
        checked_at = _utc_now()
        governance_version = response.get("governance_version") or state.governance_sync.governance_version
        governance_bundle = response.get("governance_bundle") or state.governance_sync.governance_bundle
        issued_timestamp = response.get("issued_timestamp") or state.governance_sync.issued_timestamp
        refresh_interval_s = response.get("refresh_interval_s") or state.governance_sync.refresh_interval_s
        updated_state = state.model_copy(
            update={
                "governance_sync": state.governance_sync.model_copy(
                    update={
                        "governance_sync_status": "issued" if governance_version else "pending",
                        "governance_version": governance_version,
                        "issued_timestamp": issued_timestamp,
                        "refresh_interval_s": refresh_interval_s,
                        "governance_bundle": governance_bundle,
                        "last_refresh_request_at": checked_at,
                        "governance_freshness_state": "fresh" if governance_version else "pending",
                        "governance_freshness_changed_at": checked_at,
                        "governance_stale_for_s": 0 if governance_version else None,
                        "governance_outdated": False,
                        "last_error": None,
                    }
                ),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": "ready" if updated and state.operational_status.operational_ready else "governance_sync",
                        "last_completed_step_id": "capability_declaration",
                    }
                ),
            }
        )
        self._store.save(updated_state)
        return updated_state
