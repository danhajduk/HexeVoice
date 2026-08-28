#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/scripts/stack.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy scripts/stack.env.example first."
  exit 1
fi

. "$ENV_FILE"

case "${1:-}" in
  backend)
    eval "$BACKEND_CMD"
    ;;
  stt)
    eval "$STT_CMD"
    ;;
  speaker-id)
    eval "${SPEAKER_ID_CMD:-VOICE_SPEAKER_ID_ENABLED=true SPEAKER_ID_SOCKET_PATH=\"${SPEAKER_ID_SOCKET_PATH:-${VOICE_SPEAKER_ID_SOCKET_PATH:-runtime/sockets/speaker-id.sock}}\" PYTHONPATH=src .venv/bin/python -m hexevoice.speaker_id.service}"
    ;;
  openwakeword)
    "$ROOT_DIR/scripts/openwakeword-control.sh" ready
    ;;
  piper-tts)
    "$ROOT_DIR/scripts/piper-tts-control.sh" ready
    ;;
  frontend)
    eval "$FRONTEND_CMD"
    ;;
  *)
    echo "Usage: $0 {backend|stt|speaker-id|openwakeword|piper-tts|frontend}"
    exit 1
    ;;
esac
