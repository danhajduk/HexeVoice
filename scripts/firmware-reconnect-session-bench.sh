#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:9004}"
OUTPUT="${OUTPUT:-$ROOT_DIR/docs/firmware-reconnect-session-results.json}"
OPERATOR="${OPERATOR:-${USER:-operator}}"
RELEASE_ID="${RELEASE_ID:-$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)}"
TIMEOUT_S="${TIMEOUT_S:-3}"
PROFILE_ARG=""
RECORDS_FILE=""

SCENARIO_IDS=(
  "backend_restart_idle"
  "endpoint_power_cycle"
  "wifi_loss_rejoin"
  "active_session_disconnect"
  "post_tts_cooldown"
  "wake_retry"
  "duplicate_session_prevention"
)

SCENARIO_TITLES=(
  "Backend restart while endpoint is idle"
  "Endpoint power cycle"
  "Wi-Fi loss and rejoin"
  "Active session disconnect"
  "Post-TTS cooldown"
  "Wake retry after rejected wake"
  "Duplicate session prevention"
)

SCENARIO_STEPS=(
  "Restart the backend while the endpoint is idle, then wait for heartbeat and voice WebSocket recovery."
  "Power-cycle the endpoint and wait for heartbeat, capabilities, and firmware version to return."
  "Temporarily block or disable Wi-Fi for the endpoint, restore Wi-Fi, then wait for backend reconnect."
  "Start a voice session, interrupt the endpoint or backend connection mid-session, then restore it."
  "Complete a TTS response near the microphone and try to trigger capture during the cooldown window."
  "Perform a below-threshold or rejected wake, then perform a valid wake."
  "Attempt overlapping local/backend session starts from the same endpoint."
)

SCENARIO_EXPECTED=(
  "Endpoint returns online with the same endpoint id, firmware version, and board profile."
  "Endpoint returns online with the expected board profile and no stale active session."
  "Endpoint reconnects using configured backoff and reports RSSI/IP metadata after rejoin."
  "Backend cancels or finalizes the interrupted session and the endpoint can start a fresh session."
  "Speaker tail does not start duplicate capture; a later local wake retry succeeds."
  "Rejected wake does not leave the endpoint stuck; valid retry starts exactly one session."
  "Only one active session is accepted and stale/duplicate session state is cleared after reconnect."
)

usage() {
  cat <<'USAGE'
Usage: scripts/firmware-reconnect-session-bench.sh [options]

Guided physical reconnect/session-boundary bench runner. It tests one selected
device/profile at a time, waits for the operator at every physical step, records
backend observations, and merges that profile into the release artifact.

Options:
  --backend-url URL          Backend URL, default: http://127.0.0.1:9004
  --profile PROFILE=ENDPOINT Run one profile directly, e.g. esp_box_3=esp-box-1
  --operator NAME           Operator recorded in the artifact
  --release-id ID           Release/build id recorded in the artifact
  --output PATH             Results artifact path
  --timeout-s SECONDS       API timeout, default: 3
  -h, --help                Show this help

Environment variables with the same names in uppercase may also be used:
BACKEND_URL, OPERATOR, RELEASE_ID, OUTPUT, TIMEOUT_S.
USAGE
}

while (($#)); do
  case "$1" in
    --backend-url)
      BACKEND_URL="$2"
      shift 2
      ;;
    --profile)
      PROFILE_ARG="$2"
      shift 2
      ;;
    --operator)
      OPERATOR="$2"
      shift 2
      ;;
    --release-id)
      RELEASE_ID="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    --timeout-s)
      TIMEOUT_S="$2"
      shift 2
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

curl_json() {
  curl -fsS --connect-timeout "$TIMEOUT_S" --max-time "$TIMEOUT_S" "$@"
}

read_key() {
  local prompt="$1"
  local key
  if [[ -t 0 ]]; then
    printf '%s' "$prompt" > /dev/tty
    IFS= read -r -s -n 1 key
    printf '%s\n' "$key" > /dev/tty
  else
    IFS= read -r key
    key="${key:0:1}"
  fi
  printf '%s' "$key"
}

read_line() {
  local prompt="$1"
  local line
  if [[ -t 0 ]]; then
    IFS= read -r -p "$prompt" line < /dev/tty
  else
    IFS= read -r line || line=""
  fi
  printf '%s' "$line"
}

load_profile_rows() {
  mapfile -t PROFILE_ROWS < <(
    {
      curl_json "${BACKEND_URL%/}/api/endpoints"
      echo
      curl_json "${BACKEND_URL%/}/api/voice/status" || echo "{}"
    } | python3 -c '
import json
import sys

raw = sys.stdin.read().splitlines()
endpoints_payload = json.loads(raw[0]) if raw else {}
voice_payload = json.loads(raw[1]) if len(raw) > 1 and raw[1].strip() else {}
connected = set(voice_payload.get("connected_endpoint_ids") or [])

for endpoint in endpoints_payload.get("endpoints", []):
    capabilities = endpoint.get("capabilities") or {}
    firmware = capabilities.get("firmware") or {}
    provisioning = capabilities.get("provisioning") or {}
    endpoint_id = endpoint.get("endpoint_id") or ""
    profile = firmware.get("board_profile") or firmware.get("profile") or ""
    name = endpoint.get("display_name") or provisioning.get("display_name") or endpoint_id
    version = endpoint.get("firmware_version") or firmware.get("version") or "unknown"
    state = endpoint.get("connection_state") or "unknown"
    voice = "connected" if endpoint_id in connected else "missing"
    if endpoint_id and profile:
        print("\t".join([profile, endpoint_id, name, version, state, voice]))
'
  )
}

