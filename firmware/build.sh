#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${IDF_TARGET:-esp32s3}"
EXPORT_AFTER_BUILD="${EXPORT_AFTER_BUILD:-1}"
COMMAND="${1:-build}"
CONVERTER_PYTHON="python3"
RUNTIME_FIRMWARE_DIR="${RUNTIME_FIRMWARE_DIR:-${ROOT_DIR}/../runtime/firmware}"
OTA_API_BASE="${OTA_API_BASE:-http://127.0.0.1:${API_PORT:-9004}}"
COMMON_EXPORT_DIR="${COMMON_EXPORT_DIR:-${ROOT_DIR}/export}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [build|push]

Commands:
  build  Build firmware and refresh runtime/export artifacts. Builds both profiles by default.
  push   Build one firmware profile, refresh artifacts, then push OTA to the endpoint.

Environment:
  HEXE_BOARD_PROFILE  Firmware board profile: esp_box_3, ha_voice_pe, or all. Default: all for build, esp_box_3 for push.
  BUILD_DIR     ESP-IDF build directory. Defaults to build or build-ha-voice-pe by profile.
  EXPORT_DIR    Firmware export directory. Defaults to export or export-ha-voice-pe by profile.
  COMMON_EXPORT_DIR  Folder that receives profile-named binaries for all builds. Default: firmware/export.
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
  local manifest_path="$2"
  "${CONVERTER_PYTHON}" - "$key" "${manifest_path}" <<'PY'
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

profile_build_dir() {
  case "$1" in
    esp_box_3) echo "${BUILD_DIR:-${ROOT_DIR}/build}" ;;
    ha_voice_pe) echo "${BUILD_DIR:-${ROOT_DIR}/build-ha-voice-pe}" ;;
  esac
}

profile_export_dir() {
  case "$1" in
    esp_box_3) echo "${EXPORT_DIR:-${ROOT_DIR}/export}" ;;
    ha_voice_pe) echo "${EXPORT_DIR:-${ROOT_DIR}/export-ha-voice-pe}" ;;
  esac
}

profile_app_filename() {
  echo "hexe_firmware_${1}.bin"
}

profile_manifest_path() {
  echo "${RUNTIME_FIRMWARE_DIR}/manifest-${1}.json"
}

validate_profile() {
  case "$1" in
    esp_box_3|ha_voice_pe)
      ;;
    *)
      echo "Unsupported HEXE_BOARD_PROFILE: $1" >&2
      exit 1
      ;;
  esac
}

build_profile() {
  local profile="$1"
  validate_profile "${profile}"

  local build_dir
  local export_dir
  local profile_app
  build_dir="$(profile_build_dir "${profile}")"
  export_dir="$(profile_export_dir "${profile}")"
  profile_app="$(profile_app_filename "${profile}")"

  echo "Building firmware profile ${profile}"
  idf.py -B "${build_dir}" -D "HEXE_BOARD_PROFILE=${profile}" build

  if [[ "${EXPORT_AFTER_BUILD}" == "1" ]]; then
    HEXE_BOARD_PROFILE="${profile}" \
      BUILD_DIR="${build_dir}" \
      EXPORT_DIR="${export_dir}" \
      COMMON_EXPORT_DIR="${COMMON_EXPORT_DIR}" \
      UPDATE_RUNTIME_FIRMWARE=1 \
      PROFILE_APP_FILENAME="${profile_app}" \
      PROFILE_MANIFEST_FILENAME="manifest-${profile}.json" \
      "${ROOT_DIR}/export-artifacts.sh"
  fi
}

push_ota() {
  local profile="$1"
  local endpoint_id="${ENDPOINT_ID:-$(yaml_value endpoint id)}"
  local filename
  local manifest_path
  local version
  filename="$(profile_app_filename "${profile}")"
  manifest_path="$(profile_manifest_path "${profile}")"
  version="$(json_value version "${manifest_path}")"

  if [[ -z "${endpoint_id}" ]]; then
    echo "Missing ENDPOINT_ID and could not read endpoint.id from firmware config." >&2
    exit 1
  fi
  if [[ ! -f "${RUNTIME_FIRMWARE_DIR}/${filename}" ]]; then
    echo "Missing ${RUNTIME_FIRMWARE_DIR}/${filename}; build did not produce a runtime firmware binary." >&2
    exit 1
  fi

  echo "Pushing ${profile} firmware OTA to ${endpoint_id} via ${OTA_API_BASE}"
  curl -fsS -X POST "${OTA_API_BASE%/}/api/firmware/ota/push" \
    -H "Content-Type: application/json" \
    -d "{\"endpoint_id\":\"${endpoint_id}\",\"filename\":\"${filename}\",\"version\":\"${version}\"}"
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

if [[ ! -f "${ROOT_DIR}/sdkconfig" ]]; then
  idf.py set-target "${TARGET}"
fi

requested_profile="${HEXE_BOARD_PROFILE:-}"
if [[ "${COMMAND}" == "push" && -z "${requested_profile}" ]]; then
  requested_profile="esp_box_3"
elif [[ -z "${requested_profile}" ]]; then
  requested_profile="all"
fi

if [[ "${requested_profile}" == "all" && (-n "${BUILD_DIR:-}" || -n "${EXPORT_DIR:-}") ]]; then
  echo "BUILD_DIR and EXPORT_DIR overrides require a single HEXE_BOARD_PROFILE." >&2
  exit 1
fi

case "${requested_profile}" in
  all)
    if [[ "${COMMAND}" == "push" ]]; then
      echo "push mode requires a single HEXE_BOARD_PROFILE: esp_box_3 or ha_voice_pe" >&2
      exit 1
    fi
    build_profile esp_box_3
    build_profile ha_voice_pe
    ;;
  esp_box_3|ha_voice_pe)
    build_profile "${requested_profile}"
    ;;
  *)
    echo "Unsupported HEXE_BOARD_PROFILE: ${requested_profile}" >&2
    exit 1
    ;;
esac

if [[ "${COMMAND}" == "push" ]]; then
  push_ota "${requested_profile}"
fi

echo "Firmware build complete."
