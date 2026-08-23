#!/usr/bin/env bash
set -euo pipefail

NODE_HOST="${NODE_HOST:-hexe.local}"
API_BASE_URL="${API_BASE_URL:-http://${NODE_HOST}:9004}"
CONNECT_TIMEOUT_S="${CONNECT_TIMEOUT_S:-5}"
HTTP_TIMEOUT_S="${HTTP_TIMEOUT_S:-20}"

usage() {
  cat <<'USAGE'
Usage: scripts/firmware-ota-menu.sh [--list|--all]

Lists HexeVoice endpoints, shows their firmware versions and update status,
then opens a menu to OTA one endpoint or every endpoint with an available update.

Environment:
  NODE_HOST          Node host, default: hexe.local
  API_BASE_URL      Backend URL, default: http://hexe.local:9004
  CONNECT_TIMEOUT_S Curl connect timeout, default: 5
  HTTP_TIMEOUT_S    Curl total request timeout, default: 20

Examples:
  scripts/firmware-ota-menu.sh
  scripts/firmware-ota-menu.sh --list
  scripts/firmware-ota-menu.sh --all
  API_BASE_URL=http://127.0.0.1:9004 scripts/firmware-ota-menu.sh
USAGE
}

curl_json() {
  curl -fsS \
    --connect-timeout "${CONNECT_TIMEOUT_S}" \
    --max-time "${HTTP_TIMEOUT_S}" \
    "$@"
}

fetch_endpoints() {
  curl_json "${API_BASE_URL%/}/api/endpoints"
}

load_endpoint_rows() {
  mapfile -t ENDPOINT_ROWS < <(
    fetch_endpoints | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
for endpoint in payload.get("endpoints", []):
    capabilities = endpoint.get("capabilities") or {}
    provisioning = capabilities.get("provisioning") or {}
    firmware = capabilities.get("firmware") or {}
    update = endpoint.get("firmware_update") or {}
    endpoint_id = endpoint.get("endpoint_id") or ""
    name = endpoint.get("display_name") or provisioning.get("display_name") or endpoint_id
    version = endpoint.get("firmware_version") or firmware.get("version") or "unknown"
    latest = update.get("latest_version") or ""
    filename = update.get("filename") or ""
    profile = update.get("profile") or update.get("board_profile") or firmware.get("board_profile") or ""
    connection_state = endpoint.get("connection_state") or "unknown"
    update_available = "yes" if update.get("update_available") else "no"
    reason = update.get("reason") or ""
    print("\t".join([endpoint_id, name, version, latest, update_available, filename, profile, connection_state, reason]))
'
  )
}

