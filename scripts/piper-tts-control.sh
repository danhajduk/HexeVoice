#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PIPER_TTS_ENV_FILE:-$ROOT_DIR/scripts/piper-tts.env}"
COMPOSE_FILE="$ROOT_DIR/compose.piper-tts.yaml"
DOCKER_BIN="${DOCKER_BIN:-docker}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

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
export PIPER_TTS_VOICE_REPO_URL="${PIPER_TTS_VOICE_REPO_URL:-https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0}"
export PIPER_TTS_DOWNLOAD_VOICES="${PIPER_TTS_DOWNLOAD_VOICES:-}"
PIPER_TTS_SERVICE_URL="${PIPER_TTS_HEALTH_URL:-${VOICE_TTS_PIPER_BASE_URL:-http://${VOICE_TTS_PIPER_SERVICE_HOST:-127.0.0.1}:${VOICE_TTS_PIPER_SERVICE_PORT:-$PIPER_TTS_PORT}}}"
PIPER_TTS_HEALTH_TIMEOUT_S="${PIPER_TTS_HEALTH_TIMEOUT_S:-60}"
PIPER_TTS_HEALTH_INTERVAL_S="${PIPER_TTS_HEALTH_INTERVAL_S:-2}"

compose() {
  "$DOCKER_BIN" compose -f "$COMPOSE_FILE" "$@"
}

model_dir_abs() {
  case "$PIPER_TTS_MODEL_DIR" in
    /*) printf '%s\n' "$PIPER_TTS_MODEL_DIR" ;;
    *) printf '%s\n' "$ROOT_DIR/${PIPER_TTS_MODEL_DIR#./}" ;;
  esac
}

service_url() {
  printf '%s' "${PIPER_TTS_SERVICE_URL%/}"
}

http_request() {
  local method="$1"
  local path="$2"
  local url
  url="$(service_url)$path"
  "$PYTHON_BIN" - "$method" "$url" <<'PY'
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

method = sys.argv[1]
url = sys.argv[2]
request = urllib.request.Request(url, method=method)
if method in {"POST", "PUT"}:
    request.add_header("Content-Type", "application/json")
    data = b"{}"
else:
    data = None
try:
    with urllib.request.urlopen(request, data=data, timeout=5) as response:
        body = response.read().decode("utf-8")
except urllib.error.HTTPError as exc:
    print(exc.read().decode("utf-8"), file=sys.stderr)
    raise SystemExit(exc.code)
except Exception as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
if body:
    try:
        print(json.dumps(json.loads(body), indent=2, sort_keys=True))
    except json.JSONDecodeError:
        print(body)
PY
}

wait_for_health() {
  local deadline now
  deadline=$((SECONDS + PIPER_TTS_HEALTH_TIMEOUT_S))
  while true; do
    if http_request GET /health >/dev/null 2>&1; then
      http_request GET /health
      return 0
    fi
    now="$SECONDS"
    if (( now >= deadline )); then
      echo "Piper TTS health check did not pass within ${PIPER_TTS_HEALTH_TIMEOUT_S}s at $(service_url)/health" >&2
      return 1
    fi
    sleep "$PIPER_TTS_HEALTH_INTERVAL_S"
  done
}

download_models() {
  local target_dir
  target_dir="$(model_dir_abs)"
  mkdir -p "$target_dir"
  "$PYTHON_BIN" - "$target_dir" <<'PY'
from __future__ import annotations

from pathlib import Path
import os
import sys
import urllib.request

target_dir = Path(sys.argv[1])
repo_url = os.environ["PIPER_TTS_VOICE_REPO_URL"].rstrip("/")
requested = [item.strip() for item in os.environ.get("PIPER_TTS_DOWNLOAD_VOICES", "").split(",") if item.strip()]
model_path = Path(os.environ.get("PIPER_TTS_MODEL_PATH", "/models/en_US-lessac-medium.onnx"))
default_voice = model_path.name.removesuffix(".onnx")
if default_voice and default_voice not in requested:
    requested.insert(0, default_voice)
for voice in [item.strip() for item in os.environ.get("PIPER_TTS_WARM_VOICES", "").split(",") if item.strip()]:
    if voice not in requested:
        requested.append(voice)
if not requested:
    requested = ["en_US-lessac-medium"]


def source_path(voice: str, suffix: str) -> str:
    parts = voice.split("-")
    if len(parts) < 3 or "_" not in parts[0]:
        raise SystemExit(f"unsupported_piper_voice_id:{voice}")
    locale = parts[0]
    quality = parts[-1]
    speaker = "-".join(parts[1:-1])
    language = locale.split("_", 1)[0]
    return f"{language}/{locale}/{speaker}/{quality}/{voice}.onnx{suffix}"


def download(url: str, destination: Path) -> None:
    if destination.exists() and destination.stat().st_size > 0:
        print(f"exists {destination.name}")
        return
    tmp_path = destination.with_suffix(destination.suffix + ".tmp")
    print(f"download {destination.name}")
    with urllib.request.urlopen(url, timeout=60) as response:
        tmp_path.write_bytes(response.read())
    if tmp_path.stat().st_size <= 0:
        tmp_path.unlink(missing_ok=True)
        raise SystemExit(f"empty_download:{destination.name}")
    tmp_path.replace(destination)


for voice in requested:
    for suffix in ("", ".json"):
        rel = source_path(voice, suffix)
        download(f"{repo_url}/{rel}", target_dir / f"{voice}.onnx{suffix}")
PY
}

doctor() {
  local failed=0
  echo "Piper TTS URL: $(service_url)"
  echo "model dir: $(model_dir_abs)"
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
  if [[ -d "$(model_dir_abs)" ]]; then
    echo "model dir: ok"
  else
    echo "model dir: missing"
    failed=1
  fi
  if [[ -f "$(model_dir_abs)/$(basename "${PIPER_TTS_MODEL_PATH%.onnx}").onnx" ]]; then
    echo "default model: ok"
  else
    echo "default model: missing ($(basename "$PIPER_TTS_MODEL_PATH"))"
  fi
  if http_request GET /health >/dev/null 2>&1; then
    echo "health: ok"
  else
    echo "health: unavailable (container may be stopped)"
  fi
  return "$failed"
}

ACTION="${1:-status}"
case "$ACTION" in
  install|download-models)
    download_models
    ;;
  start)
    mkdir -p "$(model_dir_abs)"
    compose up -d --build
    ;;
  stop)
    compose stop
    ;;
  restart)
    mkdir -p "$(model_dir_abs)"
    compose up -d --force-recreate piper_tts
    ;;
  status)
    compose ps
    ;;
  health)
    http_request GET /health
    ;;
  wait-health)
    wait_for_health
    ;;
  preload)
    http_request PUT /config
    ;;
  ready)
    download_models
    mkdir -p "$(model_dir_abs)"
    compose up -d --build
    wait_for_health
    http_request PUT /config
    ;;
  doctor)
    doctor
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
    echo "Usage: $0 {install|download-models|start|stop|restart|status|health|wait-health|preload|ready|doctor|logs|build|config}"
    exit 1
    ;;
esac
