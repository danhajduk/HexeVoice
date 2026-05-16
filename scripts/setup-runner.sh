#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TEMP_BACKEND_PORT="${TEMP_BACKEND_PORT:-9100}"
TEMP_FRONTEND_PORT="${TEMP_FRONTEND_PORT:-8180}"
PRODUCTION_FRONTEND_PORT="${PRODUCTION_FRONTEND_PORT:-8084}"
TEMP_BACKEND_HOST="${TEMP_BACKEND_HOST:-0.0.0.0}"
TEMP_FRONTEND_HOST="${TEMP_FRONTEND_HOST:-0.0.0.0}"
SETUP_RUNNER_LAN_HOST="${SETUP_RUNNER_LAN_HOST:-}"
SETUP_RUNNER_SHUTDOWN_DELAY_S="${SETUP_RUNNER_SHUTDOWN_DELAY_S:-120}"
SETUP_RUNNER_PRODUCTION_TIMEOUT_S="${SETUP_RUNNER_PRODUCTION_TIMEOUT_S:-600}"
SETUP_RUNNER_POLL_INTERVAL_S="${SETUP_RUNNER_POLL_INTERVAL_S:-3}"
SETUP_RUNNER_HANDOFF_MODE="${SETUP_RUNNER_HANDOFF_MODE:-none}"
SETUP_RUNNER_OPEN_BROWSER="${SETUP_RUNNER_OPEN_BROWSER:-false}"
DEFAULT_CORE_SUPERVISOR_INSTALLER="$ROOT_DIR/docs/Core-Documents/scripts/install-supervisor.sh"
if [[ ! -x "$DEFAULT_CORE_SUPERVISOR_INSTALLER" ]]; then
  DEFAULT_CORE_SUPERVISOR_INSTALLER="install-supervisor.sh"
fi
CORE_SUPERVISOR_INSTALLER="${CORE_SUPERVISOR_INSTALLER:-$DEFAULT_CORE_SUPERVISOR_INSTALLER}"
CORE_SUPERVISOR_URL="${CORE_SUPERVISOR_URL:-}"
CORE_SUPERVISOR_ID="${CORE_SUPERVISOR_ID:-hexevoice-supervisor}"
CORE_SUPERVISOR_ENROLLMENT_TOKEN="${CORE_SUPERVISOR_ENROLLMENT_TOKEN:-}"

TEMP_BACKEND_PID=""
TEMP_FRONTEND_PID=""
REDIRECT_PID=""
HANDOFF_PID=""
PRODUCTION_READY="false"

usage() {
  cat <<USAGE
Usage: $0 [--handoff MODE] [--lan-host HOST] [--open-browser]

Starts a temporary HexeVoice setup API/UI:
  API: http://<lan-host>:${TEMP_BACKEND_PORT}
  UI:  http://<lan-host>:${TEMP_FRONTEND_PORT}/setup

Handoff modes:
  none                  Start only the temporary setup API/UI.
  existing-supervisor   Wait for production UI managed outside this runner.
  systemd               Run scripts/bootstrap.sh to install/start user services.
  standalone-supervisor Run Core supervisor installer with --standalone.
  joined-supervisor     Run Core supervisor installer with --join-core.

Joined Supervisor requires:
  CORE_SUPERVISOR_URL
  CORE_SUPERVISOR_ENROLLMENT_TOKEN
  CORE_SUPERVISOR_ID (optional, default: hexevoice-supervisor)
USAGE
}

log() {
  printf '[setup-runner] %s\n' "$*"
}

lan_host() {
  if [[ -n "$SETUP_RUNNER_LAN_HOST" ]]; then
    printf '%s\n' "$SETUP_RUNNER_LAN_HOST"
    return
  fi
  hostname -I 2>/dev/null | awk '{print $1; exit}'
}

require_file() {
  local path="$1"
  local message="$2"
  if [[ ! -e "$path" ]]; then
    log "$message"
    exit 1
  fi
}

