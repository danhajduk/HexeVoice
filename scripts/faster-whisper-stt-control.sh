#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/scripts/stack.env"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

STT_SERVICE_NAME="${STT_SERVICE_NAME:-${VOICE_STT_SERVICE_NAME:-hexevoice-stt.service}}"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
STT_UNIT_TEMPLATE="$ROOT_DIR/scripts/systemd/hexevoice-stt.service.in"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
JOURNALCTL_BIN="${JOURNALCTL_BIN:-journalctl}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
STT_SERVICE_URL="${STT_HEALTH_URL:-${VOICE_STT_SERVICE_BASE_URL:-http://${VOICE_STT_SERVICE_HOST:-127.0.0.1}:${VOICE_STT_SERVICE_PORT:-10300}}}"
STT_HEALTH_TIMEOUT_S="${STT_HEALTH_TIMEOUT_S:-60}"
STT_HEALTH_INTERVAL_S="${STT_HEALTH_INTERVAL_S:-2}"

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
  python_with_src - "$method" "$url" <<'PY'
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

systemctl_user() {
  "$SYSTEMCTL_BIN" --user "$@"
}

install_unit() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE. Copy scripts/stack.env.example first."
    exit 1
  fi
  mkdir -p "$SYSTEMD_DIR"
  sed "s|__ROOT_DIR__|$ROOT_DIR|g; s|__ENV_FILE__|$ENV_FILE|g" \
    "$STT_UNIT_TEMPLATE" > "$SYSTEMD_DIR/$STT_SERVICE_NAME"
  systemctl_user daemon-reload
  echo "Installed $STT_SERVICE_NAME"
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
      echo "STT health check did not pass within ${STT_HEALTH_TIMEOUT_S}s at $(service_url)/health" >&2
      return 1
    fi
    sleep "$STT_HEALTH_INTERVAL_S"
  done
}

doctor() {
  local failed=0
  echo "STT service: $STT_SERVICE_NAME"
  echo "STT URL: $(service_url)"
  if [[ -f "$ENV_FILE" ]]; then
    echo "env: ok ($ENV_FILE)"
  else
    echo "env: missing ($ENV_FILE)"
    failed=1
  fi
  if [[ -x "$PYTHON_BIN" ]]; then
    echo "python: ok ($PYTHON_BIN)"
  else
    echo "python: missing or not executable ($PYTHON_BIN)"
    failed=1
  fi
  if python_with_src - <<'PY' >/dev/null 2>&1; then
import faster_whisper  # noqa: F401
import stt.service  # noqa: F401
PY
    echo "imports: ok (faster_whisper, stt.service)"
  else
    echo "imports: failed (faster_whisper or stt.service)"
    failed=1
  fi
  if "$SYSTEMCTL_BIN" --user --version >/dev/null 2>&1; then
    echo "systemctl --user: ok"
  else
    echo "systemctl --user: unavailable"
    failed=1
  fi
  if http_request GET /health >/dev/null 2>&1; then
    echo "health: ok"
  else
    echo "health: unavailable (service may be stopped)"
  fi
  return "$failed"
}

ACTION="${1:-status}"
case "$ACTION" in
  install)
    install_unit
    ;;
  start|stop|restart)
    systemctl_user "$ACTION" "$STT_SERVICE_NAME"
    ;;
  status)
    systemctl_user is-active "$STT_SERVICE_NAME" || true
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
    install_unit
    systemctl_user restart "$STT_SERVICE_NAME"
    wait_for_health
    http_request POST /preload
    ;;
  doctor)
    doctor
    ;;
  logs)
    "$JOURNALCTL_BIN" --user -u "$STT_SERVICE_NAME" -f --lines="${2:-100}"
    ;;
  *)
    echo "Usage: $0 {install|start|stop|restart|status|health|wait-health|preload|ready|doctor|logs}"
    exit 1
    ;;
esac
