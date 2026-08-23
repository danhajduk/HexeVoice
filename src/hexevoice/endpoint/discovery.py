from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse
import asyncio
import json
import socket

from hexevoice.api.models import EndpointDiscoveryRequest, EndpointDiscoveryResponse
from hexevoice.config.settings import Settings
from hexevoice.persistence import EndpointRegistryRecord, EndpointRegistryStore
from hexevoice.persistence.endpoint_registry import utc_now_iso


class EndpointDiscoveryService:
    def __init__(
        self,
        *,
        settings: Settings,
        endpoint_registry_store: EndpointRegistryStore,
        stale_after_seconds: int = 60,
    ) -> None:
        self._settings = settings
        self._store = endpoint_registry_store
        self._stale_after_seconds = stale_after_seconds

    def offer(self, payload: EndpointDiscoveryRequest, *, source_ip: str | None = None) -> EndpointDiscoveryResponse:
        now = utc_now_iso()
        registry = self._store.load()
        existing = registry.endpoints.get(payload.endpoint_id)
        duplicate_online = (
            existing is not None
            and self._connection_state(existing) == "online"
            and bool(source_ip)
            and bool(existing.ip_address)
            and existing.ip_address != source_ip
        )
        if duplicate_online:
            return EndpointDiscoveryResponse(
                accepted=False,
                endpoint_id=payload.endpoint_id,
                pairing_state="duplicate_online",
                reason="endpoint_id_already_online",
                server_time=now,
            )

        pairing_state = "stale_recovered" if existing is not None and self._connection_state(existing) == "stale" else "paired"
        registry.endpoints[payload.endpoint_id] = EndpointRegistryRecord(
            endpoint_id=payload.endpoint_id,
            display_name=existing.display_name if existing else payload.display_name,
            zone_id=existing.zone_id if existing else None,
            device_state=existing.device_state if existing else "offline",
            session_id=existing.session_id if existing else None,
            firmware_version=payload.firmware_version or (existing.firmware_version if existing else None),
            ip_address=source_ip or (existing.ip_address if existing else None),
            rssi_dbm=existing.rssi_dbm if existing else None,
            capabilities=payload.capabilities or (existing.capabilities if existing else {}),
            first_seen_at=existing.first_seen_at if existing else now,
            last_seen_at=now,
            operator_updated_at=existing.operator_updated_at if existing else None,
            updated_at=now,
        )
        self._store.save(registry)

        return EndpointDiscoveryResponse(
            accepted=True,
            endpoint_id=payload.endpoint_id,
            pairing_state=pairing_state,
            backend_host=self._advertise_host(),
            http_port=self._settings.api_port,
            ws_port=self._settings.api_port,
            use_tls=self._settings.endpoint_discovery_use_tls,
            server_time=now,
        )

    def _advertise_host(self) -> str:
        if self._settings.endpoint_discovery_advertise_host:
            return self._settings.endpoint_discovery_advertise_host
        if self._settings.public_api_base_url:
            parsed = urlparse(self._settings.public_api_base_url)
            if parsed.hostname:
                return parsed.hostname
        if self._settings.api_host not in {"", "0.0.0.0", "127.0.0.1", "localhost"}:
            return self._settings.api_host
        return socket.gethostname()

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


class EndpointDiscoveryUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, *, service: EndpointDiscoveryService) -> None:
        self._service = service
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport if isinstance(transport, asyncio.DatagramTransport) else None

    def datagram_received(self, data: bytes, addr) -> None:
        if self.transport is None:
            return
        source_ip = addr[0] if addr else None
        raw = None
        try:
            raw = json.loads(data.decode("utf-8"))
            request = EndpointDiscoveryRequest.model_validate(raw)
            response = self._service.offer(request, source_ip=source_ip)
        except Exception as exc:
            endpoint_id = "unknown"
            if isinstance(raw, dict) and isinstance(raw.get("endpoint_id"), str):
                endpoint_id = raw["endpoint_id"]
            response = EndpointDiscoveryResponse(
                accepted=False,
                endpoint_id=endpoint_id,
                pairing_state="invalid_request",
                reason=exc.__class__.__name__,
                server_time=utc_now_iso(),
            )
        self.transport.sendto(response.model_dump_json(exclude_none=True).encode("utf-8"), addr)
