from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from pydantic import BaseModel, Field

from hexevoice.onboarding import CANONICAL_ONBOARDING_STEPS, initial_onboarding_step


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PreTrustSetupState(BaseModel):
    node_name: str | None = None
    requested_node_id: str | None = None
    hostname: str | None = None
    ui_endpoint: str | None = None
    api_base_url: str | None = None
    core_base_url: str | None = None
    protocol_version: str | None = None
    node_nonce: str | None = None


class OnboardingSessionState(BaseModel):
    session_id: str | None = None
    approval_url: str | None = None
    expires_at: str | None = None
    finalize_url: str | None = None
    session_state: str | None = None
    last_error: str | None = None


class BootstrapDiscoveryState(BaseModel):
    bootstrap_topic: str = "hexe/bootstrap/core"
    bootstrap_host: str | None = None
    bootstrap_port: int = 1884
    connection_status: str = "pending"
    last_checked_at: str | None = None
    last_error: str | None = None
    advertisement_valid: bool = False
    onboarding_mode: str | None = None
    onboarding_contract: str | None = None
    api_base: str | None = None
    mqtt_host: str | None = None
    mqtt_port: int | None = None
    register_session_endpoint: str | None = None
    registrations_endpoint: str | None = None
    compatibility_register_endpoint: str | None = None
    compatibility_ai_node_register_endpoint: str | None = None


class TrustActivationState(BaseModel):
    node_id: str | None = None
    paired_core_id: str | None = None
    node_trust_token: str | None = None
    trust_status: str = "untrusted"
    baseline_policy_version: str | None = None
    operational_mqtt_identity: str | None = None
    operational_mqtt_host: str | None = None
    operational_mqtt_port: int | None = None
    trusted_at: str | None = None


class ResumeState(BaseModel):
    current_step_id: str = Field(default_factory=lambda: initial_onboarding_step().step_id)
    last_completed_step_id: str | None = None
    last_transition_at: str | None = None


class PersistedOnboardingState(BaseModel):
    schema_version: int = 1
    pre_trust: PreTrustSetupState = Field(default_factory=PreTrustSetupState)
    bootstrap_discovery: BootstrapDiscoveryState = Field(default_factory=BootstrapDiscoveryState)
    onboarding_session: OnboardingSessionState = Field(default_factory=OnboardingSessionState)
    trust_activation: TrustActivationState = Field(default_factory=TrustActivationState)
    resume: ResumeState = Field(default_factory=ResumeState)
    updated_at: str = Field(default_factory=_utc_now)

    def normalized_current_step_id(self) -> str:
        valid_step_ids = {step.step_id for step in CANONICAL_ONBOARDING_STEPS}
        step_id = str(self.resume.current_step_id or "").strip()
        if step_id in valid_step_ids:
            return step_id
        return initial_onboarding_step().step_id


class OnboardingStateStore:
    def __init__(self, *, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> PersistedOnboardingState:
        if not self._path.exists():
            return PersistedOnboardingState()

        payload = json.loads(self._path.read_text())
        return PersistedOnboardingState.model_validate(payload)

    def save(self, state: PersistedOnboardingState) -> PersistedOnboardingState:
        updated = state.model_copy(update={"updated_at": _utc_now()})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(updated.model_dump_json(indent=2))
        temp_path.replace(self._path)
        return updated
