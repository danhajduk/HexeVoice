from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException

from hexevoice.api.models import (
    EndpointHeartbeatRequest,
    EndpointHeartbeatResponse,
    EndpointStatusResponse,
)


@dataclass
class _EndpointRecord:
    endpoint_id: str
    device_state: str
    session_id: str | None
    firmware_version: str | None
    ip_address: str | None
    rssi_dbm: int | None
    last_seen_at: str


class EndpointHeartbeatService:
    def __init__(self) -> None:
        self._records: dict[str, _EndpointRecord] = {}

    def record_heartbeat(self, payload: EndpointHeartbeatRequest) -> EndpointHeartbeatResponse:
        now = datetime.now(UTC).isoformat()
        self._records[payload.endpoint_id] = _EndpointRecord(
            endpoint_id=payload.endpoint_id,
            device_state=payload.device_state,
            session_id=payload.session_id,
            firmware_version=payload.firmware_version,
            ip_address=payload.ip_address,
            rssi_dbm=payload.rssi_dbm,
            last_seen_at=now,
        )
        return EndpointHeartbeatResponse(
            accepted=True,
            endpoint_id=payload.endpoint_id,
            device_state=payload.device_state,
            session_id=payload.session_id,
            server_time=now,
            last_seen_at=now,
        )

    def status(self, endpoint_id: str) -> EndpointStatusResponse:
        record = self._records.get(endpoint_id)
        if record is None:
            raise HTTPException(status_code=404, detail="endpoint_not_found")
        return EndpointStatusResponse(
            endpoint_id=record.endpoint_id,
            device_state=record.device_state,
            session_id=record.session_id,
            firmware_version=record.firmware_version,
            ip_address=record.ip_address,
            rssi_dbm=record.rssi_dbm,
            last_seen_at=record.last_seen_at,
        )