choose_profile() {
  if [[ -n "$PROFILE_ARG" ]]; then
    if [[ "$PROFILE_ARG" != *=* ]]; then
      echo "--profile must be PROFILE=ENDPOINT_ID" >&2
      exit 2
    fi
    SELECTED_PROFILE="${PROFILE_ARG%%=*}"
    SELECTED_ENDPOINT="${PROFILE_ARG#*=}"
    return
  fi

  load_profile_rows
  if ((${#PROFILE_ROWS[@]} == 0)); then
    echo "No endpoint profiles reported by ${BACKEND_URL%/}." >&2
    exit 1
  fi

  echo
  echo "Detected devices"
  local index=1 row profile endpoint_id name version state voice
  for row in "${PROFILE_ROWS[@]}"; do
    IFS=$'\t' read -r profile endpoint_id name version state voice <<<"$row"
    printf '  [%s] %-12s %-18s %-18s FW %-8s %s/%s\n' "$index" "$profile" "$endpoint_id" "$name" "$version" "$state" "$voice"
    index=$((index + 1))
  done
  echo

  local choice
  while true; do
    choice="$(read_key "Choose one device number, or q to quit: ")"
    case "$choice" in
      q|Q)
        exit 0
        ;;
      [0-9])
        if ((choice >= 1 && choice <= ${#PROFILE_ROWS[@]})); then
          row="${PROFILE_ROWS[$((choice - 1))]}"
          IFS=$'\t' read -r SELECTED_PROFILE SELECTED_ENDPOINT _ <<<"$row"
          return
        fi
        ;;
    esac
    echo "Invalid selection."
  done
}

collect_scenario_result() {
  local scenario_id="$1"
  local title="$2"
  local step="$3"
  local expected="$4"
  local status="$5"
  local reason="$6"
  local note="$7"

  python3 - "$BACKEND_URL" "$TIMEOUT_S" "$SELECTED_PROFILE" "$SELECTED_ENDPOINT" "$scenario_id" "$title" "$step" "$expected" "$status" "$reason" "$note" <<'PY'
from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

base_url, timeout_s, profile, endpoint_id, scenario_id, title, step, expected, status, reason, note = sys.argv[1:12]
timeout = float(timeout_s)

def http_json(path: str):
    try:
        with urlopen(f"{base_url.rstrip('/')}{path}", timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}, None
    except HTTPError as exc:
        return exc.code, None, str(exc)
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return 0, None, str(exc)

endpoint_status, endpoint_payload, endpoint_error = http_json(f"/api/endpoint/status/{endpoint_id}")
voice_status, voice_payload, voice_error = http_json("/api/voice/status")

endpoint_observation = {
    "endpoint_status_code": endpoint_status,
    "endpoint_query_error": endpoint_error,
    "connection_state": None,
    "device_state": None,
    "firmware_version": None,
    "reported_board_profile": None,
    "online": False,
    "profile_matches": False,
}
if isinstance(endpoint_payload, dict):
    firmware = endpoint_payload.get("capabilities", {}).get("firmware", {})
    reported_profile = firmware.get("board_profile") or firmware.get("profile")
    endpoint_observation.update(
        {
            "connection_state": endpoint_payload.get("connection_state"),
            "device_state": endpoint_payload.get("device_state"),
            "firmware_version": endpoint_payload.get("firmware_version"),
            "reported_board_profile": reported_profile,
            "online": endpoint_payload.get("connection_state") == "online",
            "profile_matches": reported_profile == profile,
        }
    )

voice_observation = {
    "voice_status_code": voice_status,
    "voice_query_error": voice_error,
    "connection_count": voice_payload.get("connection_count") if isinstance(voice_payload, dict) else None,
    "connected_endpoint_ids": voice_payload.get("connected_endpoint_ids") if isinstance(voice_payload, dict) else None,
    "session_state": voice_payload.get("state_projection", {}).get("session_state") if isinstance(voice_payload, dict) else None,
    "endpoint_voice": (voice_payload.get("endpoints", {}) or {}).get(endpoint_id) if isinstance(voice_payload, dict) else None,
    "last_error": voice_payload.get("last_error") if isinstance(voice_payload, dict) else None,
    "event_diagnostics": (voice_payload.get("event_diagnostics") or [])[:5] if isinstance(voice_payload, dict) else [],
}

payload = {
    "id": scenario_id,
    "title": title,
    "status": status,
    "reason": reason,
    "operator_note": note or None,
    "operator_step": step,
    "expected": expected,
    "endpoint_observation": endpoint_observation,
    "voice_observation": voice_observation,
}
print(json.dumps(payload, separators=(",", ":")))
PY
}

print_observation_summary() {
  python3 - "$RECORDS_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

lines = [line for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]
scenario = json.loads(lines[-1])
endpoint = scenario["endpoint_observation"]
voice = scenario["voice_observation"]
print(
    "Observed:"
    f" endpoint={endpoint.get('connection_state')}"
    f" fw={endpoint.get('firmware_version')}"
    f" profile={endpoint.get('reported_board_profile')}"
    f" voice_session={voice.get('session_state')}"
    f" voice_connections={voice.get('connection_count')}"
)
last_error = voice.get("last_error")
if last_error:
    print(f"Last voice error: {last_error}")
diagnostics = voice.get("event_diagnostics") or []
if diagnostics:
    newest = diagnostics[0]
    print(f"Latest diagnostic: {newest.get('code')} - {newest.get('message')}")
PY
}

record_scenario() {
  local index="$1"
  local scenario_id="${SCENARIO_IDS[$index]}"
  local title="${SCENARIO_TITLES[$index]}"
  local step="${SCENARIO_STEPS[$index]}"
  local expected="${SCENARIO_EXPECTED[$index]}"
  local status reason note key

  echo
  echo "[$SELECTED_PROFILE/$SELECTED_ENDPOINT] $title"
  echo "Step: $step"
  echo "Expected: $expected"
  echo

  key="$(read_key "Do the step, then press Enter to observe. Press s to skip, q to quit: ")"
  case "$key" in
    q|Q)
      echo "Stopped before recording $scenario_id."
      exit 130
      ;;
    s|S)
      status="blocked"
      reason="operator_skipped_physical_step"
      note="$(read_line "Skip reason, optional: ")"
      ;;
    *)
      collect_scenario_result "$scenario_id" "$title" "$step" "$expected" "blocked" "observation_pending_result" "" >> "$RECORDS_FILE"
      print_observation_summary
      while true; do
        key="$(read_key "Record result: p pass, f fail, b blocked, q quit: ")"
        case "$key" in
          p|P) status="pass"; reason="operator_recorded_pass"; note=""; break ;;
          f|F) status="fail"; reason="operator_recorded_fail"; note="$(read_line "Failure note / follow-up, optional: ")"; break ;;
          b|B) status="blocked"; reason="operator_recorded_blocked"; note="$(read_line "Blocker note, optional: ")"; break ;;
          q|Q) echo "Stopped before recording $scenario_id result."; exit 130 ;;
          *) echo "Invalid result." ;;
        esac
      done
      sed -i '$d' "$RECORDS_FILE"
      ;;
  esac

  collect_scenario_result "$scenario_id" "$title" "$step" "$expected" "$status" "$reason" "$note" >> "$RECORDS_FILE"
  print_observation_summary
}

