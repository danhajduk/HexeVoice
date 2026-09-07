from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)

MIN_FLUSH_ROWS = 1
MAX_FLUSH_ROWS = 32
MIN_PIXEL_CLOCK_HZ = 3_000_000
MAX_PIXEL_CLOCK_HZ = 40_000_000

SECRET_FIELD_PARTS = ("password", "passphrase", "secret", "token", "key")
PUBLIC_JSON_SCALARS = (str, int, float, bool)
ConfigFileSignature = tuple[int, int]


def _int_in_range(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


def _non_secret_mapping(values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    payload: dict[str, Any] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            continue
        normalized = key.lower()
        if any(part in normalized for part in SECRET_FIELD_PARTS):
            continue
        if isinstance(value, dict):
            nested = _non_secret_mapping(value)
            if nested:
                payload[key] = nested
        elif isinstance(value, list):
            payload[key] = [item for item in value if isinstance(item, PUBLIC_JSON_SCALARS) or item is None]
        elif isinstance(value, PUBLIC_JSON_SCALARS) or value is None:
            payload[key] = value
    return payload


def _merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _display_payload(values: dict[str, Any]) -> dict[str, int]:
    display = values.get("display")
    if not isinstance(display, dict):
        display = values
    payload: dict[str, int] = {}
    flush_rows = _int_in_range(display.get("flush_rows"), minimum=MIN_FLUSH_ROWS, maximum=MAX_FLUSH_ROWS)
    pixel_clock_hz = _int_in_range(
        display.get("pixel_clock_hz"),
        minimum=MIN_PIXEL_CLOCK_HZ,
        maximum=MAX_PIXEL_CLOCK_HZ,
    )
    if flush_rows is not None:
        payload["flush_rows"] = flush_rows
    if pixel_clock_hz is not None:
        payload["pixel_clock_hz"] = pixel_clock_hz
    return payload


def _micro_vad_payload(values: dict[str, Any]) -> dict[str, int]:
    micro_vad = values.get("micro_vad")
    if not isinstance(micro_vad, dict):
        return {}
    payload: dict[str, int] = {}
    pause_ms = _int_in_range(micro_vad.get("pause_ms"), minimum=80, maximum=3000)
    energy_threshold = _int_in_range(micro_vad.get("energy_threshold"), minimum=50, maximum=20000)
    if pause_ms is not None:
        payload["pause_ms"] = pause_ms
    if energy_threshold is not None:
        payload["energy_threshold"] = energy_threshold
    return payload


def _volume_payload(values: dict[str, Any]) -> dict[str, int]:
    audio = values.get("audio")
    output = audio.get("output") if isinstance(audio, dict) else None
    if not isinstance(output, dict):
        return {}
    volume_percent = _int_in_range(output.get("volume_percent"), minimum=0, maximum=100)
    return {"volume_percent": volume_percent} if volume_percent is not None else {}


def _mute_payload(values: dict[str, Any]) -> dict[str, bool]:
    audio = values.get("audio")
    output = audio.get("output") if isinstance(audio, dict) else None
    if not isinstance(output, dict) or not isinstance(output.get("muted"), bool):
        return {}
    return {"muted": bool(output["muted"])}


def command_payloads_for_config(values: dict[str, Any]) -> list[tuple[str, str, dict[str, object]]]:
    commands: list[tuple[str, str, dict[str, object]]] = []
    display = _display_payload(values)
    if display:
        commands.append(("endpoint.display.tuning", "endpoint.display.tuning.set", display))
    micro_vad = _micro_vad_payload(values)
    if micro_vad:
        commands.append(("endpoint.micro_vad", "endpoint.micro_vad.set", micro_vad))
    volume = _volume_payload(values)
    if volume:
        commands.append(("endpoint.volume", "endpoint.volume.set", volume))
    mute = _mute_payload(values)
    if mute:
        commands.append(("endpoint.mute", "endpoint.mute", mute))
    restart = values.get("restart")
    if isinstance(restart, dict) and restart.get("on_apply") is True:
        commands.append(("endpoint.restart", "endpoint.restart", {"reason": "runtime_config"}))
    return commands


class EndpointRuntimeConfig:
    def __init__(
        self,
        *,
        board_profiles: dict[str, dict[str, Any]] | None = None,
        endpoints: dict[str, dict[str, Any]] | None = None,
        path: Path | None = None,
    ) -> None:
        self._board_profiles = board_profiles or {}
        self._endpoints = endpoints or {}
        self._path = path
        self._signature: ConfigFileSignature | None = None
        if path is not None:
            self.reload(force=True)

    def config_for(self, *, endpoint_id: str, board_profile: str | None = None) -> dict[str, Any]:
        self.reload_if_changed()
        board_config = self._board_profiles.get(board_profile or "", {})
        endpoint_config = self._endpoints.get(endpoint_id, {})
        return _merge_config(board_config, endpoint_config)

    def command_payloads_for(self, *, endpoint_id: str, board_profile: str | None = None) -> list[tuple[str, str, dict[str, object]]]:
        return command_payloads_for_config(self.config_for(endpoint_id=endpoint_id, board_profile=board_profile))

    def reload_if_changed(self) -> bool:
        return self.reload(force=False)

    def reload(self, *, force: bool = False) -> bool:
        if self._path is None:
            return False
        signature = _config_file_signature(self._path)
        if not force and signature == self._signature:
            return False
        parsed = _load_endpoint_runtime_config_parts(self._path)
        if parsed is None:
            return False
        board_profiles, endpoints = parsed
        changed = force or signature != self._signature
        self._board_profiles = board_profiles
        self._endpoints = endpoints
        self._signature = signature
        return changed


def _config_file_signature(path: Path) -> ConfigFileSignature | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _load_endpoint_runtime_config_parts(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]] | None:
    if not path.exists():
        return {}, {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Endpoint runtime config ignored: path=%s error=%s", path, exc)
        return None
    if not isinstance(raw, dict):
        log.warning("Endpoint runtime config ignored: path=%s reason=root_not_object", path)
        return None
    board_profiles = raw.get("board_profiles")
    endpoints = raw.get("endpoints")
    return (
        {
            str(profile): _non_secret_mapping(values)
            for profile, values in board_profiles.items()
            if isinstance(profile, str) and isinstance(values, dict)
        }
        if isinstance(board_profiles, dict)
        else {},
        {
            str(endpoint_id): _non_secret_mapping(values)
            for endpoint_id, values in endpoints.items()
            if isinstance(endpoint_id, str) and isinstance(values, dict)
        }
        if isinstance(endpoints, dict)
        else {},
    )


def load_endpoint_runtime_config(path: Path) -> EndpointRuntimeConfig:
    return EndpointRuntimeConfig(path=path)
