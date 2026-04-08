from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field


class OnboardingStepResponse(BaseModel):
    step_id: str
    label: str
    lifecycle_state: str
    phase: str
    complete: bool = False
    current: bool = False


class ApiHealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class NodeStatusResponse(BaseModel):
    node_name: str
    node_type: str
    node_id: str | None
    lifecycle_state: str
    current_step_id: str
    current_step_label: str
    trust_state: str
    operational_ready: bool
    blocking_reasons: list[str] = Field(default_factory=list)


class OnboardingStatusResponse(BaseModel):
    onboarding_state: str
    lifecycle_state: str
    trust_state: str
    current_step_id: str
    current_step_label: str
    next_action: str
    steps: list[OnboardingStepResponse] = Field(default_factory=list)


class NodeIdentitySetupRequest(BaseModel):
    node_name: str
    protocol_version: str
    node_nonce: str
    requested_node_id: str | None = None
    hostname: str | None = None
    ui_endpoint: AnyHttpUrl | None = None
    api_base_url: AnyHttpUrl | None = None


class NodeIdentitySetupResponse(BaseModel):
    configured: bool
    node_name: str | None = None
    protocol_version: str | None = None
    node_nonce: str | None = None
    requested_node_id: str | None = None
    hostname: str | None = None
    ui_endpoint: str | None = None
    api_base_url: str | None = None


class CoreConnectionSetupRequest(BaseModel):
    core_base_url: AnyHttpUrl


class CoreConnectionSetupResponse(BaseModel):
    configured: bool
    core_base_url: str | None = None


class LocalSetupStateResponse(BaseModel):
    node_identity: NodeIdentitySetupResponse
    core_connection: CoreConnectionSetupResponse


class CapabilitySummaryResponse(BaseModel):
    configured: list[str] = Field(default_factory=list)
    declared: list[str] = Field(default_factory=list)


class GovernanceReadinessResponse(BaseModel):
    operational_ready: bool
    degraded: bool
    blocking_reasons: list[str] = Field(default_factory=list)


class ProviderStatusResponse(BaseModel):
    provider_id: str
    configured: bool
    healthy: bool
    status: str


class ServiceStatusResponse(BaseModel):
    backend: str
    frontend: str
    scheduler: str
