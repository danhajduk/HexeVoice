#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:9004}"
ENDPOINT_ID="${ENDPOINT_ID:-esp-pe-1}"

usage() {
  cat <<'USAGE'
Usage: scripts/set-micro-vad-pause.sh <pause_ms>

Sets the endpoint micro-VAD pause threshold. The endpoint persists this value
after it receives the command.

Environment:
  API_BASE_URL   Backend URL, default: http://127.0.0.1:9004
  ENDPOINT_ID    Endpoint id, default: esp-pe-1

Examples:
  scripts/set-micro-vad-pause.sh 190
  API_BASE_URL=http://10.0.0.100:9004 scripts/set-micro-vad-pause.sh 180
  ENDPOINT_ID=esp-box-1 scripts/set-micro-vad-pause.sh 220
USAGE
}

PAUSE_MS="${1:-}"

if [[ -z "$PAUSE_MS" || "$PAUSE_MS" == "-h" || "$PAUSE_MS" == "--help" ]]; then
  usage
  exit 0
fi

if ! [[ "$PAUSE_MS" =~ ^[0-9]+$ ]]; then
  echo "pause_ms must be a number, got: $PAUSE_MS" >&2
  exit 2
fi

if (( PAUSE_MS < 80 || PAUSE_MS > 1000 )); then
  echo "pause_ms must be between 80 and 1000, got: $PAUSE_MS" >&2
  exit 2
fi

curl -sS -X POST "${API_BASE_URL%/}/api/endpoint/micro-vad" \
  -H 'Content-Type: application/json' \
  -d "{\"endpoint_id\":\"${ENDPOINT_ID}\",\"pause_ms\":${PAUSE_MS}}"
echo
