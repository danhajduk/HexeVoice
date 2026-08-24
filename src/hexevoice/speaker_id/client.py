from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from hexevoice.engine_http import client_for_engine


class SpeakerIdServiceClient:
    def __init__(
        self,
        *,
        base_url: str = "http://hexevoice-speaker-id",
        socket_path: Path | None = None,
        timeout_s: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._socket_path = socket_path
        self._timeout_s = timeout_s
        self._client = client

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/status")

    def enroll(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/enroll", payload=payload)

    def identify(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/identify", payload=payload)

    def verify(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/verify", payload=payload)

    def profiles(self) -> dict[str, Any]:
        return self._request("GET", "/profiles")

    def profile(self, profile_id: str) -> dict[str, Any]:
        return self._request("GET", f"/profiles/{profile_id}")

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/profiles/{profile_id}")

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", "/config", payload=payload)

    def _request(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._client is not None:
            response = self._client.request(method, path, json=payload)
            response.raise_for_status()
            return _json_object(response)
        with client_for_engine(timeout=self._timeout_s, socket_path=self._socket_path) as client:
            response = client.request(method, f"{self._base_url}{path}", json=payload)
            response.raise_for_status()
            return _json_object(response)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Speaker ID service returned a non-object JSON response")
    return payload
