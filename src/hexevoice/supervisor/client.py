from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx


log = logging.getLogger("hexevoice.supervisor.client")


def _env_text(name: str, default: str) -> str:
    raw = os.getenv(name)
    return str(raw).strip() if raw is not None else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _normalize_transport(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"socket", "http"}:
        return value
    if value in {"disabled", "off", "none"}:
        return "disabled"
    return "socket"


def _normalize_base_url(raw: str) -> str:
    candidate = str(raw or "").strip()
    if not candidate:
        return "http://127.0.0.1:9009"
    if "://" not in candidate:
        return f"http://{candidate}"
    return candidate


@dataclass(frozen=True)
class SupervisorClientConfig:
    transport: str
    base_url: str
    unix_socket: str
    timeout_s: float


def supervisor_client_config() -> SupervisorClientConfig:
    transport = _normalize_transport(_env_text("HEXE_SUPERVISOR_API_TRANSPORT", "socket"))
    return SupervisorClientConfig(
        transport=transport,
        base_url=_normalize_base_url(_env_text("HEXE_SUPERVISOR_API_BASE_URL", "")),
        unix_socket=_env_text("HEXE_SUPERVISOR_API_SOCKET", "/run/hexe/supervisor.sock"),
        timeout_s=_env_float("HEXE_SUPERVISOR_API_TIMEOUT_S", 8.0),
    )


class SupervisorApiClient:
    def __init__(self, config: SupervisorClientConfig | None = None, client: httpx.Client | None = None) -> None:
        self._config = config or supervisor_client_config()
        self._enabled = self._config.transport != "disabled"
        self._client = client or (self._build_client(self._config) if self._enabled else None)

    def _build_client(self, config: SupervisorClientConfig) -> httpx.Client:
        timeout = httpx.Timeout(config.timeout_s)
        if config.transport == "socket":
            transport = httpx.HTTPTransport(uds=config.unix_socket)
            return httpx.Client(base_url="http://supervisor", transport=transport, timeout=timeout)
        return httpx.Client(base_url=config.base_url.rstrip("/"), timeout=timeout)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any] | None:
        if not self._enabled or self._client is None:
            return None
        try:
            kwargs: dict[str, Any] = {"json": payload, "params": params}
            if timeout_s is not None:
                kwargs["timeout"] = timeout_s
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            log.debug("Supervisor API request failed: %s %s (%s)", method, path, exc)
            return None
        if response.status_code >= 400:
            log.debug("Supervisor API response error: %s %s -> %s", method, path, response.status_code)
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def health(self) -> dict[str, Any] | None:
        return self._request_json("GET", "/api/supervisor/health")

    def register_runtime(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._request_json("POST", "/api/supervisor/runtimes/register", payload=payload)

    def heartbeat_runtime(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._request_json("POST", "/api/supervisor/runtimes/heartbeat", payload=payload)

    def provision_ble_wifi(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._request_json("POST", "/api/supervisor/hardware/bluetooth/ble/provision-wifi", payload=payload)

    def scan_ble(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            scan_seconds = float(payload.get("scan_seconds") or 5.0)
        except (TypeError, ValueError):
            scan_seconds = 5.0
        timeout_s = min(max(self._config.timeout_s, scan_seconds + 10.0), 75.0)
        return self._request_json(
            "POST",
            "/api/supervisor/hardware/bluetooth/ble/scan",
            payload=payload,
            timeout_s=timeout_s,
        )
