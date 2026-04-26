#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${IDF_TARGET:-esp32s3}"
EXPORT_AFTER_BUILD="${EXPORT_AFTER_BUILD:-1}"
COMMAND="${1:-build}"
CONVERTER_PYTHON="python3"
RUNTIME_FIRMWARE_DIR="${ROOT_DIR}/../runtime/firmware"
RUNTIME_FIRMWARE_BIN="${RUNTIME_FIRMWARE_DIR}/hexe_firmware.bin"
RUNTIME_FIRMWARE_MANIFEST="${RUNTIME_FIRMWARE_DIR}/manifest.json"
OTA_API_BASE="${OTA_API_BASE:-http://127.0.0.1:${API_PORT:-9004}}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [build|push]

Commands:
  build  Build firmware and refresh runtime/export artifacts. This is the default.
  push   Build firmware, refresh artifacts, then push OTA to the endpoint.

Environment:
  OTA_API_BASE   Backend API base URL for push mode. Default: ${OTA_API_BASE}
  ENDPOINT_ID    Endpoint id for push mode. Default: endpoint.id from config YAML.
EOF
}

yaml_value() {
  local section="$1"
  local key="$2"
  local config_path="${ROOT_DIR}/config/endpoint.yaml"
  if [[ ! -f "${config_path}" ]]; then
    config_path="${ROOT_DIR}/config/endpoint.example.yaml"
  fi
  awk -v section="${section}" -v key="${key}" '
    /^[^[:space:]#][^:]*:/ {
      current=$1
      sub(":", "", current)
    }
    current == section && $1 == key ":" {
      $1=""
      sub(/^[[:space:]]*/, "", $0)
      gsub(/^["'\'']|["'\'']$/, "", $0)
      print $0
      exit
    }
  ' "${config_path}"
}

json_value() {
  local key="$1"
  "${CONVERTER_PYTHON}" - "$key" "${RUNTIME_FIRMWARE_MANIFEST}" <<'PY'
import json
import sys

key = sys.argv[1]
path = sys.argv[2]
with open(path, "r", encoding="utf-8") as handle:
    payload = json.load(handle)
value = payload.get(key)
print("" if value is None else value)
PY
}

push_ota() {
  local endpoint_id="${ENDPOINT_ID:-$(yaml_value endpoint id)}"
  local version
  version="$(json_value version)"

  if [[ -z "${endpoint_id}" ]]; then
    echo "Missing ENDPOINT_ID and could not read endpoint.id from firmware config." >&2
    exit 1
  fi
  if [[ ! -f "${RUNTIME_FIRMWARE_BIN}" ]]; then
    echo "Missing ${RUNTIME_FIRMWARE_BIN}; build did not produce a runtime firmware binary." >&2
    exit 1
  fi

  echo "Pushing firmware OTA to ${endpoint_id} via ${OTA_API_BASE}"
  curl -fsS -X POST "${OTA_API_BASE%/}/api/firmware/ota/push" \
    -H "Content-Type: application/json" \
    -d "{\"endpoint_id\":\"${endpoint_id}\",\"filename\":\"hexe_firmware.bin\",\"version\":\"${version}\"}"
  echo
}

case "${COMMAND}" in
  build|push)
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown command: ${COMMAND}" >&2
    usage >&2
    exit 1
    ;;
esac

if [[ -z "${IDF_PATH:-}" ]]; then
  if [[ -f "${HOME}/esp-idf/export.sh" ]]; then
    # shellcheck disable=SC1090
    . "${HOME}/esp-idf/export.sh"
  else
    echo "ESP-IDF environment is not loaded and ${HOME}/esp-idf/export.sh was not found." >&2
    echo "Run '. ~/esp-idf/export.sh' first, or install ESP-IDF under ~/esp-idf." >&2
    exit 1
  fi
fi

cd "${ROOT_DIR}"

LOGO_SOURCE="${ROOT_DIR}/assets/Logo 320x240.png"
LOGO_HEADER="${ROOT_DIR}/main/assets/logo_rgb565.h"
IDLE_SOURCE="${ROOT_DIR}/assets/Idle.png"
IDLE_HEADER="${ROOT_DIR}/main/assets/idle_rgb565.h"
LISTENING_SOURCE="${ROOT_DIR}/assets/Listen.png"
LISTENING_HEADER="${ROOT_DIR}/main/assets/listening_rgb565.h"
THINKING_SOURCE="${ROOT_DIR}/assets/Thinking.png"
THINKING_HEADER="${ROOT_DIR}/main/assets/thinking_rgb565.h"
TALK_SOURCE="${ROOT_DIR}/assets/Talk.png"
TALK_HEADER="${ROOT_DIR}/main/assets/talk_rgb565.h"
WORK_SOURCE="${ROOT_DIR}/assets/Work.png"
WORK_HEADER="${ROOT_DIR}/main/assets/work_rgb565.h"
ERROR_SOURCE="${ROOT_DIR}/assets/Error.png"
ERROR_HEADER="${ROOT_DIR}/main/assets/error_rgb565.h"

if [[ -x "${HOME}/.espressif/python_env/idf6.1_py3.11_env/bin/python" ]]; then
  CONVERTER_PYTHON="${HOME}/.espressif/python_env/idf6.1_py3.11_env/bin/python"
fi

if [[ -f "${LOGO_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_image.py" "${LOGO_SOURCE}" "${LOGO_HEADER}" --format cpp-header --width 320 --height 240 --alpha-mode discard
fi

if [[ -f "${IDLE_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_image.py" "${IDLE_SOURCE}" "${IDLE_HEADER}" --format cpp-header --width 320 --height 240 --alpha-mode discard
fi

if [[ -f "${LISTENING_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_image.py" "${LISTENING_SOURCE}" "${LISTENING_HEADER}" --format cpp-header --width 320 --height 240 --alpha-mode discard
fi

if [[ -f "${THINKING_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_image.py" "${THINKING_SOURCE}" "${THINKING_HEADER}" --format cpp-header --width 320 --height 240 --alpha-mode discard
fi

if [[ -f "${TALK_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_image.py" "${TALK_SOURCE}" "${TALK_HEADER}" --format cpp-header --width 320 --height 240 --alpha-mode discard
fi

if [[ -f "${WORK_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_image.py" "${WORK_SOURCE}" "${WORK_HEADER}" --format cpp-header --width 320 --height 240 --alpha-mode discard
fi

if [[ -f "${ERROR_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_image.py" "${ERROR_SOURCE}" "${ERROR_HEADER}" --format cpp-header --width 320 --height 240 --alpha-mode discard
fi

if [[ ! -f "${ROOT_DIR}/sdkconfig" ]]; then
  idf.py set-target "${TARGET}"
fi

idf.py build

mkdir -p "${RUNTIME_FIRMWARE_DIR}"
cp "${ROOT_DIR}/build/hexe_firmware.bin" "${RUNTIME_FIRMWARE_BIN}"
sha256sum "${RUNTIME_FIRMWARE_BIN}" > "${RUNTIME_FIRMWARE_DIR}/SHA256SUMS"
echo "Copied firmware app binary to ${RUNTIME_FIRMWARE_BIN}"

if [[ "${EXPORT_AFTER_BUILD}" == "1" ]]; then
  "${ROOT_DIR}/export-artifacts.sh"
fi

if [[ "${COMMAND}" == "push" ]]; then
  push_ota
fi

echo "Firmware build complete."
