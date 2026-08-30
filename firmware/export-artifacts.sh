#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOARD_PROFILE="${HEXE_BOARD_PROFILE:-esp_box_3}"
BUILD_DIR="${BUILD_DIR:-${ROOT_DIR}/build}"
EXPORT_DIR="${EXPORT_DIR:-${ROOT_DIR}/export}"
COMMON_EXPORT_DIR="${COMMON_EXPORT_DIR:-${ROOT_DIR}/export}"
RUNTIME_FIRMWARE_DIR="${RUNTIME_FIRMWARE_DIR:-${ROOT_DIR}/../runtime/firmware}"
UPDATE_RUNTIME_FIRMWARE="${UPDATE_RUNTIME_FIRMWARE:-1}"
PROFILE_APP_FILENAME="${PROFILE_APP_FILENAME:-hexe_firmware_${BOARD_PROFILE}.bin}"
PROFILE_MANIFEST_FILENAME="${PROFILE_MANIFEST_FILENAME:-manifest-${BOARD_PROFILE}.json}"
FIRMWARE_APPLICATION_TYPE="${FIRMWARE_APPLICATION_TYPE:-endpoint}"
FIRMWARE_API_VERSION="${FIRMWARE_API_VERSION:-hexe-firmware-main-api-v1}"
MODEL_API_VERSION="${MODEL_API_VERSION:-hexe-model-bundle-api-v1}"
ASSET_API_VERSION="${ASSET_API_VERSION:-hexe-asset-bundle-api-v1}"
CALIBRATION_SCHEMA_VERSION="${CALIBRATION_SCHEMA_VERSION:-hexe-calibration-schema-v1}"
GENERATED_COMPONENT_NAME="${GENERATED_COMPONENT_NAME:-endpoint_runtime}"

BOOTLOADER_SRC="${BUILD_DIR}/bootloader/bootloader.bin"
PARTITION_SRC="${BUILD_DIR}/partition_table/partition-table.bin"
OTA_DATA_SRC="${BUILD_DIR}/ota_data_initial.bin"
APP_SRC="${BUILD_DIR}/hexe_firmware.bin"
ELF_SRC="${BUILD_DIR}/hexe_firmware.elf"
FLASH_ARGS_SRC="${BUILD_DIR}/flasher_args.json"
PROJECT_DESC_SRC="${BUILD_DIR}/project_description.json"
PROVISIONING_CSV_TOOL_SRC="${ROOT_DIR}/tools/provisioning-env-to-nvs-csv.py"
GENERATED_DIR="${BUILD_DIR}/esp-idf/${GENERATED_COMPONENT_NAME}/generated"
LEGACY_GENERATED_DIR="${BUILD_DIR}/generated"
GENERATED_BOARD_CONFIG_SRC="${GENERATED_DIR}/board_profile_config.cmake"
GENERATED_CONFIG_SRC="${GENERATED_DIR}/endpoint_config.h"
if [[ ! -f "${GENERATED_BOARD_CONFIG_SRC}" ]]; then
  GENERATED_BOARD_CONFIG_SRC="${LEGACY_GENERATED_DIR}/board_profile_config.cmake"
fi
if [[ ! -f "${GENERATED_CONFIG_SRC}" ]]; then
  GENERATED_CONFIG_SRC="${LEGACY_GENERATED_DIR}/endpoint_config.h"
fi

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo "Missing required file: ${path}" >&2
    echo "Run 'idf.py build' in ${ROOT_DIR} first." >&2
    exit 1
  fi
}

require_file "${BOOTLOADER_SRC}"
require_file "${PARTITION_SRC}"
require_file "${OTA_DATA_SRC}"
require_file "${APP_SRC}"
require_file "${PROJECT_DESC_SRC}"
require_file "${PROVISIONING_CSV_TOOL_SRC}"
require_file "${GENERATED_BOARD_CONFIG_SRC}"

