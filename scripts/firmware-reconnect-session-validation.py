#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


SUPPORTED_PROFILES = {
    "esp_box_3": "esp-box-1",
    "ha_voice_pe": "esp-pe-1",
}

SCENARIOS = [
    {
        "id": "backend_restart_idle",
        "title": "Backend restart while endpoint is idle",
        "operator_step": "Restart the backend while the endpoint is idle, then wait for heartbeat and voice WebSocket recovery.",
        "expected": "Endpoint returns online with the same endpoint id, firmware version, and board profile.",
    },
    {
        "id": "endpoint_power_cycle",
        "title": "Endpoint power cycle",
        "operator_step": "Power-cycle the endpoint and wait for heartbeat, capabilities, and firmware version to return.",
        "expected": "Endpoint returns online with the expected board profile and no stale active session.",
    },
    {
        "id": "wifi_loss_rejoin",
        "title": "Wi-Fi loss and rejoin",
        "operator_step": "Temporarily block or disable Wi-Fi for the endpoint, restore Wi-Fi, then wait for backend reconnect.",
        "expected": "Endpoint reconnects using configured backoff and reports RSSI/IP metadata after rejoin.",
    },
    {
        "id": "active_session_disconnect",
        "title": "Active session disconnect",
        "operator_step": "Start a voice session, interrupt the endpoint or backend connection mid-session, then restore it.",
        "expected": "Backend cancels or finalizes the interrupted session and the endpoint can start a fresh session.",
    },
    {
        "id": "post_tts_cooldown",
        "title": "Post-TTS cooldown",
        "operator_step": "Complete a TTS response near the microphone and try to trigger capture during the cooldown window.",
        "expected": "Speaker tail does not start duplicate capture; a later local wake retry succeeds.",
    },
    {
        "id": "wake_retry",
        "title": "Wake retry after rejected wake",
        "operator_step": "Perform a below-threshold or rejected wake, then perform a valid wake.",
        "expected": "Rejected wake does not leave the endpoint stuck; valid retry starts exactly one session.",
    },
    {
        "id": "duplicate_session_prevention",
        "title": "Duplicate session prevention",
        "operator_step": "Attempt overlapping local/backend session starts from the same endpoint.",
        "expected": "Only one active session is accepted and stale/duplicate session state is cleared after reconnect.",
    },
]


def http_json(base_url: str, path: str, timeout_s: float) -> tuple[int, dict | None, str | None]:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        with urlopen(url, timeout=timeout_s) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}, None
    except HTTPError as exc:
        return exc.code, None, str(exc)
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return 0, None, str(exc)