installer_available() {
  if [[ "$CORE_SUPERVISOR_INSTALLER" == */* ]]; then
    [[ -x "$CORE_SUPERVISOR_INSTALLER" ]]
  else
    command -v "$CORE_SUPERVISOR_INSTALLER" >/dev/null 2>&1
  fi
}

stop_pid() {
  local pid="$1"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
}

cleanup() {
  local exit_code=$?
  stop_pid "$TEMP_BACKEND_PID"
  stop_pid "$TEMP_FRONTEND_PID"
  stop_pid "$REDIRECT_PID"
  wait 2>/dev/null || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

while [[ $# -gt 0 ]]; do
  case "$1" in
    --handoff)
      SETUP_RUNNER_HANDOFF_MODE="${2:-}"
      shift 2
      ;;
    --lan-host)
      SETUP_RUNNER_LAN_HOST="${2:-}"
      shift 2
      ;;
    --open-browser)
      SETUP_RUNNER_OPEN_BROWSER="true"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      log "Unknown argument: $1"
      usage
      exit 2
      ;;
  esac
done

LAN_HOST="$(lan_host)"
LAN_HOST="${LAN_HOST:-127.0.0.1}"
TEMP_API_BASE_URL="http://${LAN_HOST}:${TEMP_BACKEND_PORT}"
TEMP_SETUP_URL="http://${LAN_HOST}:${TEMP_FRONTEND_PORT}/setup"
PRODUCTION_SETUP_URL="${SETUP_RUNNER_PRODUCTION_URL:-http://${LAN_HOST}:${PRODUCTION_FRONTEND_PORT}/setup}"

require_file "$ROOT_DIR/.venv/bin/python" "Missing backend virtualenv at $ROOT_DIR/.venv/bin/python"
require_file "$ROOT_DIR/frontend/node_modules" "Missing frontend dependencies at $ROOT_DIR/frontend/node_modules"

start_temp_backend() {
  log "Starting temporary backend on ${TEMP_API_BASE_URL}"
  (
    cd "$ROOT_DIR"
    API_HOST="$TEMP_BACKEND_HOST" \
      API_PORT="$TEMP_BACKEND_PORT" \
      PUBLIC_API_BASE_URL="$TEMP_API_BASE_URL" \
      PUBLIC_UI_BASE_URL="$TEMP_SETUP_URL" \
      HEXEVOICE_SETUP_RUNNER_MODE="temporary" \
      PYTHONPATH=src \
      .venv/bin/python -m hexevoice.main
  ) &
  TEMP_BACKEND_PID=$!
}

start_temp_frontend() {
  log "Starting temporary frontend on ${TEMP_SETUP_URL}"
  (
    cd "$ROOT_DIR/frontend"
    VITE_PROXY_TARGET="http://127.0.0.1:${TEMP_BACKEND_PORT}" \
      npm run dev -- --host "$TEMP_FRONTEND_HOST" --port "$TEMP_FRONTEND_PORT" --strictPort
  ) &
  TEMP_FRONTEND_PID=$!
}

open_browser() {
  if [[ "$SETUP_RUNNER_OPEN_BROWSER" != "true" ]]; then
    return
  fi
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$TEMP_SETUP_URL" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$TEMP_SETUP_URL" >/dev/null 2>&1 || true
  else
    log "No browser opener found; open ${TEMP_SETUP_URL} manually."
  fi
}

run_handoff() {
  case "$SETUP_RUNNER_HANDOFF_MODE" in
    none)
      log "No production handoff requested yet."
      ;;
    existing-supervisor)
      log "Existing Supervisor mode selected; waiting for production setup URL."
      ;;
    systemd)
      log "Starting unsupervised systemd user services through scripts/bootstrap.sh"
      "$ROOT_DIR/scripts/bootstrap.sh" &
      HANDOFF_PID=$!
      ;;
    standalone-supervisor)
      if ! installer_available; then
        log "Supervisor installer not found: ${CORE_SUPERVISOR_INSTALLER}. Keeping temporary setup running."
        return
      fi
      log "Installing standalone Core Supervisor."
      "$CORE_SUPERVISOR_INSTALLER" --standalone &
      HANDOFF_PID=$!
      ;;
    joined-supervisor)
      if ! installer_available; then
        log "Supervisor installer not found: ${CORE_SUPERVISOR_INSTALLER}. Keeping temporary setup running."
        return
      fi
      if [[ -z "$CORE_SUPERVISOR_URL" || -z "$CORE_SUPERVISOR_ENROLLMENT_TOKEN" ]]; then
        log "Joined Supervisor requires CORE_SUPERVISOR_URL and CORE_SUPERVISOR_ENROLLMENT_TOKEN."
        return
      fi
      log "Installing joined Core Supervisor for ${CORE_SUPERVISOR_URL}."
      "$CORE_SUPERVISOR_INSTALLER" \
        --join-core \
        --core-url "$CORE_SUPERVISOR_URL" \
        --enrollment-token "$CORE_SUPERVISOR_ENROLLMENT_TOKEN" \
        --supervisor-id "$CORE_SUPERVISOR_ID" &
      HANDOFF_PID=$!
      ;;
    *)
      log "Unknown handoff mode: ${SETUP_RUNNER_HANDOFF_MODE}"
      exit 2
      ;;
  esac
}

production_ready() {
  curl -fsS --max-time 2 "$PRODUCTION_SETUP_URL" >/dev/null 2>&1
}

start_redirect_server() {
  local delay="$1"
  stop_pid "$TEMP_FRONTEND_PID"
  wait "$TEMP_FRONTEND_PID" 2>/dev/null || true
  TEMP_FRONTEND_PID=""
  log "Production setup is healthy; redirecting temporary UI to ${PRODUCTION_SETUP_URL}"
  TARGET_URL="$PRODUCTION_SETUP_URL" TEMP_FRONTEND_PORT="$TEMP_FRONTEND_PORT" "$ROOT_DIR/.venv/bin/python" - <<'PY' &
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

target = os.environ["TARGET_URL"]
port = int(os.environ.get("TEMP_FRONTEND_PORT", "8180"))

class RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()

    def log_message(self, fmt, *args):
        return

ThreadingHTTPServer(("0.0.0.0", port), RedirectHandler).serve_forever()
PY
  REDIRECT_PID=$!
  log "Temporary runner will stop in ${delay}s."
  sleep "$delay"
}

start_temp_backend
start_temp_frontend

log "Temporary setup URL: ${TEMP_SETUP_URL}"
log "Temporary backend API: ${TEMP_API_BASE_URL}"
open_browser
run_handoff

deadline=$((SECONDS + SETUP_RUNNER_PRODUCTION_TIMEOUT_S))
while (( SECONDS < deadline )); do
  if production_ready; then
    PRODUCTION_READY="true"
    break
  fi
  if [[ -n "$HANDOFF_PID" ]] && ! kill -0 "$HANDOFF_PID" 2>/dev/null; then
    wait "$HANDOFF_PID" || log "Production handoff command exited with an error."
    HANDOFF_PID=""
  fi
  sleep "$SETUP_RUNNER_POLL_INTERVAL_S"
done

if [[ "$PRODUCTION_READY" == "true" ]]; then
  start_redirect_server "$SETUP_RUNNER_SHUTDOWN_DELAY_S"
else
  log "Production setup URL was not healthy before timeout: ${PRODUCTION_SETUP_URL}"
  log "Temporary setup remains active until this runner is stopped."
  wait -n "$TEMP_BACKEND_PID" "$TEMP_FRONTEND_PID"
fi
