from typing import Literal

from pydantic import BaseModel, Field


class ApiHealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class NodeStatusResponse(BaseModel):
    node_name: str
    node_type: str
    node_id: str | None
    lifecycle_state: str
    trust_state: str
    operational_ready: bool
    blocking_reasons: list[str] = Field(default_factory=list)


class OnboardingStatusResponse(BaseModel):
    onboarding_state: str
    trust_state: str
    next_action: str


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
