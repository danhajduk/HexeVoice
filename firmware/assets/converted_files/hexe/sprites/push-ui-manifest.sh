#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"
FIRMWARE_DIR="${ROOT_DIR}/firmware"
MANIFEST_PATH="${MANIFEST_PATH:-${SCRIPT_DIR}/ui_manifest.json}"
ASSET_ID="${ASSET_ID:-ui_manifest}"
API_BASE="${API_BASE:-${OTA_API_BASE:-http://127.0.0.1:${API_PORT:-9004}}}"

yaml_value() {
  local section="$1"
  local key="$2"
  local config_path="${FIRMWARE_DIR}/config/endpoint.yaml"
  if [[ ! -f "${config_path}" ]]; then
    config_path="${FIRMWARE_DIR}/config/endpoint.example.yaml"
  fi
  awk -v section="${section}" -v key="${key}" '
    /^[^[:space:]#][^:]*:/ {
      current=$1
      sub(":", "", current)
    }
    current == section && $1 == key ":" {
      $1=""
      sub(/^[[:space:]]*/, "", $0)
      gsub(/^["'\''"]|["'\''"]$/, "", $0)
      print $0
      exit
    }
  ' "${config_path}"
}

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required tool: $1" >&2
    exit 1
  fi
}

require_tool curl
require_tool python3

if [[ ! -f "${MANIFEST_PATH}" ]]; then
  echo "Manifest not found: ${MANIFEST_PATH}" >&2
  exit 1
fi

ENDPOINT_ID="${ENDPOINT_ID:-$(yaml_value endpoint id)}"
if [[ -z "${ENDPOINT_ID}" ]]; then
  echo "Missing ENDPOINT_ID and could not read endpoint.id from firmware config." >&2
  exit 1
fi

upload_payload="$(mktemp)"
upload_response="$(mktemp)"
deliver_payload="$(mktemp)"
deliver_response="$(mktemp)"
cleanup() {
  rm -f "${upload_payload}" "${upload_response}" "${deliver_payload}" "${deliver_response}"
}
trap cleanup EXIT

python3 - "${MANIFEST_PATH}" "${ASSET_ID}" > "${upload_payload}" <<'PY'
import base64
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
asset_id = sys.argv[2]
payload = {
    "media_type": "sprite",
    "filename": manifest_path.name,
    "content_base64": base64.b64encode(manifest_path.read_bytes()).decode("ascii"),
    "asset_id": asset_id,
    "content_type": "application/json",
    "metadata": {"asset_class": "manifest"},
    "overwrite": True,
    "activate": True,
}
print(json.dumps(payload))
PY

echo "Uploading ${MANIFEST_PATH} to ${API_BASE%/} as asset_id=${ASSET_ID}"
curl -fsS -X POST "${API_BASE%/}/api/endpoint/media" \
  -H "Content-Type: application/json" \
  -d @"${upload_payload}" > "${upload_response}"

actual_asset_id="$(python3 - "${upload_response}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
print(payload["asset_id"])
PY
)"

python3 - "${ENDPOINT_ID}" > "${deliver_payload}" <<'PY'
import json
import sys

payload = {
    "endpoint_id": sys.argv[1],
    "overwrite": True,
    "activate": True,
}
print(json.dumps(payload))
PY

echo "Delivering ${actual_asset_id} to endpoint=${ENDPOINT_ID}"
curl -fsS -X POST "${API_BASE%/}/api/endpoint/media/${actual_asset_id}/deliver" \
  -H "Content-Type: application/json" \
  -d @"${deliver_payload}" > "${deliver_response}"

python3 - "${upload_response}" "${deliver_response}" <<'PY'
import json
import sys
from pathlib import Path

upload = json.loads(Path(sys.argv[1]).read_text())
deliver = json.loads(Path(sys.argv[2]).read_text())
print(
    "Uploaded: "
    f"asset_id={upload['asset_id']} filename={upload['filename']} "
    f"size={upload['size_bytes']} sha256={upload['sha256']}"
)
print(
    "Delivery: "
    f"accepted={deliver['accepted']} endpoint_id={deliver['endpoint_id']} "
    f"request_id={deliver.get('request_id')} status={deliver.get('status')} "
    f"reason={deliver.get('reason')}"
)
PY
