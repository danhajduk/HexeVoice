#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"
FIRMWARE_DIR="${ROOT_DIR}/firmware"
MANIFEST_PATH="${MANIFEST_PATH:-${SCRIPT_DIR}/ui_manifest.json}"
API_BASE="${API_BASE:-${OTA_API_BASE:-http://127.0.0.1:${API_PORT:-9004}}}"

usage() {
  cat <<EOF
Usage: $(basename "$0") SPRITE_NAME

Uploads a sprite RGB565 file and its alpha mask when present.
The sprite is resolved from ui_manifest.json avatars[SPRITE_NAME] first, then
from sprites[] entries by id/name/filename stem.

Environment:
  ENDPOINT_ID     Endpoint id; defaults from firmware/config/endpoint.yaml
  MANIFEST_PATH   Manifest to inspect; default: ${MANIFEST_PATH}
  API_BASE        Backend API base; default: ${API_BASE}
EOF
}

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

if [[ $# -eq 1 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
  usage
  exit 0
fi

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

require_tool curl
require_tool python3

SPRITE_NAME="$1"
if [[ ! "${SPRITE_NAME}" =~ ^[A-Za-z0-9_.-]+$ || "${SPRITE_NAME}" == .* || "${SPRITE_NAME}" == *..* ]]; then
  echo "Invalid sprite name: ${SPRITE_NAME}" >&2
  exit 1
fi
if [[ ! -f "${MANIFEST_PATH}" ]]; then
  echo "Manifest not found: ${MANIFEST_PATH}" >&2
  exit 1
fi

ENDPOINT_ID="${ENDPOINT_ID:-$(yaml_value endpoint id)}"
if [[ -z "${ENDPOINT_ID}" ]]; then
  echo "Missing ENDPOINT_ID and could not read endpoint.id from firmware config." >&2
  exit 1
fi

work_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${work_dir}"
}
trap cleanup EXIT

resolved_assets="${work_dir}/assets.json"
python3 - "${MANIFEST_PATH}" "${SCRIPT_DIR}" "${SPRITE_NAME}" > "${resolved_assets}" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
sprites_dir = Path(sys.argv[2])
name = sys.argv[3]
manifest = json.loads(manifest_path.read_text())

layer = None
avatars = manifest.get("avatars")
if isinstance(avatars, dict) and isinstance(avatars.get(name), dict):
    layer = dict(avatars[name])

if layer is None:
    for item in manifest.get("sprites", []):
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "")
        stem = Path(filename).stem
        if item.get("id") == name or item.get("name") == name or stem == name:
            layer = dict(item)
            break

if layer is None:
    candidate = sprites_dir / f"{name}.rgb565"
    if candidate.exists():
      layer = {"filename": candidate.name, "width": 320, "height": 240}

if layer is None:
    raise SystemExit(f"Could not find sprite '{name}' in {manifest_path}")

filename = layer.get("filename")
if not isinstance(filename, str) or not filename:
    raise SystemExit(f"Sprite '{name}' has no filename")
width = int(layer.get("width") or 0)
height = int(layer.get("height") or 0)
if width <= 0 or height <= 0:
    raise SystemExit(f"Sprite '{name}' needs width and height in the manifest")

assets = [
    {
        "kind": "rgb565",
        "asset_id": name,
        "path": str(sprites_dir / filename),
        "metadata": {"asset_class": "sprite", "pixel_format": "rgb565", "width": width, "height": height},
    }
]

alpha = layer.get("alpha")
alpha_format = layer.get("alpha_format")
if isinstance(alpha, str) and alpha:
    suffix = Path(alpha).suffix.lower().lstrip(".")
    assets.append(
        {
            "kind": "alpha",
            "asset_id": f"{name}_alpha",
            "path": str(sprites_dir / alpha),
            "metadata": {"asset_class": "sprite_alpha", "alpha_format": alpha_format or suffix or "alpha8"},
        }
    )
else:
    matches = [path for path in (sprites_dir / f"{Path(filename).stem}.alpha8", sprites_dir / f"{Path(filename).stem}.alpha1") if path.exists()]
    if len(matches) > 1:
        raise SystemExit(f"Both alpha8 and alpha1 masks exist for '{name}'; declare alpha in the manifest")
    if matches:
        suffix = matches[0].suffix.lower().lstrip(".")
        assets.append(
            {
                "kind": "alpha",
                "asset_id": f"{name}_alpha",
                "path": str(matches[0]),
                "metadata": {"asset_class": "sprite_alpha", "alpha_format": suffix},
            }
        )

for asset in assets:
    path = Path(asset["path"])
    if not path.is_file():
        raise SystemExit(f"Missing {asset['kind']} file: {path}")
    asset["filename"] = path.name

print(json.dumps(assets))
PY

python3 - "${resolved_assets}" "${work_dir}" <<'PY'
import base64
import json
import sys
from pathlib import Path

assets = json.loads(Path(sys.argv[1]).read_text())
work_dir = Path(sys.argv[2])
for index, asset in enumerate(assets):
    path = Path(asset["path"])
    payload = {
        "media_type": "sprite",
        "filename": path.name,
        "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "asset_id": asset["asset_id"],
        "content_type": "application/octet-stream",
        "metadata": asset["metadata"],
        "overwrite": True,
        "activate": True,
    }
    (work_dir / f"upload-{index}.json").write_text(json.dumps(payload), encoding="utf-8")
PY

count="$(python3 - "${resolved_assets}" <<'PY'
import json
import sys
from pathlib import Path
print(len(json.loads(Path(sys.argv[1]).read_text())))
PY
)"

for index in $(seq 0 $((count - 1))); do
  upload_response="${work_dir}/upload-response-${index}.json"
  deliver_payload="${work_dir}/deliver-${index}.json"
  deliver_response="${work_dir}/deliver-response-${index}.json"
  asset_label="$(python3 - "${resolved_assets}" "${index}" <<'PY'
import json
import sys
from pathlib import Path
asset = json.loads(Path(sys.argv[1]).read_text())[int(sys.argv[2])]
print(f"{asset['asset_id']} ({asset['filename']})")
PY
)"
  echo "Uploading ${asset_label} to ${API_BASE%/}"
  curl -fsS -X POST "${API_BASE%/}/api/endpoint/media" \
    -H "Content-Type: application/json" \
    -d @"${work_dir}/upload-${index}.json" > "${upload_response}"

  actual_asset_id="$(python3 - "${upload_response}" <<'PY'
import json
import sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text())["asset_id"])
PY
)"

  python3 - "${ENDPOINT_ID}" > "${deliver_payload}" <<'PY'
import json
import sys
print(json.dumps({"endpoint_id": sys.argv[1], "overwrite": True, "activate": True}))
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
done
