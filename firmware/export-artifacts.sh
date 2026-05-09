#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOARD_PROFILE="${HEXE_BOARD_PROFILE:-esp_box_3}"
BUILD_DIR="${BUILD_DIR:-${ROOT_DIR}/build}"
EXPORT_DIR="${EXPORT_DIR:-${ROOT_DIR}/export}"
COMMON_EXPORT_DIR="${COMMON_EXPORT_DIR:-${ROOT_DIR}/export}"
RUNTIME_FIRMWARE_DIR="${ROOT_DIR}/../runtime/firmware"
UPDATE_RUNTIME_FIRMWARE="${UPDATE_RUNTIME_FIRMWARE:-1}"
PROFILE_APP_FILENAME="${PROFILE_APP_FILENAME:-hexe_firmware_${BOARD_PROFILE}.bin}"
PROFILE_MANIFEST_FILENAME="${PROFILE_MANIFEST_FILENAME:-manifest-${BOARD_PROFILE}.json}"

BOOTLOADER_SRC="${BUILD_DIR}/bootloader/bootloader.bin"
PARTITION_SRC="${BUILD_DIR}/partition_table/partition-table.bin"
OTA_DATA_SRC="${BUILD_DIR}/ota_data_initial.bin"
APP_SRC="${BUILD_DIR}/hexe_firmware.bin"
ELF_SRC="${BUILD_DIR}/hexe_firmware.elf"
FLASH_ARGS_SRC="${BUILD_DIR}/flasher_args.json"
PROJECT_DESC_SRC="${BUILD_DIR}/project_description.json"

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

mkdir -p "${EXPORT_DIR}"
if [[ "${UPDATE_RUNTIME_FIRMWARE}" == "1" ]]; then
  mkdir -p "${RUNTIME_FIRMWARE_DIR}"
fi

cp "${BOOTLOADER_SRC}" "${EXPORT_DIR}/bootloader.bin"
cp "${PARTITION_SRC}" "${EXPORT_DIR}/partition-table.bin"
cp "${OTA_DATA_SRC}" "${EXPORT_DIR}/ota_data_initial.bin"
cp "${APP_SRC}" "${EXPORT_DIR}/hexe_firmware.bin"
cp "${APP_SRC}" "${EXPORT_DIR}/${PROFILE_APP_FILENAME}"
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
EOF

if [[ "${UPDATE_RUNTIME_FIRMWARE}" == "1" ]]; then
  cat > "${RUNTIME_FIRMWARE_DIR}/${PROFILE_MANIFEST_FILENAME}" <<EOF
{
  "project_name": "${PROJECT_NAME}",
  "version": "${VERSION}",
  "target": "${TARGET}",
  "board_profile": "${BOARD_PROFILE}",
  "created_at_utc": "${CREATED_AT}",
  "filename": "${PROFILE_APP_FILENAME}",
  "sha256": "$(sha256sum "${RUNTIME_FIRMWARE_DIR}/${PROFILE_APP_FILENAME}" | awk '{print $1}')"
}
EOF
  if [[ "${BOARD_PROFILE}" == "esp_box_3" ]]; then
    cp "${RUNTIME_FIRMWARE_DIR}/${PROFILE_MANIFEST_FILENAME}" "${RUNTIME_FIRMWARE_DIR}/manifest.json"
  fi
fi

cat > "${EXPORT_DIR}/flash-esptool.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-/dev/ttyACM0}"
BAUD="${BAUD:-460800}"

if [[ -z "${IDF_PATH:-}" ]]; then
  echo "IDF_PATH is not set. Run '. ~/esp-idf/export.sh' first." >&2
  exit 1
fi

python "${IDF_PATH}/components/esptool_py/esptool/esptool.py" \
  --chip esp32s3 \
  -p "${PORT}" \
  -b "${BAUD}" \
  write_flash -z \
  0x0 bootloader.bin \
  0x8000 partition-table.bin \
  0xd000 ota_data_initial.bin \
  0x10000 hexe_firmware.bin
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

## Flash With ESP-IDF Environment Loaded

\`\`\`bash
. ~/esp-idf/export.sh
cd firmware/export
./flash-esptool.sh /dev/ttyACM0
\`\`\`

## Flash Offsets

- \`0x0\` bootloader
- \`0x8000\` partition table
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