merge_profile_results() {
  python3 - "$OUTPUT" "$RECORDS_FILE" "$SELECTED_PROFILE" "$SELECTED_ENDPOINT" "$OPERATOR" "$RELEASE_ID" "$BACKEND_URL" <<'PY'
from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sys

output = Path(sys.argv[1])
records_path = Path(sys.argv[2])
profile = sys.argv[3]
endpoint_id = sys.argv[4]
operator = sys.argv[5]
release_id = sys.argv[6]
backend_url = sys.argv[7]

def summarize(scenarios: list[dict]) -> str:
    statuses = {scenario.get("status") for scenario in scenarios}
    if "fail" in statuses:
        return "fail"
    if "blocked" in statuses:
        return "blocked"
    return "pass"

if output.exists():
    report = json.loads(output.read_text(encoding="utf-8"))
else:
    report = {
        "schema_version": 1,
        "profiles": [],
        "follow_up_policy": "Create or link a repo task for every fail or blocked scenario before release approval.",
    }

records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
profile_result = {
    "profile": profile,
    "endpoint_id": endpoint_id,
    "status": summarize(records),
    "scenarios": records,
}

profiles = [item for item in report.get("profiles", []) if item.get("profile") != profile]
profiles.append(profile_result)
profiles.sort(key=lambda item: str(item.get("profile") or ""))

report.update(
    {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "release_id": release_id,
        "operator": operator,
        "backend_url": backend_url,
        "profiles": profiles,
        "overall_status": summarize(profiles),
        "follow_up_policy": "Create or link a repo task for every fail or blocked scenario before release approval.",
    }
)

output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"Wrote {output}")
print(f"{profile}/{endpoint_id}: {profile_result['status']}")
print(f"overall: {report['overall_status']}")
PY
}

main() {
  choose_profile
  RECORDS_FILE="$(mktemp)"
  trap 'rm -f "$RECORDS_FILE"' EXIT

  echo
  echo "Running bench for $SELECTED_PROFILE=$SELECTED_ENDPOINT"
  echo "Backend: ${BACKEND_URL%/}"
  echo "Output: $OUTPUT"
  echo "Operator: $OPERATOR"
  echo "Release: $RELEASE_ID"

  local index
  for index in "${!SCENARIO_IDS[@]}"; do
    record_scenario "$index"
  done

  echo
  merge_profile_results
}

main
