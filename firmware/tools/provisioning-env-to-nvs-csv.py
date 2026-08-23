#!/usr/bin/env python3
"""Convert Hexe provisioning.env text files to ESP-IDF NVS CSV input."""

import csv
import sys
from pathlib import Path


DEFAULT_VALUES = {
    "ENDPOINT_ID": "",
    "DISPLAY_NAME": "",
    "BACKEND_HOST": "",
    "HTTP_PORT": "9004",
    "WS_PORT": "9004",
    "USE_TLS": "false",
    "WIFI_SSID": "",
    "WIFI_PASSWORD": "",
}

BYTE_LIMITS = {
    "ENDPOINT_ID": 63,
    "DISPLAY_NAME": 63,
    "BACKEND_HOST": 95,
    "WIFI_SSID": 32,
    "WIFI_PASSWORD": 64,
}


def load_env(path: Path) -> dict[str, str]:
    values = dict(DEFAULT_VALUES)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise SystemExit(f"Invalid provisioning line without '=': {raw_line}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key in values:
            values[key] = value
    return values


def require(values: dict[str, str], name: str, env_path: Path) -> str:
    value = values[name].strip()
    if not value:
        raise SystemExit(f"{name} must be set in {env_path}")
    return value


def port(values: dict[str, str], name: str, env_path: Path) -> int:
    raw_value = require(values, name, env_path)
    try:
        value = int(raw_value, 10)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer port") from exc
    if value < 1 or value > 65535:
        raise SystemExit(f"{name} must be between 1 and 65535")
    return value


def bool_u8(values: dict[str, str], name: str) -> int:
    raw_value = values[name].strip().lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return 1
    if raw_value in {"0", "false", "no", "off", ""}:
        return 0
    raise SystemExit(f"{name} must be true/false or 1/0")


def validate_lengths(values: dict[str, str]) -> None:
    for name, max_length in BYTE_LIMITS.items():
        if len(values[name].encode("utf-8")) > max_length:
            raise SystemExit(f"{name} is too long; max {max_length} bytes")


def write_csv(values: dict[str, str], csv_path: Path, env_path: Path) -> None:
    validate_lengths(values)

    endpoint_id = require(values, "ENDPOINT_ID", env_path)
    display_name = values["DISPLAY_NAME"].strip() or endpoint_id
    backend_host = require(values, "BACKEND_HOST", env_path)
    wifi_ssid = require(values, "WIFI_SSID", env_path)
    http_port = port(values, "HTTP_PORT", env_path)
    ws_port = port(values, "WS_PORT", env_path)
    use_tls = bool_u8(values, "USE_TLS")

    rows = [
        ["key", "type", "encoding", "value"],
        ["hexe_settings", "namespace", "", ""],
        ["endpoint_id", "data", "string", endpoint_id],
        ["display_name", "data", "string", display_name],
        ["backend_host", "data", "string", backend_host],
        ["http_port", "data", "i32", str(http_port)],
        ["ws_port", "data", "i32", str(ws_port)],
        ["use_tls", "data", "u8", str(use_tls)],
        ["wifi_ssid", "data", "string", wifi_ssid],
        ["wifi_password", "data", "string", values["WIFI_PASSWORD"]],
        ["provisioned", "data", "u8", "1"],
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: provisioning-env-to-nvs-csv.py provisioning.env provisioning.csv", file=sys.stderr)
        return 2

    env_path = Path(sys.argv[1])
    csv_path = Path(sys.argv[2])
    write_csv(load_env(env_path), csv_path, env_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
