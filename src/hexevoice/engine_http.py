from __future__ import annotations

from pathlib import Path

import httpx


def client_for_engine(*, timeout: float, socket_path: Path | None = None) -> httpx.Client:
    if socket_path is None:
        return httpx.Client(timeout=timeout)
    return httpx.Client(timeout=timeout, transport=httpx.HTTPTransport(uds=str(socket_path)))


def async_client_for_engine(*, timeout: float, socket_path: Path | None = None) -> httpx.AsyncClient:
    if socket_path is None:
        return httpx.AsyncClient(timeout=timeout)
    return httpx.AsyncClient(timeout=timeout, transport=httpx.AsyncHTTPTransport(uds=str(socket_path)))
