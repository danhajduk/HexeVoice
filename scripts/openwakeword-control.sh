#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${OPENWAKEWORD_ENV_FILE:-$ROOT_DIR/scripts/openwakeword.env}"
COMPOSE_FILE="$ROOT_DIR/compose.openwakeword.yaml"
DOCKER_BIN="${DOCKER_BIN:-docker}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

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
export OPENWAKEWORD_DEFAULT_MODEL="${OPENWAKEWORD_DEFAULT_MODEL:-Hexe}"
export OPENWAKEWORD_HEALTH_HOST="${OPENWAKEWORD_HEALTH_HOST:-${VOICE_WAKE_SERVICE_HOST:-127.0.0.1}}"
export OPENWAKEWORD_HEALTH_PORT="${OPENWAKEWORD_HEALTH_PORT:-${VOICE_WAKE_SERVICE_PORT:-$OPENWAKEWORD_PORT}}"
export OPENWAKEWORD_HEALTH_TIMEOUT_S="${OPENWAKEWORD_HEALTH_TIMEOUT_S:-60}"
export OPENWAKEWORD_HEALTH_INTERVAL_S="${OPENWAKEWORD_HEALTH_INTERVAL_S:-2}"

compose() {
  "$DOCKER_BIN" compose -f "$COMPOSE_FILE" "$@"
}

model_dir_abs() {
  case "$OPENWAKEWORD_MODEL_DIR" in
    /*) printf '%s\n' "$OPENWAKEWORD_MODEL_DIR" ;;
    *) printf '%s\n' "$ROOT_DIR/${OPENWAKEWORD_MODEL_DIR#./}" ;;
  esac
}

list_models() {
  local target_dir
  target_dir="$(model_dir_abs)"
  if [[ ! -d "$target_dir" ]]; then
    return 0
  fi
  find "$target_dir" -maxdepth 1 -type f \( -name '*.tflite' -o -name '*.onnx' \) -printf '%f\n' | sort
}

sync_models() {
  local target_dir source_default legacy_hexa copied
  target_dir="$(model_dir_abs)"
  source_default="$ROOT_DIR/runtime/openwakeword/models/hexe.tflite"
  legacy_hexa="$ROOT_DIR/runtime/vioce_models/Hexa.tflite"
  copied=0
  mkdir -p "$target_dir"

  if [[ -f "$source_default" && "$source_default" != "$target_dir/hexe.tflite" ]]; then
    cp -n "$source_default" "$target_dir/hexe.tflite"
    copied=1
  elif [[ -f "$target_dir/hexe.tflite" ]]; then
    copied=1
  fi

  if [[ -d "$OPENWAKEWORD_LEGACY_MODEL_DIR" ]]; then
    while IFS= read -r -d '' model; do
      cp -n "$model" "$target_dir/"
      copied=1
    done < <(find "$OPENWAKEWORD_LEGACY_MODEL_DIR" -maxdepth 1 -type f \( -name '*.tflite' -o -name '*.onnx' \) -print0)
  fi

  if [[ ! -f "$target_dir/hexe.tflite" && -f "$legacy_hexa" ]]; then
    cp -n "$legacy_hexa" "$target_dir/hexe.tflite"
    copied=1
  fi

  if [[ "$copied" -eq 0 ]]; then
    echo "No wake models found. Put hexe.tflite, .tflite, or .onnx files in $target_dir." >&2
    return 1
  fi

  list_models
}

health() {
  local target_dir
  target_dir="${1:-$(model_dir_abs)}"
  "$PYTHON_BIN" - "$target_dir" <<'PY'
from __future__ import annotations

from pathlib import Path
import json
import os
import socket
import sys

host = os.environ["OPENWAKEWORD_HEALTH_HOST"]
port = int(os.environ["OPENWAKEWORD_HEALTH_PORT"])
model_dir = Path(sys.argv[1])
models = sorted(path.name for pattern in ("*.tflite", "*.onnx") for path in model_dir.glob(pattern))
reachable = False
error = None
try:
    with socket.create_connection((host, port), timeout=5):
        reachable = True
except OSError as exc:
    error = str(exc)

payload = {
    "provider": "supervised_openwakeword",
    "host": host,
    "port": port,
    "reachable": reachable,
    "models": models,
}
if error:
    payload["error"] = error
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if reachable else 1)
PY
}

wait_for_health() {
  local deadline now
  deadline=$((SECONDS + OPENWAKEWORD_HEALTH_TIMEOUT_S))
  while true; do
    if health >/dev/null 2>&1; then
      health
      return 0
    fi
    now="$SECONDS"
    if (( now >= deadline )); then
      echo "openWakeWord health check did not pass within ${OPENWAKEWORD_HEALTH_TIMEOUT_S}s at ${OPENWAKEWORD_HEALTH_HOST}:${OPENWAKEWORD_HEALTH_PORT}" >&2
      return 1
    fi
    sleep "$OPENWAKEWORD_HEALTH_INTERVAL_S"
  done
}

doctor() {
  local failed=0 target_dir model_count
  target_dir="$(model_dir_abs)"
  model_count="$(list_models | wc -l | tr -d ' ')"
  echo "openWakeWord host: ${OPENWAKEWORD_HEALTH_HOST}:${OPENWAKEWORD_HEALTH_PORT}"
  echo "model dir: $target_dir"
  if "$DOCKER_BIN" --version >/dev/null 2>&1; then
    echo "docker: ok"
  else
    echo "docker: missing"
    failed=1
  fi
  if "$DOCKER_BIN" compose version >/dev/null 2>&1; then
    echo "docker compose: ok"
  else
    echo "docker compose: missing"
    failed=1
  fi
  if [[ -d "$target_dir" ]]; then
    echo "model dir: ok"
  else
    echo "model dir: missing"
    failed=1
  fi
  if [[ "$model_count" -gt 0 ]]; then
    echo "models: ok ($model_count)"
    list_models
  else
    echo "models: missing"
    failed=1
  fi
  if [[ -f "$target_dir/hexe.tflite" || -f "$target_dir/hexe.onnx" ]]; then
    echo "default Hexe model: ok"
  else
    echo "default Hexe model: missing"
    failed=1
  fi
  if health >/dev/null 2>&1; then
    echo "health: ok"
  else
    echo "health: unavailable (container may be stopped)"
  fi
  return "$failed"
}

ACTION="${1:-status}"
case "$ACTION" in
  install|sync-models|preload)
    sync_models
    ;;
  start)
    sync_models >/dev/null
    compose up -d
    ;;
  stop)
    compose stop
    ;;
  restart)
    sync_models >/dev/null
    compose up -d --force-recreate
    ;;
  status)
    compose ps
    ;;
  health)
    health "$(model_dir_abs)"
    ;;
  wait-health)
    wait_for_health
    ;;
  ready)
    sync_models
    compose up -d
    wait_for_health
    ;;
  doctor)
    doctor
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
  *)
    echo "Usage: $0 {install|sync-models|preload|start|stop|restart|status|health|wait-health|ready|doctor|logs|pull|config}"
    exit 1
    ;;
esac
