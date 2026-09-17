#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:9004}"
ENDPOINT_ID="${ENDPOINT_ID:-esp-box-1}"

curl --fail --silent --show-error -X POST "${API_BASE_URL%/}/api/endpoint/restart" \
  -H 'Content-Type: application/json' \
  -d "{\"endpoint_id\":\"${ENDPOINT_ID}\"}"

echo
