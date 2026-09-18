#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT
    / "firmware/assets/waveshare_p4_wifi6_touch_lcd_7b/assets/config/screens_layout.yaml"
)
VALID_DURATIONS = (5, 10, 20, 30)
MEDIA_ACTION = "__recreate_media__"
CONFIG_ACTION = "__recreate_config__"
MEDIA_GENERATOR = ROOT / "firmware/tools/generate-board-media-assets.py"
BOARD_PROFILE = "waveshare_p4_wifi6_touch_lcd_7b"


def load_screen_ids(index_path: Path) -> list[str]:
    try:
        source = index_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"could not read screen index {index_path}: {exc}") from exc

    screen_ids: list[str] = []
    for relative_name in re.findall(r"!include\s+([^\s#]+)", source):
        screen_path = (index_path.parent / relative_name).resolve()
        try:
            screen_source = screen_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"could not read included screen {screen_path}: {exc}") from exc
        match = re.search(r"(?m)^id:\s*['\"]?([^\s'\"#]+)", screen_source)
        if match is None:
            raise RuntimeError(f"screen file has no id: {screen_path}")
        screen_id = match.group(1)
        if screen_id in screen_ids:
            raise RuntimeError(f"duplicate screen id: {screen_id}")
        screen_ids.append(screen_id)

    if not screen_ids:
        raise RuntimeError(f"no !include screen entries found in {index_path}")
    return screen_ids


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"could not reach {base_url}: {exc.reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("API returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("API returned a non-object response")
    return parsed


def choose(
    label: str,
    options: list[tuple[str, str]],
    *,
    columns: int = 1,
    default: str | None = None,
    shortcuts: list[tuple[str, str, str]] | None = None,
) -> str:
    if columns < 1:
        raise ValueError("columns must be at least 1")
    default_description = next((description for value, description in options if value == default), None)
    while True:
        print(f"\n{label}")
        entries = [f"{index:>2}. {description}" for index, (_, description) in enumerate(options, start=1)]
        column_width = max((len(entry) for entry in entries), default=0) + 4
        for start in range(0, len(entries), columns):
            print("  " + "".join(entry.ljust(column_width) for entry in entries[start : start + columns]).rstrip())
        for key, _, description in shortcuts or []:
            print(f"   {key}. {description}")
        print("   q. Quit")
        prompt = f"Choose [{default_description}]: " if default_description is not None else "Choose: "
        selection = input(prompt).strip().lower()
        if not selection and default is not None:
            return default
        if selection in {"q", "quit", "exit"}:
            raise KeyboardInterrupt
        for key, value, _ in shortcuts or []:
            if selection == key.lower():
                return value
        if selection.isdigit() and 1 <= int(selection) <= len(options):
            return options[int(selection) - 1][0]
        print("Invalid choice.")


def select_endpoint(base_url: str, timeout: float) -> str:
    response = request_json(base_url, "/api/endpoints", timeout=timeout)
    endpoints = response.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise RuntimeError("the backend reports no registered endpoints")
    options: list[tuple[str, str]] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        endpoint_id = endpoint.get("endpoint_id")
        if not isinstance(endpoint_id, str) or not endpoint_id:
            continue
        name = endpoint.get("display_name") or endpoint_id
        state = endpoint.get("connection_state") or "unknown"
        options.append((endpoint_id, f"{name} [{endpoint_id}] ({state})"))
    if not options:
        raise RuntimeError("the backend returned no usable endpoints")
    return options[0][0] if len(options) == 1 else choose("Endpoint", options)


def send_screen(
    base_url: str,
    endpoint_id: str,
    screen_id: str,
    duration: int,
    timeout: float,
) -> dict[str, Any]:
    return request_json(
        base_url,
        "/api/endpoint/ui/screen",
        method="POST",
        payload={
            "endpoint_id": endpoint_id,
            "screen_id": screen_id,
            "duration_seconds": duration,
        },
        timeout=timeout,
    )


def sync_endpoint_media(base_url: str, endpoint_id: str, timeout: float) -> dict[str, Any]:
    return request_json(
        base_url,
        "/api/endpoint/media/sync",
        method="POST",
        payload={"endpoint_id": endpoint_id},
        timeout=timeout,
    )


def recreate_media_files() -> None:
    try:
        subprocess.run(
            [sys.executable, str(MEDIA_GENERATOR), BOARD_PROFILE],
            cwd=ROOT,
            check=True,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start media generator: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"media generator failed with exit code {exc.returncode}") from exc


def recreate_config_files() -> None:
    try:
        subprocess.run(
            [sys.executable, str(MEDIA_GENERATOR), BOARD_PROFILE, "--config-only"],
            cwd=ROOT,
            check=True,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start config generator: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"config generator failed with exit code {exc.returncode}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview a configured UI screen on a HexeVoice endpoint.")
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("API_BASE_URL", "http://hexe.local:9004"),
        help="HexeVoice backend URL (default: API_BASE_URL or http://hexe.local:9004).",
    )
    parser.add_argument("--endpoint-id", default=os.environ.get("HEXEVOICE_ENDPOINT_ID"))
    parser.add_argument("--screen", help="Skip the screen menu and select this screen ID.")
    parser.add_argument("--duration", type=int, choices=VALID_DURATIONS, help="Override duration in seconds.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to screens_layout.yaml.")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds.")
    parser.add_argument("--list-screens", action="store_true", help="Print configured screen IDs and exit.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        screens = load_screen_ids(args.config)
        if args.list_screens:
            print("\n".join(screens))
            return 0
        if args.screen is not None and args.screen not in screens:
            raise RuntimeError(f"unknown screen {args.screen!r}; choose from: {', '.join(screens)}")

        endpoint_id = args.endpoint_id
        while True:
            screen_id = args.screen or choose(
                "Screen",
                [(item, item) for item in screens],
                columns=3,
                shortcuts=[
                    ("m", MEDIA_ACTION, "Recreate media files"),
                    ("n", CONFIG_ACTION, "Recreate config and manifest"),
                ],
            )
            if screen_id in {MEDIA_ACTION, CONFIG_ACTION}:
                if screen_id == MEDIA_ACTION:
                    print("\nRecreating P4 media files...")
                    recreate_media_files()
                    print("Media files recreated.")
                else:
                    print("\nRecreating P4 config and manifest...")
                    recreate_config_files()
                    print("Config and manifest recreated.")
                if endpoint_id is None:
                    endpoint_id = select_endpoint(args.api_base_url, args.timeout)
                response = sync_endpoint_media(args.api_base_url, endpoint_id, args.timeout)
                if not response.get("accepted"):
                    reason = response.get("reason") or response.get("status") or "asset sync rejected"
                    raise RuntimeError(str(reason))
                print(f"Media sync requested for {endpoint_id!r}.")
                continue
            if endpoint_id is None:
                endpoint_id = select_endpoint(args.api_base_url, args.timeout)
            duration = args.duration or int(
                choose(
                    "Duration",
                    [(str(value), f"{value} seconds") for value in VALID_DURATIONS],
                    columns=3,
                    default="5",
                )
            )
            response = send_screen(args.api_base_url, endpoint_id, screen_id, duration, args.timeout)
            if not response.get("accepted"):
                reason = response.get("reason") or response.get("status") or "request rejected"
                raise RuntimeError(str(reason))
            print(f"Showing {screen_id!r} on {endpoint_id!r} for {duration} seconds.")
            if args.screen is not None:
                return 0
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
