from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import logging
import socket
from typing import Any

from hexevoice.config.settings import Settings
from hexevoice.endpoint.mdns import build_endpoint_mdns_metadata


BEACON_SCHEMA_VERSION = "hexevoice.node.beacon.v1"
HEARTBEAT_PATH = "/api/endpoint/heartbeat"
VOICE_WS_PATH = "/api/voice/ws"

log = logging.getLogger("hexevoice")


def build_endpoint_beacon_payload(
    settings: Settings,
    *,
    advertised_ip: str | None = None,
    node_id: str | None = None,
    emitted_at: datetime | None = None,
) -> dict[str, Any]:
    metadata = build_endpoint_mdns_metadata(
        settings,
        advertised_ip=advertised_ip or settings.endpoint_beacon_advertise_host,
        node_id=node_id,
    )
    timestamp = emitted_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return {
        "schema_version": BEACON_SCHEMA_VERSION,
        "emitted_at": timestamp.astimezone(UTC).isoformat(),
        "node": {
            "node_id": metadata.node_id,
            "node_name": settings.node_name,
            "node_type": metadata.node_type,
        },
        "network": {
            "advertised_ip": metadata.advertised_ip,
            "tls": metadata.tls,
        },
        "api": {
            "url": metadata.api_url,
            "port": metadata.api_port,
            "heartbeat_path": HEARTBEAT_PATH,
            "voice_ws_path": VOICE_WS_PATH,
        },
        "ui": {
            "url": metadata.ui_url,
            "port": metadata.ui_port,
        },
    }


class EndpointBeaconService:
    def __init__(self, *, settings: Settings, node_id_provider=None) -> None:
        self._settings = settings
        self._node_id_provider = node_id_provider
        self._task: asyncio.Task | None = None
        self._last_payload: dict[str, Any] | None = None
        self._last_sent_at: str | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        if not self._settings.endpoint_beacon_udp_enabled:
            self._last_error = None
            return
        if self._task is not None and not self._task.done():
            return
        try:
            self._last_payload = self._build_payload()
            self._last_error = None
        except Exception as exc:
            self._last_error = exc.__class__.__name__
            log.warning("Endpoint UDP beacon could not build payload", exc_info=True)
            return
        self._task = asyncio.create_task(self._run(), name="endpoint-udp-beacon")
        log.info(
            "Endpoint UDP beacon started: target=%s:%s interval=%ss",
            self._settings.endpoint_beacon_udp_host,
            self._settings.endpoint_beacon_udp_port,
            self._settings.endpoint_beacon_interval_seconds,
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def status(self) -> dict[str, Any]:
        active = self._task is not None and not self._task.done()
        payload = self._last_payload or {}
        network = payload.get("network") if isinstance(payload.get("network"), dict) else {}
        api = payload.get("api") if isinstance(payload.get("api"), dict) else {}
        ui = payload.get("ui") if isinstance(payload.get("ui"), dict) else {}
        return {
            "enabled": self._settings.endpoint_beacon_udp_enabled,
            "active": active,
            "status": "active" if active else ("error" if self._last_error else "disabled"),
            "host": self._settings.endpoint_beacon_udp_host,
            "port": self._settings.endpoint_beacon_udp_port,
            "interval_seconds": self._settings.endpoint_beacon_interval_seconds,
            "advertised_ip": network.get("advertised_ip"),
            "api_url": api.get("url"),
            "ui_url": ui.get("url"),
            "last_sent_at": self._last_sent_at,
            "last_error": self._last_error,
        }

    async def _run(self) -> None:
        while True:
            try:
                self._last_payload = self._build_payload()
                await asyncio.to_thread(self._send_payload, self._last_payload)
                self._last_sent_at = datetime.now(UTC).isoformat()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = exc.__class__.__name__
                log.warning("Endpoint UDP beacon send failed", exc_info=True)
            await asyncio.sleep(self._settings.endpoint_beacon_interval_seconds)

    def _build_payload(self) -> dict[str, Any]:
        node_id = self._node_id_provider() if self._node_id_provider is not None else None
        return build_endpoint_beacon_payload(self._settings, node_id=node_id)

    def _send_payload(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(data, (self._settings.endpoint_beacon_udp_host, self._settings.endpoint_beacon_udp_port))
