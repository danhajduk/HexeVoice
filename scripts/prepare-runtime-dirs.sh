#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${HEXEVOICE_RUNTIME_DIR:-${RUNTIME_DIR:-$ROOT_DIR/runtime}}"

runtime_path() {
  local path="$1"
  case "$RUNTIME_DIR" in
    /*) printf '%s/%s\n' "${RUNTIME_DIR%/}" "$path" ;;
    *) printf '%s/%s/%s\n' "$ROOT_DIR" "${RUNTIME_DIR%/}" "$path" ;;
  esac
}

DIRS=(
  "endpoint_media"
  "endpoint_media/ota"
  "endpoint_media/ui_manifest"
  "firmware"
  "logs"
  "migration"
  "migration/backups"
  "micro_vad_chunks"
  "openwakeword"
  "openwakeword/models"
  "piper-tts"
  "piper-tts/models"
  "rendered_node_ui_pages"
  "sockets"
  "stt"
  "stt/faster-whisper"
  "voice_tts"
  "wake_recordings"
)

for dir in "${DIRS[@]}"; do
  mkdir -p "$(runtime_path "$dir")"
done

printf 'Prepared HexeVoice runtime directories under %s\n' "$RUNTIME_DIR"