mkdir -p "${EXPORT_DIR}"
if [[ "${UPDATE_RUNTIME_FIRMWARE}" == "1" ]]; then
  mkdir -p "${RUNTIME_FIRMWARE_DIR}"
fi

cp "${BOOTLOADER_SRC}" "${EXPORT_DIR}/bootloader.bin"
cp "${PARTITION_SRC}" "${EXPORT_DIR}/partition-table.bin"
cp "${OTA_DATA_SRC}" "${EXPORT_DIR}/ota_data_initial.bin"
cp "${APP_SRC}" "${EXPORT_DIR}/hexe_firmware.bin"
cp "${APP_SRC}" "${EXPORT_DIR}/${PROFILE_APP_FILENAME}"
cp "${PROVISIONING_CSV_TOOL_SRC}" "${EXPORT_DIR}/provisioning-env-to-nvs-csv.py"
mkdir -p "${COMMON_EXPORT_DIR}"
cp "${APP_SRC}" "${COMMON_EXPORT_DIR}/${PROFILE_APP_FILENAME}"
if [[ "${UPDATE_RUNTIME_FIRMWARE}" == "1" ]]; then
  cp "${APP_SRC}" "${RUNTIME_FIRMWARE_DIR}/${PROFILE_APP_FILENAME}"
  if [[ "${BOARD_PROFILE}" == "esp_box_3" ]]; then
    cp "${APP_SRC}" "${RUNTIME_FIRMWARE_DIR}/hexe_firmware.bin"
  fi
fi

if [[ -f "${ELF_SRC}" ]]; then
  cp "${ELF_SRC}" "${EXPORT_DIR}/hexe_firmware.elf"
fi

if [[ -f "${FLASH_ARGS_SRC}" ]]; then
  cp "${FLASH_ARGS_SRC}" "${EXPORT_DIR}/flasher_args.json"
fi

VERSION="$(awk -F'"' '/"project_version"/ {print $4; exit}' "${PROJECT_DESC_SRC}")"
TARGET="$(awk -F'"' '/"target"/ {print $4; exit}' "${PROJECT_DESC_SRC}")"
PROJECT_NAME="$(awk -F'"' '/"project_name"/ {print $4; exit}' "${PROJECT_DESC_SRC}")"
CREATED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

if [[ "${VERSION}" == *dirty* ]]; then
  echo "Refusing to export dirty firmware version: ${VERSION}" >&2
  echo "Commit or stash changes before building OTA/runtime firmware artifacts." >&2
  exit 1
fi

config_string() {
  local name="$1"
  awk -F'"' -v name="${name}" '$0 ~ ("constexpr const char \\*" name " =") {print $2; exit}' "${GENERATED_CONFIG_SRC}" 2>/dev/null || true
}

config_int() {
  local name="$1"
  awk -v name="${name}" '$0 ~ ("constexpr int " name " =") {value=$NF; gsub(/;/, "", value); print value; exit}' "${GENERATED_CONFIG_SRC}" 2>/dev/null || true
}

config_bool() {
  local name="$1"
  awk -v name="${name}" '$0 ~ ("constexpr bool " name " =") {value=$NF; gsub(/;/, "", value); print value; exit}' "${GENERATED_CONFIG_SRC}" 2>/dev/null || true
}

cmake_config_string() {
  local name="$1"
  awk -F'"' -v name="${name}" '$0 ~ ("set\\(" name " ") {print $2; exit}' "${GENERATED_BOARD_CONFIG_SRC}" 2>/dev/null || true
}

DEFAULT_ENDPOINT_ID="$(config_string kEndpointId)"
DEFAULT_BACKEND_HOST="$(config_string kEndpointBackendHost)"
DEFAULT_HTTP_PORT="$(config_int kEndpointHttpPort)"
DEFAULT_WS_PORT="$(config_int kEndpointWsPort)"
DEFAULT_USE_TLS="$(config_bool kEndpointUseTls)"
OTA_MANIFEST_KEY_ID="$(config_string kEndpointOtaManifestKeyId)"

