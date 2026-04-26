from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EndpointRegistryRecord(BaseModel):
    endpoint_id: str
    display_name: str | None = None
    zone_id: str | None = None
    device_state: str = "offline"
    session_id: str | None = None
    firmware_version: str | None = None
    ip_address: str | None = None
    rssi_dbm: int | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    first_seen_at: str
    last_seen_at: str
    operator_updated_at: str | None = None
    updated_at: str


class PersistedEndpointRegistry(BaseModel):
    schema_version: int = 1
    endpoints: dict[str, EndpointRegistryRecord] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=utc_now_iso)


class EndpointRegistryStore:
    def __init__(self, *, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> PersistedEndpointRegistry:
        if not self._path.exists():
            return PersistedEndpointRegistry()

        payload = json.loads(self._path.read_text())
        return PersistedEndpointRegistry.model_validate(payload)

    def save(self, registry: PersistedEndpointRegistry) -> PersistedEndpointRegistry:
        updated = registry.model_copy(update={"updated_at": utc_now_iso()})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(updated.model_dump_json(indent=2))
        temp_path.replace(self._path)
        return updated
