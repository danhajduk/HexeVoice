#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:9004}"
ENDPOINT_ID="${ENDPOINT_ID:-esp-pe-1}"

usage() {
  cat <<'USAGE'
Usage: scripts/simulate-pe-led.sh <pattern> [duration_ms]

Patterns:
  all boot wifi backend listening capturing thinking replying ota completed
  cancelled muted speaker_silent volume color error disconnected off

Environment:
  API_BASE_URL   Backend URL, default: http://127.0.0.1:9004
  ENDPOINT_ID    Endpoint id, default: esp-pe-1

Examples:
  scripts/simulate-pe-led.sh capturing
  scripts/simulate-pe-led.sh muted 3000
  API_BASE_URL=http://10.0.0.100:9004 scripts/simulate-pe-led.sh all 900
USAGE
}

PATTERN="${1:-}"
DURATION_MS="${2:-1200}"

if [[ -z "$PATTERN" || "$PATTERN" == "-h" || "$PATTERN" == "--help" ]]; then
  usage
  exit 0
fi

case "$PATTERN" in
  all|boot|wifi|backend|listening|capturing|thinking|replying|ota|completed|cancelled|muted|speaker_silent|volume|color|error|disconnected|off)
    ;;
  *)
    echo "Unknown LED pattern: $PATTERN" >&2
    usage >&2
    exit 2
    ;;
esac

if ! [[ "$DURATION_MS" =~ ^[0-9]+$ ]]; then
  echo "duration_ms must be a number, got: $DURATION_MS" >&2
  exit 2
fi

if (( DURATION_MS < 300 || DURATION_MS > 5000 )); then
  echo "duration_ms must be between 300 and 5000, got: $DURATION_MS" >&2
  exit 2
fi

curl -sS -X POST "${API_BASE_URL%/}/api/endpoint/led/simulate" \
  -H 'Content-Type: application/json' \
  -d "{\"endpoint_id\":\"${ENDPOINT_ID}\",\"pattern\":\"${PATTERN}\",\"duration_ms\":${DURATION_MS}}"
echo