BOARD_IDF_TARGET="$(cmake_config_string HEXE_BOARD_IDF_TARGET)"
BOARD_SOC="$(cmake_config_string HEXE_BOARD_SOC)"
BOARD_PARTITION_SCHEMA="$(cmake_config_string HEXE_BOARD_PARTITION_SCHEMA)"
BOARD_APP_SLOT_SIZE="$(cmake_config_string HEXE_BOARD_APP_SLOT_SIZE)"
BOARD_FLASH_SIZE="$(cmake_config_string HEXE_BOARD_FLASH_SIZE)"
BOARD_PSRAM_SIZE="$(cmake_config_string HEXE_BOARD_PSRAM_SIZE)"
BOARD_SUPPORT_STATUS="$(cmake_config_string HEXE_BOARD_SUPPORT_STATUS)"

DEFAULT_ENDPOINT_ID="${DEFAULT_ENDPOINT_ID:-${BOARD_PROFILE}}"
DEFAULT_DISPLAY_NAME="${DEFAULT_ENDPOINT_ID}"
DEFAULT_BACKEND_HOST="${DEFAULT_BACKEND_HOST:-10.0.0.100}"
DEFAULT_HTTP_PORT="${DEFAULT_HTTP_PORT:-9004}"
DEFAULT_WS_PORT="${DEFAULT_WS_PORT:-9004}"
DEFAULT_USE_TLS="${DEFAULT_USE_TLS:-false}"
OTA_MANIFEST_KEY_ID="${OTA_MANIFEST_KEY_ID:-hexevoice-dev-v1}"
BOARD_IDF_TARGET="${BOARD_IDF_TARGET:-${TARGET}}"
BOARD_SOC="${BOARD_SOC:-${BOARD_IDF_TARGET}}"
BOARD_PARTITION_SCHEMA="${BOARD_PARTITION_SCHEMA:-unknown}"
BOARD_APP_SLOT_SIZE="${BOARD_APP_SLOT_SIZE:-unknown}"
BOARD_FLASH_SIZE="${BOARD_FLASH_SIZE:-unknown}"
BOARD_PSRAM_SIZE="${BOARD_PSRAM_SIZE:-unknown}"
BOARD_SUPPORT_STATUS="${BOARD_SUPPORT_STATUS:-unknown}"
APP_SIZE_BYTES="$(stat -c '%s' "${APP_SRC}")"
APP_SHA256="$(sha256sum "${APP_SRC}" | awk '{print $1}')"

(
  cd "${EXPORT_DIR}"
  sha256sum \
    bootloader.bin \
    partition-table.bin \
    ota_data_initial.bin \
    hexe_firmware.bin \
    "${PROFILE_APP_FILENAME}" > SHA256SUMS
)

(
  cd "${COMMON_EXPORT_DIR}"
  sha256sum hexe_firmware_*.bin > SHA256SUMS.profiles
)

if [[ "${UPDATE_RUNTIME_FIRMWARE}" == "1" ]]; then
  (
    cd "${RUNTIME_FIRMWARE_DIR}"
    sha256sum hexe_firmware*.bin > SHA256SUMS
  )
fi