def parse_profile(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--profile must be PROFILE=ENDPOINT_ID")
    profile, endpoint_id = value.split("=", 1)
    profile = profile.strip()
    endpoint_id = endpoint_id.strip()
    if not profile or not endpoint_id:
        raise argparse.ArgumentTypeError("--profile must include non-empty profile and endpoint id")
    return profile, endpoint_id


def parse_result_override(value: str) -> tuple[str, str, str, str | None]:
    parts = value.split("=", 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("--result must be PROFILE:SCENARIO=STATUS[:NOTE]")
    selector, status_note = parts
    if ":" not in selector:
        raise argparse.ArgumentTypeError("--result selector must be PROFILE:SCENARIO")
    profile, scenario = selector.split(":", 1)
    status_parts = status_note.split(":", 1)
    status = status_parts[0].strip()
    note = status_parts[1].strip() if len(status_parts) == 2 else None
    if status not in {"pass", "fail", "blocked"}:
        raise argparse.ArgumentTypeError("--result status must be pass, fail, or blocked")
    return profile.strip(), scenario.strip(), status, note


def endpoint_observation(base_url: str, endpoint_id: str, profile: str, timeout_s: float) -> dict:
    status, payload, error = http_json(base_url, f"/api/endpoint/status/{endpoint_id}", timeout_s)
    observation = {
        "endpoint_status_code": status,
        "endpoint_query_error": error,
        "connection_state": None,
        "device_state": None,
        "firmware_version": None,
        "reported_board_profile": None,
        "online": False,
        "profile_matches": False,
    }
    if isinstance(payload, dict):
        firmware = payload.get("capabilities", {}).get("firmware", {})
        reported_profile = firmware.get("board_profile") or firmware.get("profile")
        observation.update(
            {
                "connection_state": payload.get("connection_state"),
                "device_state": payload.get("device_state"),
                "firmware_version": payload.get("firmware_version"),
                "reported_board_profile": reported_profile,
                "online": payload.get("connection_state") == "online",
                "profile_matches": reported_profile == profile,
            }
        )
    return observation


def voice_observation(base_url: str, timeout_s: float) -> dict:
    status, payload, error = http_json(base_url, "/api/voice/status", timeout_s)
    return {
        "voice_status_code": status,
        "voice_query_error": error,
        "connection_count": payload.get("connection_count") if isinstance(payload, dict) else None,
        "connected_endpoint_ids": payload.get("connected_endpoint_ids") if isinstance(payload, dict) else None,
        "session_state": payload.get("state_projection", {}).get("session_state") if isinstance(payload, dict) else None,
    }


def scenario_result(
    *,
    profile: str,
    endpoint_id: str,
    scenario: dict,
    base_url: str,
    timeout_s: float,
    non_interactive: bool,
    override: tuple[str, str | None] | None,
) -> dict:
    skipped = False
    if not non_interactive and override is None:
        print(f"\n[{profile}] {scenario['title']}")
        print(f"Step: {scenario['operator_step']}")
        print(f"Expected: {scenario['expected']}")
        choice = input("Perform the step, then press Enter to collect backend observations, or type s to skip... ")
        skipped = choice.strip().lower() in {"s", "skip"}

    endpoint = endpoint_observation(base_url, endpoint_id, profile, timeout_s)
    voice = voice_observation(base_url, timeout_s)

    if override is not None:
        status, note = override
        reason = note or "operator_recorded_result"
    elif skipped:
        status = "blocked"
        reason = "operator_skipped_physical_step"
    elif non_interactive:
        status = "blocked"
        reason = "non_interactive_run_requires_physical_operator_result"
    elif endpoint["online"] and endpoint["profile_matches"]:
        status = "pass"
        reason = "endpoint_returned_online_with_expected_profile"
    else:
        status = "fail"
        reason = "endpoint_did_not_return_online_with_expected_profile"

    return {
        "id": scenario["id"],
        "title": scenario["title"],
        "status": status,
        "reason": reason,
        "operator_step": scenario["operator_step"],
        "expected": scenario["expected"],
        "endpoint_observation": endpoint,
        "voice_observation": voice,
    }


def summarize_profile(scenarios: list[dict]) -> str:
    statuses = {scenario["status"] for scenario in scenarios}
    if "fail" in statuses:
        return "fail"
    if "blocked" in statuses:
        return "blocked"
    return "pass"


def build_report(args: argparse.Namespace) -> dict:
    profiles = dict(SUPPORTED_PROFILES)
    for profile, endpoint_id in args.profile:
        profiles[profile] = endpoint_id

    overrides: dict[tuple[str, str], tuple[str, str | None]] = {}
    for profile, scenario, status, note in args.result:
        overrides[(profile, scenario)] = (status, note)

    profile_results = []
    for profile, endpoint_id in sorted(profiles.items()):
        scenarios = [
            scenario_result(
                profile=profile,
                endpoint_id=endpoint_id,
                scenario=scenario,
                base_url=args.backend_url,
                timeout_s=args.timeout_s,
                non_interactive=args.non_interactive,
                override=overrides.get((profile, scenario["id"])),
            )
            for scenario in SCENARIOS
        ]
        profile_results.append(
            {
                "profile": profile,
                "endpoint_id": endpoint_id,
                "status": summarize_profile(scenarios),
                "scenarios": scenarios,
            }
        )

    overall_status = summarize_profile([{"status": profile["status"]} for profile in profile_results])
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "release_id": args.release_id,
        "operator": args.operator,
        "backend_url": args.backend_url,
        "overall_status": overall_status,
        "profiles": profile_results,
        "follow_up_policy": "Create or link a repo task for every fail or blocked scenario before release approval.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Record firmware reconnect/session-boundary field validation results.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:9004")
    parser.add_argument("--profile", action="append", type=parse_profile, default=[])
    parser.add_argument("--result", action="append", type=parse_result_override, default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-s", type=float, default=3.0)
    parser.add_argument("--operator", default=None)
    parser.add_argument("--release-id", default=None)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(args)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.json or args.output is None:
        print(rendered)
    return 0 if report["overall_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
