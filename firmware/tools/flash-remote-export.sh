#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: flash-remote-export.sh [box|pe] [port]
       flash-remote-export.sh [port]

Profiles:
  box  Pull firmware/export from the HexeVoice machine.
  pe   Pull firmware/export-ha-voice-pe from the HexeVoice machine.

Environment:
  NODE_HOST    SSH host for the HexeVoice machine. Default: dan@10.0.0.100
  REMOTE_ROOT  Remote firmware directory. Default: /home/dan/Projects/HexeVoice/firmware
  IDF_EXPORT   ESP-IDF export script. Default: $HOME/esp-idf/export.sh
  MONITOR      Start serial monitor after flashing. Default: 1
  BAUD         Flash baud passed through to flash-esptool.sh. Default: 460800
EOF
}

NODE_HOST="${NODE_HOST:-dan@10.0.0.100}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/dan/Projects/HexeVoice/firmware}"
IDF_EXPORT="${IDF_EXPORT:-$HOME/esp-idf/export.sh}"
MONITOR="${MONITOR:-1}"

PROFILE="${1:-pe}"
PORT="${2:-/dev/ttyACM0}"

if [[ "${PROFILE}" == "-h" || "${PROFILE}" == "--help" || "${PROFILE}" == "help" ]]; then
  usage
  exit 0
fi

if [[ "${PROFILE}" == /dev/* ]]; then
  PORT="${PROFILE}"
  PROFILE="${FLASH_PROFILE:-pe}"
fi

case "${PROFILE}" in
  box|esp_box_3|esp-box-3)
    PROFILE="box"
    EXPECTED_BOARD_PROFILE="esp_box_3"
    REMOTE_EXPORT="${REMOTE_EXPORT:-${REMOTE_ROOT}/export}"
    ;;
  pe|ha_voice_pe|ha-voice-pe)
    PROFILE="pe"
    EXPECTED_BOARD_PROFILE="ha_voice_pe"
    REMOTE_EXPORT="${REMOTE_EXPORT:-${REMOTE_ROOT}/export-ha-voice-pe}"
    ;;
  *)
    echo "Unknown profile: ${PROFILE}" >&2
    usage >&2
    exit 1
    ;;
esac

rm -rf ./export

echo "Pulling ${PROFILE} firmware export from ${NODE_HOST}:${REMOTE_EXPORT}"
scp -r "${NODE_HOST}:${REMOTE_EXPORT}" ./export
cd ./export

if [[ ! -f manifest.txt ]]; then
  echo "Missing manifest.txt in firmware export." >&2
  exit 1
fi

if ! grep -qx "board_profile=${EXPECTED_BOARD_PROFILE}" manifest.txt; then
  echo "Firmware export profile mismatch; expected board_profile=${EXPECTED_BOARD_PROFILE}." >&2
  echo "manifest.txt contains:" >&2
  cat manifest.txt >&2
  exit 1
fi

sha256sum -c SHA256SUMS

. "${IDF_EXPORT}"

chmod +x ./flash-esptool.sh
bash ./flash-esptool.sh "${PORT}"

if [[ "${MONITOR}" == "1" ]]; then
  python -m serial.tools.miniterm "${PORT}" 115200
fi