cat > "${EXPORT_DIR}/manifest.txt" <<EOF
project_name=${PROJECT_NAME}
project_version=${VERSION}
target=${TARGET}
board_profile=${BOARD_PROFILE}
created_at_utc=${CREATED_AT}
bootloader=bootloader.bin
bootloader_offset=0x0
partition_table=partition-table.bin
partition_table_offset=0x8000
ota_data=ota_data_initial.bin
ota_data_offset=0xd000
app=hexe_firmware.bin
app_offset=0x10000
profile_app=${PROFILE_APP_FILENAME}
application_type=${FIRMWARE_APPLICATION_TYPE}
idf_target=${BOARD_IDF_TARGET}
soc=${BOARD_SOC}
flash_size=${BOARD_FLASH_SIZE}
psram_size=${BOARD_PSRAM_SIZE}
partition_schema=${BOARD_PARTITION_SCHEMA}
app_slot_size=${BOARD_APP_SLOT_SIZE}
firmware_api_version=${FIRMWARE_API_VERSION}
model_api_version=${MODEL_API_VERSION}
asset_api_version=${ASSET_API_VERSION}
calibration_schema_version=${CALIBRATION_SCHEMA_VERSION}
image_size_bytes=${APP_SIZE_BYTES}
sha256=${APP_SHA256}
signature_algorithm=hmac-sha256
signature_key_id=${OTA_MANIFEST_KEY_ID}
EOF

cat > "${EXPORT_DIR}/provisioning.env.example" <<EOF
# Copy this file to provisioning.env before flashing, then edit the values.
# The flash script converts provisioning.env to an ESP-IDF NVS image and
# writes it to the firmware NVS partition at 0x9000.
#
# provisioning.env may contain Wi-Fi credentials. Do not commit or share it.

ENDPOINT_ID=${DEFAULT_ENDPOINT_ID}
DISPLAY_NAME=${DEFAULT_DISPLAY_NAME}
BACKEND_HOST=${DEFAULT_BACKEND_HOST}
HTTP_PORT=${DEFAULT_HTTP_PORT}
WS_PORT=${DEFAULT_WS_PORT}
USE_TLS=${DEFAULT_USE_TLS}
WIFI_SSID=
WIFI_PASSWORD=
EOF

if [[ "${UPDATE_RUNTIME_FIRMWARE}" == "1" ]]; then
  cat > "${RUNTIME_FIRMWARE_DIR}/${PROFILE_MANIFEST_FILENAME}" <<EOF
{
  "project_name": "${PROJECT_NAME}",
  "version": "${VERSION}",
  "target": "${TARGET}",
  "application_type": "${FIRMWARE_APPLICATION_TYPE}",
  "firmware_api_version": "${FIRMWARE_API_VERSION}",
  "model_api_version": "${MODEL_API_VERSION}",
  "asset_api_version": "${ASSET_API_VERSION}",
  "calibration_schema_version": "${CALIBRATION_SCHEMA_VERSION}",
  "board_profile": "${BOARD_PROFILE}",
  "idf_target": "${BOARD_IDF_TARGET}",
  "soc": "${BOARD_SOC}",
  "flash_size": "${BOARD_FLASH_SIZE}",
  "psram_size": "${BOARD_PSRAM_SIZE}",
  "partition_schema": "${BOARD_PARTITION_SCHEMA}",
  "app_slot_size": "${BOARD_APP_SLOT_SIZE}",
  "support_status": "${BOARD_SUPPORT_STATUS}",
  "created_at_utc": "${CREATED_AT}",
  "filename": "${PROFILE_APP_FILENAME}",
  "image_size_bytes": ${APP_SIZE_BYTES},
  "size_bytes": ${APP_SIZE_BYTES},
  "sha256": "${APP_SHA256}",
  "signature_algorithm": "hmac-sha256",
  "signature_key_id": "${OTA_MANIFEST_KEY_ID}",
  "signature_scope": "ota_payload_signed_by_backend_at_delivery",
  "signature": null
}
EOF
  if [[ "${BOARD_PROFILE}" == "esp_box_3" ]]; then
    cp "${RUNTIME_FIRMWARE_DIR}/${PROFILE_MANIFEST_FILENAME}" "${RUNTIME_FIRMWARE_DIR}/manifest.json"
  fi
fi

cat > "${EXPORT_DIR}/flash-esptool.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

PORT="${1:-/dev/ttyACM0}"
BAUD="${BAUD:-460800}"
PROVISIONING_ENV="${PROVISIONING_ENV:-provisioning.env}"
PROVISIONING_WORKDIR=""

