#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/scripts/piper-tts.env"
COMPOSE_FILE="$ROOT_DIR/compose.piper-tts.yaml"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

export PIPER_TTS_IMAGE="${PIPER_TTS_IMAGE:-hexevoice/piper-tts:local}"
export PIPER_TTS_CONTAINER_NAME="${PIPER_TTS_CONTAINER_NAME:-hexevoice-piper-tts}"
export PIPER_TTS_PORT="${PIPER_TTS_PORT:-10200}"
export PIPER_TTS_MODEL_DIR="${PIPER_TTS_MODEL_DIR:-./runtime/piper-tts/models}"
export PIPER_TTS_MODEL_PATH="${PIPER_TTS_MODEL_PATH:-/models/en_US-lessac-medium.onnx}"
export PIPER_TTS_WARM_VOICES="${PIPER_TTS_WARM_VOICES:-}"
export PIPER_TTS_CONFIG_PATH="${PIPER_TTS_CONFIG_PATH:-}"
export PIPER_TTS_TIMEOUT_S="${PIPER_TTS_TIMEOUT_S:-30}"
export PIPER_TTS_WARM_TIMEOUT_S="${PIPER_TTS_WARM_TIMEOUT_S:-10}"
export PIPER_TTS_WARM_IDLE_S="${PIPER_TTS_WARM_IDLE_S:-0.25}"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

model_dir_abs() {
  case "$PIPER_TTS_MODEL_DIR" in
    /*) printf '%s\n' "$PIPER_TTS_MODEL_DIR" ;;
    *) printf '%s\n' "$ROOT_DIR/${PIPER_TTS_MODEL_DIR#./}" ;;
  esac
}

ACTION="${1:-status}"
case "$ACTION" in
  start)
    mkdir -p "$(model_dir_abs)"
    compose up -d --build
    ;;
  stop)
    compose stop
    ;;
  restart)
    mkdir -p "$(model_dir_abs)"
    compose up -d --build --force-recreate
    ;;
  status)
    compose ps
    ;;
  logs)
    compose logs -f --tail="${2:-100}"
    ;;
  build)
    compose build
    ;;
  config)
    compose config
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs|build|config}"
    exit 1
    ;;
esac
