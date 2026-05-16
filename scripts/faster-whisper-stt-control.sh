#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${STT_ENV_FILE:-$ROOT_DIR/scripts/stack.env}"
COMPOSE_FILE="$ROOT_DIR/compose.faster-whisper-stt.yaml"
DOCKER_BIN="${DOCKER_BIN:-docker}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

export HEXEVOICE_SOCKET_DIR="${HEXEVOICE_SOCKET_DIR:-$ROOT_DIR/runtime/sockets}"
export HEXEVOICE_STT_CACHE_DIR="${HEXEVOICE_STT_CACHE_DIR:-$ROOT_DIR/runtime/stt/faster-whisper}"
export STT_CONTAINER_NAME="${STT_CONTAINER_NAME:-hexevoice-faster-whisper-stt}"
export STT_SOCKET_PATH="${STT_SOCKET_PATH:-${VOICE_STT_SERVICE_SOCKET:-$HEXEVOICE_SOCKET_DIR/stt.sock}}"
STT_SERVICE_URL="${STT_HEALTH_URL:-${VOICE_STT_SERVICE_BASE_URL:-http://hexevoice-stt}}"
STT_HEALTH_TIMEOUT_S="${STT_HEALTH_TIMEOUT_S:-60}"
STT_HEALTH_INTERVAL_S="${STT_HEALTH_INTERVAL_S:-2}"

compose() {
  "$DOCKER_BIN" compose -f "$COMPOSE_FILE" "$@"
}

service_url() {
  printf '%s' "${STT_SERVICE_URL%/}"
}

python_with_src() {
  PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" "$@"
}

http_request() {
  local method="$1"
  local path="$2"
  local url
  url="$(service_url)$path"
  python_with_src - "$method" "$url" "$STT_SOCKET_PATH" <<'PY'
from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request

method = sys.argv[1]
url = sys.argv[2]
socket_path = sys.argv[3]
body = b"{}" if method in {"POST", "PUT"} else b""

if url.startswith("http://hexevoice-stt"):
    parsed_path = "/" + url.split("/", 3)[3] if len(url.split("/", 3)) > 3 else "/"
    request = (
        f"{method} {parsed_path} HTTP/1.1\r\n"
        "Host: hexevoice-stt\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8") + body
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(socket_path)
        client.sendall(request)
        chunks = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    finally:
        try:
            client.close()
        except Exception:
            pass
    raw = b"".join(chunks)
    header, _, payload = raw.partition(b"\r\n\r\n")
    status_line = header.splitlines()[0].decode("iso-8859-1") if header else ""
    status = int(status_line.split()[1]) if len(status_line.split()) >= 2 else 0
    if status >= 400 or status == 0:
        print(payload.decode("utf-8", errors="replace"), file=sys.stderr)
        raise SystemExit(status or 1)
    text = payload.decode("utf-8", errors="replace")
else:
    request = urllib.request.Request(url, method=method)
    if body:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data=body or None, timeout=5) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8"), file=sys.stderr)
        raise SystemExit(exc.code)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)

if text:
    try:
        print(json.dumps(json.loads(text), indent=2, sort_keys=True))
    except json.JSONDecodeError:
        print(text)
PY
}

wait_for_health() {
  local deadline now
  deadline=$((SECONDS + STT_HEALTH_TIMEOUT_S))
  while true; do
    if http_request GET /health >/dev/null 2>&1; then
      http_request GET /health
      return 0
    fi
    now="$SECONDS"
    if (( now >= deadline )); then
      echo "STT health check did not pass within ${STT_HEALTH_TIMEOUT_S}s at $(service_url)/health via $STT_SOCKET_PATH" >&2
      return 1
    fi
    sleep "$STT_HEALTH_INTERVAL_S"
  done
}

doctor() {
  local failed=0
  echo "STT container: $STT_CONTAINER_NAME"
  echo "STT URL: $(service_url)"
  echo "STT socket: $STT_SOCKET_PATH"
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
  if [[ -S "$STT_SOCKET_PATH" ]]; then
    echo "socket: ok"
  else
    echo "socket: unavailable (container may be stopped)"
  fi
  if http_request GET /health >/dev/null 2>&1; then
    echo "health: ok"
  else
    echo "health: unavailable"
  fi
  return "$failed"
}

ACTION="${1:-status}"
case "$ACTION" in
  install|build)
    mkdir -p "$HEXEVOICE_SOCKET_DIR" "$HEXEVOICE_STT_CACHE_DIR"
    compose build
    ;;
  start)
    mkdir -p "$HEXEVOICE_SOCKET_DIR" "$HEXEVOICE_STT_CACHE_DIR"
    rm -f "$STT_SOCKET_PATH"
    compose up -d --build
    ;;
  stop)
    compose stop
    ;;
  restart)
    mkdir -p "$HEXEVOICE_SOCKET_DIR" "$HEXEVOICE_STT_CACHE_DIR"
    rm -f "$STT_SOCKET_PATH"
    compose up -d --build --force-recreate
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
    http_request POST /preload
    ;;
  ready)
    mkdir -p "$HEXEVOICE_SOCKET_DIR" "$HEXEVOICE_STT_CACHE_DIR"
    rm -f "$STT_SOCKET_PATH"
    compose up -d --build
    wait_for_health
    http_request POST /preload
    ;;
  doctor)
    doctor
    ;;
  logs)
    compose logs -f --tail="${2:-100}"
    ;;
  config)
    compose config
    ;;
  *)
    echo "Usage: $0 {install|build|start|stop|restart|status|health|wait-health|preload|ready|doctor|logs|config}"
    exit 1
    ;;
esac
