#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: download-remote-export.sh [all|box|pe] [destination]

Profiles:
  all  Download both firmware/export and firmware/export-ha-voice-pe. Default.
  box  Download firmware/export.
  pe   Download firmware/export-ha-voice-pe.

Environment:
  NODE_HOST    SSH target for the HexeVoice machine. Default: $USER@hexe.local
  REMOTE_HOST  SSH hostname used when NODE_HOST is unset. Default: hexe.local
  REMOTE_USER  SSH user used when NODE_HOST is unset. Default: $USER
  REMOTE_ROOT  Remote firmware directory. Default: /home/dan/hexe/HexeVoice/firmware
  VERIFY       Verify manifest profile and SHA256SUMS. Default: 1

Examples:
  ./download-remote-export.sh all
  NODE_HOST=dan@hexe.local ./download-remote-export.sh pe /tmp/hexe-pe
EOF
}

PROFILE="${1:-all}"
DEST_ROOT="${2:-./hexe-firmware-exports}"
REMOTE_HOST="${REMOTE_HOST:-hexe.local}"
REMOTE_USER="${REMOTE_USER:-${USER:-dan}}"
NODE_HOST="${NODE_HOST:-${REMOTE_USER}@${REMOTE_HOST}}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/dan/hexe/HexeVoice/firmware}"
VERIFY="${VERIFY:-1}"

if [[ "${PROFILE}" == "-h" || "${PROFILE}" == "--help" || "${PROFILE}" == "help" ]]; then
  usage
  exit 0
fi

remote_quote() {
  printf "'%s'" "${1//\'/\'\\\'\'}"
}

download_export() {
  local label="$1"
  local remote_subdir="$2"
  local local_subdir="$3"
  local expected_profile="$4"
  local remote_dir="${REMOTE_ROOT}/${remote_subdir}"
  local destination="${DEST_ROOT}/${local_subdir}"
  local tmp_destination="${destination}.tmp"

  echo "Downloading ${label} export from ${NODE_HOST}:${remote_dir}"
  rm -rf "${tmp_destination}"
  mkdir -p "${tmp_destination}"

  ssh "${NODE_HOST}" "test -d $(remote_quote "${remote_dir}")"
  ssh "${NODE_HOST}" "tar -C $(remote_quote "${remote_dir}") --exclude='./provisioning.env' -czf - ." \
    | tar -xzf - -C "${tmp_destination}"

  if [[ "${VERIFY}" == "1" ]]; then
    if [[ ! -f "${tmp_destination}/manifest.txt" ]]; then
      echo "Missing manifest.txt in downloaded ${label} export." >&2
      exit 1
    fi
    if ! grep -qx "board_profile=${expected_profile}" "${tmp_destination}/manifest.txt"; then
      echo "Downloaded ${label} export profile mismatch; expected board_profile=${expected_profile}." >&2
      cat "${tmp_destination}/manifest.txt" >&2
      exit 1
    fi
    if [[ ! -f "${tmp_destination}/SHA256SUMS" ]]; then
      echo "Missing SHA256SUMS in downloaded ${label} export." >&2
      exit 1
    fi
    (cd "${tmp_destination}" && sha256sum -c SHA256SUMS)
  fi

  rm -rf "${destination}"
  mv "${tmp_destination}" "${destination}"
  echo "Saved ${label} export to ${destination}"
}

mkdir -p "${DEST_ROOT}"

case "${PROFILE}" in
  all)
    download_export "ESP-BOX-3" "export" "export" "esp_box_3"
    download_export "HA Voice PE" "export-ha-voice-pe" "export-ha-voice-pe" "ha_voice_pe"
    ;;
  box|esp_box_3|esp-box-3)
    download_export "ESP-BOX-3" "export" "export" "esp_box_3"
    ;;
  pe|ha_voice_pe|ha-voice-pe)
    download_export "HA Voice PE" "export-ha-voice-pe" "export-ha-voice-pe" "ha_voice_pe"
    ;;
  *)
    echo "Unknown profile: ${PROFILE}" >&2
    usage >&2
    exit 1
    ;;
esac

echo "Download complete."
