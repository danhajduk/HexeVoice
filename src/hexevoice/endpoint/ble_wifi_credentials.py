from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class BleWifiCredentialStore:
    def __init__(self, *, path: Path, key_path: Path) -> None:
        self._path = path
        self._key_path = key_path

    def status(self) -> dict[str, Any]:
        payload = self._load_payload()
        encrypted_password = str(payload.get("encrypted_wifi_password") or "")
        return {
            "ok": True,
            "wifi_ssid": payload.get("wifi_ssid") or None,
            "wifi_password_saved": bool(encrypted_password),
            "backend_host": payload.get("backend_host") or None,
            "http_port": payload.get("http_port"),
            "ws_port": payload.get("ws_port"),
            "use_tls": payload.get("use_tls"),
            "updated_at": payload.get("updated_at") or None,
        }

    def save(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._load_payload()
        updated = {
            "schema_version": "hexevoice-ble-wifi-credentials-v1",
            "wifi_ssid": payload.get("wifi_ssid") or current.get("wifi_ssid"),
            "encrypted_wifi_password": current.get("encrypted_wifi_password"),
            "backend_host": payload.get("backend_host") or current.get("backend_host"),
            "http_port": payload.get("http_port") if payload.get("http_port") is not None else current.get("http_port"),
            "ws_port": payload.get("ws_port") if payload.get("ws_port") is not None else current.get("ws_port"),
            "use_tls": payload.get("use_tls") if payload.get("use_tls") is not None else current.get("use_tls"),
            "updated_at": _utc_now(),
        }
        wifi_password = payload.get("wifi_password")
        if wifi_password:
            updated["encrypted_wifi_password"] = self._fernet().encrypt(str(wifi_password).encode("utf-8")).decode("ascii")
        self._write_payload(updated)
        return self.status()

    def saved_password_for_ssid(self, wifi_ssid: str) -> str | None:
        payload = self._load_payload()
        if str(payload.get("wifi_ssid") or "") != wifi_ssid:
            return None
        encrypted_password = str(payload.get("encrypted_wifi_password") or "")
        if not encrypted_password:
            return None
        try:
            return self._fernet().decrypt(encrypted_password.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError):
            return None

    def _load_payload(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(temp_path, 0o600)
        temp_path.replace(self._path)

    def _fernet(self) -> Fernet:
        return Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            return self._key_path.read_bytes().strip()
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        temp_path = self._key_path.with_suffix(f"{self._key_path.suffix}.tmp")
        temp_path.write_bytes(key)
        os.chmod(temp_path, 0o600)
        temp_path.replace(self._key_path)
        return key