if [[ -z "${IDF_PATH:-}" ]]; then
  echo "IDF_PATH is not set. Run '. ~/esp-idf/export.sh' first." >&2
  exit 1
fi

cleanup() {
  if [[ -n "${PROVISIONING_WORKDIR}" && -d "${PROVISIONING_WORKDIR}" ]]; then
    rm -rf "${PROVISIONING_WORKDIR}"
  fi
}
trap cleanup EXIT

for artifact in bootloader.bin partition-table.bin ota_data_initial.bin hexe_firmware.bin; do
  if [[ ! -f "${artifact}" ]]; then
    echo "Missing required flash artifact: ${SCRIPT_DIR}/${artifact}" >&2
    exit 1
  fi
done

FLASH_ARGS=(
  0x0 bootloader.bin
  0x8000 partition-table.bin
)

if [[ -f "${PROVISIONING_ENV}" ]]; then
  echo "Building provisioning NVS image from ${PROVISIONING_ENV}"
  PROVISIONING_WORKDIR="$(mktemp -d)"
  PROVISIONING_CSV="${PROVISIONING_WORKDIR}/provisioning.csv"
  PROVISIONING_BIN="${PROVISIONING_WORKDIR}/provisioning.bin"

  python provisioning-env-to-nvs-csv.py "${PROVISIONING_ENV}" "${PROVISIONING_CSV}"

  python "${IDF_PATH}/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py" \
    generate "${PROVISIONING_CSV}" "${PROVISIONING_BIN}" 0x4000
  FLASH_ARGS+=(0x9000 "${PROVISIONING_BIN}")
else
  echo "No ${PROVISIONING_ENV} found; flashing firmware without provisioning NVS image."
fi

FLASH_ARGS+=(
  0xd000 ota_data_initial.bin
  0x10000 hexe_firmware.bin
)

python "${IDF_PATH}/components/esptool_py/esptool/esptool.py" \
  --chip esp32s3 \
  -p "${PORT}" \
  -b "${BAUD}" \
  write_flash -z \
  "${FLASH_ARGS[@]}"
EOF
chmod +x "${EXPORT_DIR}/flash-esptool.sh"

cat > "${EXPORT_DIR}/README.md" <<EOF
# Firmware Export

This folder contains the files needed to flash Hexe firmware on another machine.

## Included Files

- \`bootloader.bin\`
- \`partition-table.bin\`
- \`ota_data_initial.bin\`
- \`hexe_firmware.bin\`
- \`${PROFILE_APP_FILENAME}\`
- \`SHA256SUMS\`
- \`manifest.txt\`
- \`flash-esptool.sh\`
- \`provisioning.env.example\`
- \`provisioning-env-to-nvs-csv.py\`

## Flash With ESP-IDF Environment Loaded

\`\`\`bash
. ~/esp-idf/export.sh
cd firmware/export
./flash-esptool.sh /dev/ttyACM0
\`\`\`

## Optional Provisioning Text File

Copy \`provisioning.env.example\` to \`provisioning.env\`, edit the values, and
then run \`flash-esptool.sh\`. The flash script converts \`provisioning.env\`
to an ESP-IDF NVS image and writes it to \`0x9000\`, so the firmware reads the
endpoint id, display name, backend host/ports, TLS flag, and Wi-Fi credentials
on boot.

\`provisioning.env\` can contain Wi-Fi credentials. Keep it local and do not
commit or share it.

## Flash Offsets

- \`0x0\` bootloader
- \`0x8000\` partition table
- \`0x9000\` optional provisioning NVS
- \`0xd000\` OTA data
- \`0x10000\` app

## Build Info

- project: \`${PROJECT_NAME}\`
- version: \`${VERSION}\`
- target: \`${TARGET}\`
- board profile: \`${BOARD_PROFILE}\`
- created: \`${CREATED_AT}\`
EOF

echo "Exported firmware artifacts to ${EXPORT_DIR}"
