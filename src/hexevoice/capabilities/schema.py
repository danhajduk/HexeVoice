from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


CAPABILITY_DECLARATION_SCHEMA_VERSION = "1.0"
SUPPORTED_CAPABILITY_DECLARATION_VERSIONS = {CAPABILITY_DECLARATION_SCHEMA_VERSION}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_TASK_FAMILY_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{1,127}$")


class CapabilityManifestValidationError(ValueError):
    code = "capability_manifest_invalid"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


def _clean_list(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        value = str(item or "").strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


class CapabilityNodeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(..., min_length=3, max_length=128)
    node_type: str = Field(..., min_length=2, max_length=64)
    node_name: str = Field(..., min_length=1, max_length=128)
    node_software_version: str = Field(..., min_length=1, max_length=64)

    @field_validator("node_id")
    @classmethod
    def _validate_node_id(cls, value: str) -> str:
        node_id = str(value or "").strip()
        if not node_id.startswith("node-"):
            raise ValueError("invalid_node_id")
        return node_id


class CapabilityNodeFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telemetry: bool = False
    governance_refresh: bool = False
    lifecycle_events: bool = False
    provider_failover: bool = False


class CapabilityEnvironmentHints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_target: str | None = Field(default=None, max_length=64)
    acceleration: str | None = Field(default=None, max_length=64)
    network_tier: str | None = Field(default=None, max_length=64)
    region: str | None = Field(default=None, max_length=64)


class CapabilityProviderModelMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_id: str = Field(..., min_length=1, max_length=128)
    pricing: dict[str, float] = Field(default_factory=dict)
    latency_metrics: dict[str, float] = Field(default_factory=dict)

    @field_validator("model_id")
    @classmethod
    def _validate_model_id(cls, value: str) -> str:
        model_id = str(value or "").strip()
        if not model_id:
            raise ValueError("invalid_model_id")
        return model_id

    @field_validator("pricing", mode="before")
    @classmethod
    def _validate_pricing(cls, value: Any) -> dict[str, float]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("provider_model_pricing_must_be_object")
        out: dict[str, float] = {}
        for key, raw in value.items():
            metric = str(key or "").strip().lower()
            if not metric:
                raise ValueError("invalid_pricing_metric")
            try:
                amount = float(raw)
            except Exception as exc:
                raise ValueError("invalid_pricing_value") from exc
            if amount < 0:
                raise ValueError("invalid_pricing_value")
            out[metric] = amount
        return out

    @field_validator("latency_metrics", mode="before")
    @classmethod
    def _validate_latency_metrics(cls, value: Any) -> dict[str, float]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("provider_model_latency_metrics_must_be_object")
        out: dict[str, float] = {}
        for key, raw in value.items():
            metric = str(key or "").strip().lower()
            if not metric:
                raise ValueError("invalid_latency_metric")
            try:
                latency = float(raw)
            except Exception as exc:
                raise ValueError("invalid_latency_value") from exc
            if latency < 0:
                raise ValueError("invalid_latency_value")
            out[metric] = latency
        return out


class CapabilityProviderIntelligence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., min_length=1, max_length=128)
    available_models: list[CapabilityProviderModelMetadata] = Field(default_factory=list)

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        provider = str(value or "").strip().lower()
        if not _ID_RE.match(provider):
            raise ValueError("invalid_provider_id")
        return provider

    @field_validator("available_models", mode="before")
    @classmethod
    def _validate_available_models(cls, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("provider_available_models_must_be_list")
        return value


class CapabilityDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: str = Field(..., min_length=1, max_length=16)
    node: CapabilityNodeMetadata
    declared_task_families: list[str] = Field(..., min_length=1)
    declared_capabilities: list[str] = Field(default_factory=list)
    capability_endpoints: dict[str, dict[str, Any]] = Field(default_factory=dict)
    supported_providers: list[str] = Field(..., min_length=1)
    enabled_providers: list[str] = Field(default_factory=list)
    node_features: CapabilityNodeFeatures
    environment_hints: CapabilityEnvironmentHints
    provider_intelligence: list[CapabilityProviderIntelligence] = Field(default_factory=list)

    @field_validator("manifest_version")
    @classmethod
    def _validate_manifest_version(cls, value: str) -> str:
        version = str(value or "").strip()
        if version not in SUPPORTED_CAPABILITY_DECLARATION_VERSIONS:
            raise ValueError("unsupported_capability_manifest_version")
        return version

    @field_validator("declared_task_families", mode="before")
    @classmethod
    def _validate_task_families(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("declared_task_families_must_be_list")
        values = _clean_list([str(item) for item in value])
        if not values:
            raise ValueError("declared_task_families_empty")
        for item in values:
            if not _TASK_FAMILY_RE.match(item):
                raise ValueError("invalid_task_family")
        return values

    @field_validator("declared_capabilities", mode="before")
    @classmethod
    def _validate_declared_capabilities(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("declared_capabilities_must_be_list")
        values = _clean_list([str(item) for item in value])
        for item in values:
            if not _TASK_FAMILY_RE.match(item):
                raise ValueError("invalid_declared_capability")
        return values

    @field_validator("capability_endpoints", mode="before")
    @classmethod
    def _validate_capability_endpoints(cls, value: Any) -> dict[str, dict[str, Any]]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("capability_endpoints_must_be_object")
        out: dict[str, dict[str, Any]] = {}
        for raw_key, raw_endpoint in value.items():
            key = str(raw_key or "").strip().lower()
            if not _TASK_FAMILY_RE.match(key):
                raise ValueError("invalid_capability_endpoint_key")
            if not isinstance(raw_endpoint, dict):
                raise ValueError("capability_endpoint_must_be_object")
            out[key] = dict(raw_endpoint)
        return out

    @field_validator("supported_providers", mode="before")
    @classmethod
    def _validate_supported_providers(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("supported_providers_must_be_list")
        values = _clean_list([str(item) for item in value])
        if not values:
            raise ValueError("supported_providers_empty")
        for item in values:
            if not _ID_RE.match(item):
                raise ValueError("invalid_provider_id")
        return values

    @field_validator("enabled_providers", mode="before")
    @classmethod
    def _validate_enabled_providers(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("enabled_providers_must_be_list")
        values = _clean_list([str(item) for item in value])
        for item in values:
            if not _ID_RE.match(item):
                raise ValueError("invalid_provider_id")
        return values

    @model_validator(mode="after")
    def _validate_enabled_subset(self) -> "CapabilityDeclaration":
        if self.declared_capabilities and self.declared_capabilities != self.declared_task_families:
            raise ValueError("declared_capabilities_must_match_declared_task_families")
        endpoint_keys = set(self.capability_endpoints)
        declared = set(self.declared_task_families)
        if endpoint_keys - declared:
            raise ValueError("capability_endpoint_not_declared")
        supported = set(self.supported_providers)
        for provider in self.enabled_providers:
            if provider not in supported:
                raise ValueError("enabled_provider_not_supported")
        for intel in self.provider_intelligence:
            if intel.provider not in supported:
                raise ValueError("provider_intelligence_not_supported")
        return self


def validate_capability_declaration(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = CapabilityDeclaration.model_validate(payload)
        return parsed.model_dump(mode="python")
    except ValidationError as exc:
        raise CapabilityManifestValidationError(str(exc)) from exc
