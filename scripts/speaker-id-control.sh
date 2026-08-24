#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SPEAKER_ID_ENV_FILE:-$ROOT_DIR/scripts/stack.env}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

export HEXEVOICE_SOCKET_DIR="${HEXEVOICE_SOCKET_DIR:-$ROOT_DIR/runtime/sockets}"
export SPEAKER_ID_SOCKET_PATH="${SPEAKER_ID_SOCKET_PATH:-${VOICE_SPEAKER_ID_SOCKET_PATH:-$HEXEVOICE_SOCKET_DIR/speaker-id.sock}}"
SPEAKER_ID_SERVICE_URL="${SPEAKER_ID_HEALTH_URL:-${VOICE_SPEAKER_ID_BASE_URL:-http://hexevoice-speaker-id}}"
SPEAKER_ID_HEALTH_TIMEOUT_S="${SPEAKER_ID_HEALTH_TIMEOUT_S:-30}"
SPEAKER_ID_HEALTH_INTERVAL_S="${SPEAKER_ID_HEALTH_INTERVAL_S:-1}"
SPEAKER_ID_SERVICE_NAME="${VOICE_SPEAKER_ID_SERVICE_NAME:-hexevoice-speaker-id.service}"

python_with_src() {
  PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" "$@"
}

service_url() {
  printf '%s' "${SPEAKER_ID_SERVICE_URL%/}"
}

prepare_runtime_dirs() {
  mkdir -p "$HEXEVOICE_SOCKET_DIR" "$ROOT_DIR/runtime/speaker_id"
  chmod 700 "$HEXEVOICE_SOCKET_DIR"
}

http_request() {
  local method="$1"
  local path="$2"
  python_with_src - "$method" "$(service_url)$path" "$SPEAKER_ID_SOCKET_PATH" <<'PY'
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

if url.startswith("http://hexevoice-speaker-id"):
    parsed_path = "/" + url.split("/", 3)[3] if len(url.split("/", 3)) > 3 else "/"
    request = (
        f"{method} {parsed_path} HTTP/1.1\r\n"
        "Host: hexevoice-speaker-id\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8") + body
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
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
        client.close()
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
    print(json.dumps(json.loads(text), indent=2, sort_keys=True))
PY
}

wait_for_health() {
  local deadline
  deadline=$((SECONDS + SPEAKER_ID_HEALTH_TIMEOUT_S))
  while true; do
    if http_request GET /health >/dev/null 2>&1; then
      http_request GET /health
      return 0
    fi
    if (( SECONDS >= deadline )); then
      echo "Speaker ID health check did not pass within ${SPEAKER_ID_HEALTH_TIMEOUT_S}s at $(service_url)/health via $SPEAKER_ID_SOCKET_PATH" >&2
      return 1
    fi
    sleep "$SPEAKER_ID_HEALTH_INTERVAL_S"
  done
}

ACTION="${1:-status}"
case "$ACTION" in
  install|build)
    prepare_runtime_dirs
    ;;
  start)
    prepare_runtime_dirs
    rm -f "$SPEAKER_ID_SOCKET_PATH"
    systemctl --user start "$SPEAKER_ID_SERVICE_NAME"
    ;;
  stop)
    systemctl --user stop "$SPEAKER_ID_SERVICE_NAME"
    ;;
  restart)
    prepare_runtime_dirs
    rm -f "$SPEAKER_ID_SOCKET_PATH"
    systemctl --user restart "$SPEAKER_ID_SERVICE_NAME"
    ;;
  status)
    systemctl --user is-active "$SPEAKER_ID_SERVICE_NAME" || true
    ;;
  health)
    http_request GET /health
    ;;
  wait-health)
    wait_for_health
    ;;
  ready)
    prepare_runtime_dirs
    rm -f "$SPEAKER_ID_SOCKET_PATH"
    systemctl --user restart "$SPEAKER_ID_SERVICE_NAME"
    wait_for_health
    ;;
  doctor)
    echo "Speaker ID service: $SPEAKER_ID_SERVICE_NAME"
    echo "Speaker ID URL: $(service_url)"
    echo "Speaker ID socket: $SPEAKER_ID_SOCKET_PATH"
    if [[ -S "$SPEAKER_ID_SOCKET_PATH" ]]; then
      echo "socket: ok"
    else
      echo "socket: unavailable"
    fi
    if http_request GET /health >/dev/null 2>&1; then
      echo "health: ok"
    else
      echo "health: unavailable"
    fi
    ;;
  logs)
    journalctl --user -u "$SPEAKER_ID_SERVICE_NAME" -f -n "${2:-100}"
    ;;
  *)
    echo "Usage: $0 {install|build|start|stop|restart|status|health|wait-health|ready|doctor|logs}"
    exit 1
    ;;
esac
