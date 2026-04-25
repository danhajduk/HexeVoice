#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${ROOT_DIR}/build"
EXPORT_DIR="${ROOT_DIR}/export"

BOOTLOADER_SRC="${BUILD_DIR}/bootloader/bootloader.bin"
PARTITION_SRC="${BUILD_DIR}/partition_table/partition-table.bin"
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
require_file "${APP_SRC}"
require_file "${PROJECT_DESC_SRC}"

mkdir -p "${EXPORT_DIR}"

cp "${BOOTLOADER_SRC}" "${EXPORT_DIR}/bootloader.bin"
cp "${PARTITION_SRC}" "${EXPORT_DIR}/partition-table.bin"
cp "${APP_SRC}" "${EXPORT_DIR}/hexe_firmware.bin"

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

sha256sum \
  "${EXPORT_DIR}/bootloader.bin" \
  "${EXPORT_DIR}/partition-table.bin" \
  "${EXPORT_DIR}/hexe_firmware.bin" > "${EXPORT_DIR}/SHA256SUMS"

cat > "${EXPORT_DIR}/manifest.txt" <<EOF
project_name=${PROJECT_NAME}
project_version=${VERSION}
target=${TARGET}
created_at_utc=${CREATED_AT}
bootloader=bootloader.bin
bootloader_offset=0x0
partition_table=partition-table.bin
partition_table_offset=0x8000
app=hexe_firmware.bin
app_offset=0x10000
EOF

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
  0x10000 hexe_firmware.bin
EOF
chmod +x "${EXPORT_DIR}/flash-esptool.sh"

cat > "${EXPORT_DIR}/README.md" <<EOF
# Firmware Export

This folder contains the files needed to flash Hexe firmware on another machine.

## Included Files

- \`bootloader.bin\`
- \`partition-table.bin\`
- \`hexe_firmware.bin\`
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
- \`0x10000\` app

## Build Info

- project: \`${PROJECT_NAME}\`
- version: \`${VERSION}\`
- target: \`${TARGET}\`
- created: \`${CREATED_AT}\`
EOF

echo "Exported firmware artifacts to ${EXPORT_DIR}"
