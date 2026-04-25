#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/scripts/openwakeword.env"
COMPOSE_FILE="$ROOT_DIR/compose.openwakeword.yaml"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

export OPENWAKEWORD_IMAGE="${OPENWAKEWORD_IMAGE:-rhasspy/wyoming-openwakeword}"
export OPENWAKEWORD_CONTAINER_NAME="${OPENWAKEWORD_CONTAINER_NAME:-hexevoice-openwakeword}"
export OPENWAKEWORD_PORT="${OPENWAKEWORD_PORT:-10400}"
export OPENWAKEWORD_MODEL_DIR="${OPENWAKEWORD_MODEL_DIR:-./runtime/openwakeword/models}"
export OPENWAKEWORD_LEGACY_MODEL_DIR="${OPENWAKEWORD_LEGACY_MODEL_DIR:-/home/dan/Projects/HomeAssistant/openwakeword/models}"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

model_dir_abs() {
  case "$OPENWAKEWORD_MODEL_DIR" in
    /*) printf '%s\n' "$OPENWAKEWORD_MODEL_DIR" ;;
    *) printf '%s\n' "$ROOT_DIR/${OPENWAKEWORD_MODEL_DIR#./}" ;;
  esac
}

sync_models() {
  local target_dir
  target_dir="$(model_dir_abs)"
  mkdir -p "$target_dir"

  local copied=0
  if [[ -d "$OPENWAKEWORD_LEGACY_MODEL_DIR" ]]; then
    find "$OPENWAKEWORD_LEGACY_MODEL_DIR" -maxdepth 1 -type f \( -name '*.tflite' -o -name '*.onnx' \) -print0 |
      while IFS= read -r -d '' model; do
        cp -n "$model" "$target_dir/"
      done
    copied=1
  fi

  if [[ -f "$ROOT_DIR/runtime/vioce_models/Hexa.tflite" ]]; then
    cp -n "$ROOT_DIR/runtime/vioce_models/Hexa.tflite" "$target_dir/"
    copied=1
  fi

  if [[ "$copied" -eq 0 ]]; then
    echo "No legacy or local wake models found. Put .tflite/.onnx files in $target_dir."
    return 1
  fi

  find "$target_dir" -maxdepth 1 -type f \( -name '*.tflite' -o -name '*.onnx' \) -printf '%f\n' | sort
}

ACTION="${1:-status}"
case "$ACTION" in
  start)
    mkdir -p "$(model_dir_abs)"
    compose up -d
    ;;
  stop)
    compose stop
    ;;
  restart)
    mkdir -p "$(model_dir_abs)"
    compose up -d --force-recreate
    ;;
  status)
    compose ps
    ;;
  logs)
    compose logs -f --tail="${2:-100}"
    ;;
  pull)
    compose pull
    ;;
  config)
    compose config
    ;;
  sync-models)
    sync_models
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs|pull|config|sync-models}"
    exit 1
    ;;
esac