print_endpoints() {
  printf '\nNode API: %s\n\n' "${API_BASE_URL%/}"
  if ((${#ENDPOINT_ROWS[@]} == 0)); then
    echo "No endpoints reported by the node."
    return
  fi

  printf '%-4s %-24s %-24s %-10s %-10s %-12s %s\n' \
    "#" "Endpoint" "Name" "Version" "Update" "State" "Latest/Profile"
  printf '%-4s %-24s %-24s %-10s %-10s %-12s %s\n' \
    "---" "--------" "----" "-------" "------" "-----" "--------------"

  local index=1
  local row endpoint_id name version latest update_available filename profile connection_state reason
  for row in "${ENDPOINT_ROWS[@]}"; do
    IFS=$'\t' read -r endpoint_id name version latest update_available filename profile connection_state reason <<<"${row}"
    local target="${latest:-none}"
    if [[ -n "${profile}" ]]; then
      target="${target}/${profile}"
    fi
    printf '%-4s %-24s %-24s %-10s %-10s %-12s %s\n' \
      "${index}" "${endpoint_id}" "${name}" "${version}" "${update_available}" "${connection_state}" "${target}"
    ((index += 1))
  done
  echo
}

json_payload() {
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import json
import sys

endpoint_id, filename, version, profile = sys.argv[1:5]
payload = {
    "endpoint_id": endpoint_id,
    "filename": filename,
}
if version:
    payload["version"] = version
if profile:
    payload["profile"] = profile
print(json.dumps(payload))
PY
}

print_ota_response() {
  python3 -c '
import json
import sys

payload = json.load(sys.stdin)
accepted = payload.get("accepted")
endpoint_id = payload.get("endpoint_id") or "unknown"
version = payload.get("version") or "unknown"
reason = payload.get("reason") or ("accepted" if accepted else "unknown")
status = "accepted" if accepted else "rejected"
print(f"{endpoint_id}: OTA {status} version={version} reason={reason}")
'
}

push_ota() {
  local endpoint_id="$1"
  local filename="$2"
  local version="$3"
  local profile="$4"

  if [[ -z "${filename}" ]]; then
    echo "${endpoint_id}: no firmware artifact filename was reported; skipping." >&2
    return 1
  fi

  local payload
  payload="$(json_payload "${endpoint_id}" "${filename}" "${version}" "${profile}")"
  curl_json -X POST "${API_BASE_URL%/}/api/firmware/ota/push" \
    -H 'Content-Type: application/json' \
    -d "${payload}" | print_ota_response
}

push_one_by_index() {
  local selection="$1"
  if ! [[ "${selection}" =~ ^[0-9]+$ ]]; then
    echo "Select a numeric endpoint row." >&2
    return 1
  fi
  if ((selection < 1 || selection > ${#ENDPOINT_ROWS[@]})); then
    echo "Endpoint row ${selection} is out of range." >&2
    return 1
  fi

  local row endpoint_id name version latest update_available filename profile connection_state reason
  row="${ENDPOINT_ROWS[$((selection - 1))]}"
  IFS=$'\t' read -r endpoint_id name version latest update_available filename profile connection_state reason <<<"${row}"

  if [[ "${update_available}" != "yes" ]]; then
    read -r -p "${endpoint_id} does not report an available update. Send OTA anyway? [y/N] " confirm
    case "${confirm}" in
      y|Y|yes|YES) ;;
      *) echo "Skipped ${endpoint_id}."; return 0 ;;
    esac
  fi

  push_ota "${endpoint_id}" "${filename}" "${latest}" "${profile}"
}

push_all_updates() {
  local pushed=0
  local skipped=0
  local row endpoint_id name version latest update_available filename profile connection_state reason

  for row in "${ENDPOINT_ROWS[@]}"; do
    IFS=$'\t' read -r endpoint_id name version latest update_available filename profile connection_state reason <<<"${row}"
    if [[ "${update_available}" != "yes" || -z "${filename}" ]]; then
      echo "${endpoint_id}: no available OTA update; skipping."
      ((skipped += 1))
      continue
    fi
    if push_ota "${endpoint_id}" "${filename}" "${latest}" "${profile}"; then
      ((pushed += 1))
    else
      ((skipped += 1))
    fi
  done

  echo "Bulk OTA complete: pushed=${pushed} skipped=${skipped}"
}

menu() {
  while true; do
    load_endpoint_rows
    print_endpoints
    cat <<'MENU'
Menu:
  number  OTA one endpoint
  a       OTA all endpoints with an available update
  r       Refresh endpoint list
  q       Quit
MENU
    read -r -p "Choose: " choice
    case "${choice}" in
      a|A|all|ALL)
        push_all_updates
        read -r -p "Press Enter to continue..." _
        ;;
      r|R|refresh|REFRESH)
        ;;
      q|Q|quit|QUIT)
        return 0
        ;;
      *)
        push_one_by_index "${choice}" || true
        read -r -p "Press Enter to continue..." _
        ;;
    esac
  done
}

case "${1:-}" in
  -h|--help|help)
    usage
    exit 0
    ;;
  --list|list)
    load_endpoint_rows
    print_endpoints
    ;;
  --all|all)
    load_endpoint_rows
    print_endpoints
    push_all_updates
    ;;
  "")
    menu
    ;;
  *)
    echo "Unknown option: $1" >&2
    usage >&2
    exit 2
    ;;
esac
