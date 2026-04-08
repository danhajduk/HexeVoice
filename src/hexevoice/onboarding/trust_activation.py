from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from hexevoice.api.models import TrustActivationFinalizeResponse
from hexevoice.persistence import OnboardingStateStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrustActivationService:
    def __init__(self, *, onboarding_state_store: OnboardingStateStore) -> None:
        self._store = onboarding_state_store

    def finalize_activation(self) -> TrustActivationFinalizeResponse:
        state = self._store.load()
        activation = state.onboarding_session.pending_activation

        if not isinstance(activation, dict) or not activation:
            raise HTTPException(status_code=400, detail="trust_activation_not_pending")

        node_id = activation.get("node_id")
        trust_status = activation.get("trust_status")
        if not node_id or trust_status != "trusted":
            raise HTTPException(status_code=400, detail="invalid_trust_activation_payload")

        applied_at = _utc_now()
        updated = state.model_copy(
            update={
                "trust_activation": state.trust_activation.model_copy(
                    update={
                        "node_id": node_id,
                        "node_type": activation.get("node_type"),
                        "paired_core_id": activation.get("paired_core_id"),
                        "node_trust_token": activation.get("node_trust_token"),
                        "initial_baseline_policy": activation.get("initial_baseline_policy"),
                        "trust_status": trust_status,
                        "baseline_policy_version": activation.get("baseline_policy_version"),
                        "activation_profile": activation.get("activation_profile"),
                        "operational_mqtt_identity": activation.get("operational_mqtt_identity"),
                        "operational_mqtt_token": activation.get("operational_mqtt_token"),
                        "operational_mqtt_host": activation.get("operational_mqtt_host"),
                        "operational_mqtt_port": activation.get("operational_mqtt_port"),
                        "issued_at": activation.get("issued_at"),
                        "source_session_id": activation.get("source_session_id")
                        or state.onboarding_session.session_id,
                        "trusted_at": activation.get("issued_at") or applied_at,
                        "activation_applied_at": applied_at,
                    }
                ),
                "onboarding_session": state.onboarding_session.model_copy(
                    update={
                        "pending_activation": None,
                    }
                ),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": "provider_setup",
                        "last_completed_step_id": "trust_activation",
                    }
                ),
            }
        )
        self._store.save(updated)

        return TrustActivationFinalizeResponse(
            node_id=updated.trust_activation.node_id or "",
            paired_core_id=updated.trust_activation.paired_core_id,
            trust_state=updated.trust_activation.trust_status,
            baseline_policy_version=updated.trust_activation.baseline_policy_version,
            operational_mqtt_identity=updated.trust_activation.operational_mqtt_identity,
            operational_mqtt_host=updated.trust_activation.operational_mqtt_host,
            operational_mqtt_port=updated.trust_activation.operational_mqtt_port,
            activation_applied_at=updated.trust_activation.activation_applied_at,
        )
