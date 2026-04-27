from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from hexevoice.api.models import (
    EndpointHeartbeatRequest,
    EndpointHeartbeatResponse,
    EndpointMetadataUpdateRequest,
    EndpointRegistryListResponse,
    EndpointStatusResponse,
    EndpointTimeResponse,
)
from hexevoice.persistence import EndpointRegistryRecord, EndpointRegistryStore
from hexevoice.persistence.endpoint_registry import utc_now_iso


class EndpointHeartbeatService:
    def __init__(self, *, endpoint_registry_store: EndpointRegistryStore, stale_after_seconds: int = 60) -> None:
        self._store = endpoint_registry_store
        self._stale_after_seconds = stale_after_seconds

    def record_heartbeat(self, payload: EndpointHeartbeatRequest) -> EndpointHeartbeatResponse:
        now = utc_now_iso()
        registry = self._store.load()
        existing = registry.endpoints.get(payload.endpoint_id)

        registry.endpoints[payload.endpoint_id] = EndpointRegistryRecord(
            endpoint_id=payload.endpoint_id,
            display_name=existing.display_name if existing else None,
            zone_id=existing.zone_id if existing else None,
            device_state=payload.device_state,
            session_id=payload.session_id,
            firmware_version=payload.firmware_version or (existing.firmware_version if existing else None),
            ip_address=payload.ip_address or (existing.ip_address if existing else None),
            rssi_dbm=payload.rssi_dbm if payload.rssi_dbm is not None else (existing.rssi_dbm if existing else None),
            capabilities=(
                payload.capabilities
                if "capabilities" in payload.model_fields_set
                else existing.capabilities if existing else {}
            ),
            first_seen_at=existing.first_seen_at if existing else now,
            last_seen_at=now,
            operator_updated_at=existing.operator_updated_at if existing else None,
            updated_at=now,
        )
        self._store.save(registry)
        return EndpointHeartbeatResponse(
            accepted=True,
            endpoint_id=payload.endpoint_id,
            device_state=payload.device_state,
            session_id=payload.session_id,
            server_time=now,
            last_seen_at=now,
        )

    def current_time(self) -> EndpointTimeResponse:
        utc_now = datetime.now(timezone.utc)
        local_now = datetime.now().astimezone()
        offset = local_now.utcoffset()
        return EndpointTimeResponse(
            server_time=utc_now.isoformat(),
            server_unix_ms=int(utc_now.timestamp() * 1000),
            timezone=local_now.tzname() or "local",
            utc_offset_seconds=int(offset.total_seconds()) if offset is not None else 0,
            sync_interval_ms=300_000,
        )

    def latest_status(self) -> EndpointStatusResponse:
        records = list(self._store.load().endpoints.values())
        if not records:
            raise HTTPException(status_code=404, detail="endpoint_not_found")
        record = max(records, key=lambda item: item.last_seen_at)
        return self._response_from_record(record)

    def list_statuses(self) -> EndpointRegistryListResponse:
        records = sorted(
            self._store.load().endpoints.values(),
            key=lambda item: item.last_seen_at,
            reverse=True,
        )
        return EndpointRegistryListResponse(endpoints=[self._response_from_record(record) for record in records])

    def status(self, endpoint_id: str) -> EndpointStatusResponse:
        record = self._store.load().endpoints.get(endpoint_id)
        if record is None:
            raise HTTPException(status_code=404, detail="endpoint_not_found")
        return self._response_from_record(record)

    def update_metadata(self, endpoint_id: str, payload: EndpointMetadataUpdateRequest) -> EndpointStatusResponse:
        registry = self._store.load()
        record = registry.endpoints.get(endpoint_id)
        if record is None:
            raise HTTPException(status_code=404, detail="endpoint_not_found")

        now = utc_now_iso()
        updates = {
            "operator_updated_at": now,
            "updated_at": now,
        }
        if "display_name" in payload.model_fields_set:
            updates["display_name"] = self._normalized_optional_text(payload.display_name)
        if "zone_id" in payload.model_fields_set:
            updates["zone_id"] = self._normalized_optional_text(payload.zone_id)
        updated = record.model_copy(update=updates)
        registry.endpoints[endpoint_id] = updated
        self._store.save(registry)
        return self._response_from_record(updated)

    def _response_from_record(self, record: EndpointRegistryRecord) -> EndpointStatusResponse:
        connection_state = self._connection_state(record)
        return EndpointStatusResponse(
            endpoint_id=record.endpoint_id,
            display_name=record.display_name,
            zone_id=record.zone_id,
            device_state=record.device_state,
            session_id=record.session_id,
            firmware_version=record.firmware_version,
            ip_address=record.ip_address,
            rssi_dbm=record.rssi_dbm,
            capabilities=record.capabilities,
            first_seen_at=record.first_seen_at,
            last_seen_at=record.last_seen_at,
            connection_state=connection_state,
            stale=connection_state == "stale",
        )

    def _connection_state(self, record: EndpointRegistryRecord) -> str:
        if record.device_state == "offline":
            return "offline"

        try:
            last_seen = datetime.fromisoformat(record.last_seen_at)
        except ValueError:
            return "stale"

        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - last_seen).total_seconds()
        return "stale" if age_seconds > self._stale_after_seconds else "online"

    @staticmethod
    def _normalized_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
