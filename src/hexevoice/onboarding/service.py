from __future__ import annotations

from hexevoice.api.models import (
    CoreConnectionSetupRequest,
    CoreConnectionSetupResponse,
    LocalSetupStateResponse,
    NodeIdentitySetupRequest,
    NodeIdentitySetupResponse,
)
from hexevoice.onboarding import initial_onboarding_step
from hexevoice.persistence import OnboardingStateStore, PersistedOnboardingState


class OnboardingStateService:
    def __init__(self, *, onboarding_state_store: OnboardingStateStore) -> None:
        self._store = onboarding_state_store

    def load_state(self) -> PersistedOnboardingState:
        return self._store.load()

    def local_setup_payload(self) -> LocalSetupStateResponse:
        state = self.load_state()
        return LocalSetupStateResponse(
            node_identity=NodeIdentitySetupResponse(
                configured=self._node_identity_configured(state),
                node_name=state.pre_trust.node_name,
                protocol_version=state.pre_trust.protocol_version,
                node_nonce=state.pre_trust.node_nonce,
                requested_node_id=state.pre_trust.requested_node_id,
                hostname=state.pre_trust.hostname,
                ui_endpoint=state.pre_trust.ui_endpoint,
                api_base_url=state.pre_trust.api_base_url,
            ),
            core_connection=CoreConnectionSetupResponse(
                configured=self._core_connection_configured(state),
                core_base_url=state.pre_trust.core_base_url,
            ),
        )

    def save_node_identity(self, payload: NodeIdentitySetupRequest) -> NodeIdentitySetupResponse:
        state = self.load_state()
        updated = state.model_copy(
            update={
                "pre_trust": state.pre_trust.model_copy(
                    update={
                        "node_name": payload.node_name,
                        "protocol_version": payload.protocol_version,
                        "node_nonce": payload.node_nonce,
                        "requested_node_id": payload.requested_node_id,
                        "hostname": payload.hostname,
                        "ui_endpoint": str(payload.ui_endpoint) if payload.ui_endpoint else None,
                        "api_base_url": str(payload.api_base_url) if payload.api_base_url else None,
                    }
                )
            }
        )
        self._store.save(self._recompute_resume(updated))
        return self.local_setup_payload().node_identity

    def save_core_connection(self, payload: CoreConnectionSetupRequest) -> CoreConnectionSetupResponse:
        state = self.load_state()
        updated = state.model_copy(
            update={
                "pre_trust": state.pre_trust.model_copy(
                    update={
                        "core_base_url": str(payload.core_base_url),
                    }
                )
            }
        )
        self._store.save(self._recompute_resume(updated))
        return self.local_setup_payload().core_connection

    def _node_identity_configured(self, state: PersistedOnboardingState) -> bool:
        return bool(state.pre_trust.node_name and state.pre_trust.protocol_version and state.pre_trust.node_nonce)

    def _core_connection_configured(self, state: PersistedOnboardingState) -> bool:
        return bool(state.pre_trust.core_base_url)

    def _recompute_resume(self, state: PersistedOnboardingState) -> PersistedOnboardingState:
        current_step_id = initial_onboarding_step().step_id
        last_completed_step_id = None

        if self._node_identity_configured(state):
            current_step_id = "core_connection"
            last_completed_step_id = "node_identity"

        if self._node_identity_configured(state) and self._core_connection_configured(state):
            current_step_id = "bootstrap_discovery"
            last_completed_step_id = "core_connection"

        if state.trust_activation.trust_status == "trusted" and state.resume.current_step_id != initial_onboarding_step().step_id:
            current_step_id = state.resume.current_step_id
            last_completed_step_id = state.resume.last_completed_step_id

        return state.model_copy(
            update={
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": current_step_id,
                        "last_completed_step_id": last_completed_step_id,
                    }
                )
            }
        )
