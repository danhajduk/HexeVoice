#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${HEXEVOICE_REPO_URL:-https://github.com/danhajduk/HexeVoice.git}"
BRANCH="${HEXEVOICE_BRANCH:-main}"
INSTALL_ROOT="${HEXEVOICE_INSTALL_ROOT:-$HOME/hexe}"
APP_DIR="${HEXEVOICE_APP_DIR:-$INSTALL_ROOT/HexeVoice}"
RUN_BOOTSTRAP="${HEXEVOICE_RUN_BOOTSTRAP:-false}"
START_SETUP_RUNNER="${HEXEVOICE_START_SETUP_RUNNER:-true}"
SETUP_DEFAULT_ARTIFACTS="${HEXEVOICE_SETUP_DEFAULT_ARTIFACTS:-true}"
DOWNLOAD_STT_MODEL="${HEXEVOICE_DOWNLOAD_STT_MODEL:-$SETUP_DEFAULT_ARTIFACTS}"
DOWNLOAD_TTS_MODEL="${HEXEVOICE_DOWNLOAD_TTS_MODEL:-$SETUP_DEFAULT_ARTIFACTS}"
DOWNLOAD_WAKE_MODEL="${HEXEVOICE_DOWNLOAD_WAKE_MODEL:-$SETUP_DEFAULT_ARTIFACTS}"
DOWNLOAD_FIRMWARE="${HEXEVOICE_DOWNLOAD_FIRMWARE:-$SETUP_DEFAULT_ARTIFACTS}"
DEFAULT_STT_MODEL="${HEXEVOICE_DEFAULT_STT_MODEL:-base}"
DEFAULT_PIPER_VOICE="${HEXEVOICE_DEFAULT_PIPER_VOICE:-en_US-kathleen-low}"
DEFAULT_WAKE_MODEL="${HEXEVOICE_DEFAULT_WAKE_MODEL:-Hexe}"
SETUP_STT="${HEXEVOICE_SETUP_STT:-false}"
SETUP_TTS="${HEXEVOICE_SETUP_TTS:-false}"
SETUP_WAKE="${HEXEVOICE_SETUP_WAKE:-false}"
SETUP_FIRMWARE="${HEXEVOICE_SETUP_FIRMWARE:-false}"
SETUP_HOST_ALIAS="${HEXEVOICE_SETUP_HOST_ALIAS:-false}"
INSTALL_SYSTEM_PACKAGES="${HEXEVOICE_INSTALL_SYSTEM_PACKAGES:-ask}"
PRINT_PREREQ_COMMANDS="${HEXEVOICE_PRINT_PREREQ_COMMANDS:-false}"
INSTALL_STATUS_UI="${HEXEVOICE_INSTALL_STATUS_UI:-true}"
INSTALL_STATUS_UI_HOST="${HEXEVOICE_INSTALL_STATUS_UI_HOST:-0.0.0.0}"
INSTALL_STATUS_UI_PORT="${HEXEVOICE_INSTALL_STATUS_UI_PORT:-8180}"
INSTALL_STATUS_UI_PATH="${HEXEVOICE_INSTALL_STATUS_UI_PATH:-/tmp/hexevoice-install-status-$(id -u).json}"
INSTALL_STATUS_UI_PUBLIC_HOST="${HEXEVOICE_INSTALL_STATUS_UI_PUBLIC_HOST:-}"
INSTALL_STATUS_UI_OPEN_BROWSER="${HEXEVOICE_INSTALL_STATUS_UI_OPEN_BROWSER:-true}"
INSTALL_STATUS_UI_TERMINAL_LINK="${HEXEVOICE_INSTALL_STATUS_UI_TERMINAL_LINK:-false}"
INSTALL_STATUS_UI_HANDOFF_DELAY_S="${HEXEVOICE_INSTALL_STATUS_UI_HANDOFF_DELAY_S:-5}"
INSTALL_QUIET="${HEXEVOICE_INSTALL_QUIET:-true}"
INSTALL_LOG_PATH="${HEXEVOICE_INSTALL_LOG_PATH:-/tmp/hexevoice-install-$(id -u).log}"
MIN_NODE_MAJOR="${HEXEVOICE_MIN_NODE_MAJOR:-18}"
APT_UPDATED=false
SYSTEM_PACKAGE_INSTALL_APPROVED=false
INSTALL_STATUS_UI_PID=""
INSTALL_OUTPUT_REDIRECTED=false

exec 3>&1 4>&2

log() {
  printf '[hexevoice-install] %s\n' "$*"
}

terminal_log() {
  printf '[hexevoice-install] %s\n' "$*" >&3
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

system_package_install_enabled() {
  case "$INSTALL_SYSTEM_PACKAGES" in
    0|false|FALSE|no|NO|off|OFF|print|PRINT) return 1 ;;
    *) return 0 ;;
  esac
}

install_status_ui_enabled() {
  truthy "$INSTALL_STATUS_UI"
}

install_lan_host() {
  hostname -I 2>/dev/null | awk '{print $1; exit}'
}

install_public_host() {
  if [[ -n "$INSTALL_STATUS_UI_PUBLIC_HOST" ]]; then
    printf '%s\n' "$INSTALL_STATUS_UI_PUBLIC_HOST"
    return
  fi
  hostname -s 2>/dev/null || hostname 2>/dev/null || install_lan_host || printf '127.0.0.1\n'
}

print_open_url_hint() {
  local url="$1"
  terminal_log "Temporary install status UI: $url"
  if truthy "$INSTALL_STATUS_UI_TERMINAL_LINK" && true 2>/dev/null </dev/tty >/dev/tty; then
    printf '\033]8;;%s\033\\Open HexeVoice setup preparation UI\033]8;;\033\\\n' "$url" >/dev/tty || true
  else
    printf 'Open HexeVoice setup preparation UI\n' >&3
  fi
}

quiet_redirect_output() {
  if ! truthy "$INSTALL_QUIET" || [[ "$INSTALL_OUTPUT_REDIRECTED" == "true" ]]; then
    return
  fi
  mkdir -p "$(dirname "$INSTALL_LOG_PATH")"
  : > "$INSTALL_LOG_PATH"
  export CI="${CI:-1}"
  export NO_COLOR="${NO_COLOR:-1}"
  export PIP_DISABLE_PIP_VERSION_CHECK="${PIP_DISABLE_PIP_VERSION_CHECK:-1}"
  export npm_config_progress="${npm_config_progress:-false}"
  export npm_config_update_notifier="${npm_config_update_notifier:-false}"
  exec >>"$INSTALL_LOG_PATH" 2>&1
  INSTALL_OUTPUT_REDIRECTED=true
  log "Install output redirected to $INSTALL_LOG_PATH"
}

open_install_status_browser() {
  if ! truthy "$INSTALL_STATUS_UI_OPEN_BROWSER"; then
    return
  fi
  local url="$1"
  if command -v xdg-open >/dev/null 2>&1; then
    nohup xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v sensible-browser >/dev/null 2>&1; then
    nohup sensible-browser "$url" >/dev/null 2>&1 || true
  elif command -v gio >/dev/null 2>&1; then
    nohup gio open "$url" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    nohup open "$url" >/dev/null 2>&1 || true
  else
    log "No browser opener found; open $url manually."
  fi
}

install_status_update() {
  if ! install_status_ui_enabled || [[ -z "$INSTALL_STATUS_UI_PID" ]]; then
    return
  fi
  local phase="$1"
  local message="$2"
  local detail="${3:-}"
  local redirect_url="${4:-}"
  STATUS_PATH="$INSTALL_STATUS_UI_PATH" \
    STATUS_PHASE="$phase" \
    STATUS_MESSAGE="$message" \
    STATUS_DETAIL="$detail" \
    STATUS_REDIRECT_URL="$redirect_url" \
    STATUS_REDIRECT_DELAY_MS="$((INSTALL_STATUS_UI_HANDOFF_DELAY_S * 1000))" \
    python3 - <<'PY'
from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path

path = Path(os.environ["STATUS_PATH"])
payload = {
    "phase": os.environ.get("STATUS_PHASE") or "running",
    "message": os.environ.get("STATUS_MESSAGE") or "Preparing HexeVoice.",
    "detail": os.environ.get("STATUS_DETAIL") or None,
    "redirect_url": os.environ.get("STATUS_REDIRECT_URL") or None,
    "redirect_delay_ms": int(os.environ.get("STATUS_REDIRECT_DELAY_MS") or "5000"),
    "updated_at": datetime.now(UTC).isoformat(),
}
path.parent.mkdir(parents=True, exist_ok=True)
temp_path = path.with_suffix(f"{path.suffix}.tmp")
temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
temp_path.replace(path)
PY
}

stop_install_status_ui() {
  if [[ -n "$INSTALL_STATUS_UI_PID" ]] && kill -0 "$INSTALL_STATUS_UI_PID" 2>/dev/null; then
    kill "$INSTALL_STATUS_UI_PID" 2>/dev/null || true
    wait "$INSTALL_STATUS_UI_PID" 2>/dev/null || true
  fi
  INSTALL_STATUS_UI_PID=""
}

cleanup_install_status_ui() {
  stop_install_status_ui
}

finish_install() {
  local status=$?
  if [[ "$status" -ne 0 && "$INSTALL_OUTPUT_REDIRECTED" == "true" ]]; then
    terminal_log "Install failed; see log: $INSTALL_LOG_PATH"
  fi
  cleanup_install_status_ui
  exit "$status"
}

start_install_status_ui() {
  if ! install_status_ui_enabled || [[ -n "$INSTALL_STATUS_UI_PID" ]]; then
    return
  fi
  local public_host status_url
  public_host="$(install_public_host)"
  public_host="${public_host:-127.0.0.1}"
  status_url="http://${public_host}:${INSTALL_STATUS_UI_PORT}/"
  STATUS_PATH="$INSTALL_STATUS_UI_PATH" \
    STATUS_HOST="$INSTALL_STATUS_UI_HOST" \
    STATUS_PORT="$INSTALL_STATUS_UI_PORT" \
    python3 - <<'PY' &
from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

status_path = Path(os.environ["STATUS_PATH"])
host = os.environ.get("STATUS_HOST") or "0.0.0.0"
port = int(os.environ.get("STATUS_PORT") or "8180")

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HexeVoice Setup</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #101418; color: #ecf2f8; }
    main { width: min(680px, calc(100vw - 32px)); }
    h1 { margin: 0 0 12px; font-size: clamp(28px, 5vw, 46px); font-weight: 680; letter-spacing: 0; }
    p { margin: 0; color: #b5c1cc; line-height: 1.6; font-size: 17px; }
    .panel { border: 1px solid #2c3742; border-radius: 8px; padding: 28px; background: #151b21; box-shadow: 0 24px 80px rgba(0, 0, 0, 0.24); }
    .status { margin-top: 24px; display: grid; gap: 10px; }
    .bar { height: 8px; overflow: hidden; border-radius: 999px; background: #26313b; }
    .bar span { display: block; width: 42%; height: 100%; border-radius: inherit; background: #40c4aa; animation: pulse 1.6s ease-in-out infinite; }
    .label { font-size: 14px; color: #d7e1ea; }
    .detail { min-height: 22px; font-size: 13px; color: #93a3b2; overflow-wrap: anywhere; }
    @keyframes pulse { 0% { transform: translateX(-80%); } 50% { transform: translateX(65%); } 100% { transform: translateX(180%); } }
  </style>
</head>
<body>
  <main class="panel">
    <h1>HexeVoice setup is preparing</h1>
    <p>The installer is getting this host ready. This page will hand off to the setup flow when the runtime is available.</p>
    <section class="status" aria-live="polite">
      <div class="bar"><span></span></div>
      <div id="message" class="label">Starting installer...</div>
      <div id="detail" class="detail"></div>
    </section>
  </main>
  <script>
    let redirectScheduled = false;
    async function refresh() {
      try {
        const response = await fetch('/status.json', { cache: 'no-store' });
        const status = await response.json();
        document.getElementById('message').textContent = status.message || 'Preparing HexeVoice.';
        document.getElementById('detail').textContent = status.detail || '';
        if (status.redirect_url && !redirectScheduled) {
          redirectScheduled = true;
          const delay = Number(status.redirect_delay_ms || 5000);
          window.setTimeout(() => {
            window.location.assign(status.redirect_url);
          }, Math.max(1000, delay));
        }
      } catch (error) {
        document.getElementById('message').textContent = 'Waiting for installer status...';
      }
    }
    refresh();
    setInterval(refresh, 1500);
  </script>
</body>
</html>
"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/status.json"):
            try:
                payload = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {"phase": "running", "message": "Preparing HexeVoice.", "detail": None}
            body = json.dumps(payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = HTML.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return

ThreadingHTTPServer((host, port), Handler).serve_forever()
PY
  INSTALL_STATUS_UI_PID=$!
  sleep 0.3
  if kill -0 "$INSTALL_STATUS_UI_PID" 2>/dev/null; then
    print_open_url_hint "$status_url"
    open_install_status_browser "$status_url"
    install_status_update "running" "Preparing HexeVoice installer." "Checking host requirements."
  else
    log "Temporary install status UI could not start on port ${INSTALL_STATUS_UI_PORT}; continuing in terminal."
    INSTALL_STATUS_UI_PID=""
  fi
}

trap finish_install EXIT INT TERM

has_interactive_tty() {
  true 2>/dev/null </dev/tty >/dev/tty
}

confirm_system_package_install() {
  if ! system_package_install_enabled; then
    return 1
  fi
  case "$INSTALL_SYSTEM_PACKAGES" in
    1|true|TRUE|yes|YES|on|ON|auto|AUTO)
      SYSTEM_PACKAGE_INSTALL_APPROVED=true
      return 0
      ;;
  esac
  if [[ "$SYSTEM_PACKAGE_INSTALL_APPROVED" == "true" ]]; then
    return 0
  fi
  if ! has_interactive_tty; then
    printf 'Missing system packages are required, but no interactive terminal is available for approval.\n' >&2
    printf 'Rerun with HEXEVOICE_INSTALL_SYSTEM_PACKAGES=true to install them automatically, or print manual commands with HEXEVOICE_PRINT_PREREQ_COMMANDS=true.\n' >&2
    return 1
  fi
  printf '\nHexeVoice needs to install missing system packages with sudo/apt.\n' >/dev/tty
  printf 'Packages needed now: %s\n' "$*" >/dev/tty
  printf 'Install them now? [Y/n] ' >/dev/tty
  local answer
  if ! IFS= read -r answer </dev/tty; then
    printf 'Could not read install approval from the terminal.\n' >&2
    return 1
  fi
  case "$answer" in
    ""|y|Y|yes|YES)
      SYSTEM_PACKAGE_INSTALL_APPROVED=true
      return 0
      ;;
    *)
      printf 'System package install skipped by operator.\n' >&2
      printf 'Show prerequisite commands with: curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_PRINT_PREREQ_COMMANDS=true bash\n' >&2
      return 1
      ;;
  esac
}

print_prereq_commands() {
  cat <<'EOF'
# Debian/Ubuntu prerequisite install for HexeVoice:
sudo apt-get update
sudo apt-get install -y git python3 python3-venv ca-certificates curl gnupg
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
  | sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main\n' \
  | sudo tee /etc/apt/sources.list.d/nodesource.list >/dev/null
sudo apt-get update
sudo apt-get install -y nodejs
EOF
}

run_privileged() {
  if [[ "$(id -u)" == "0" ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    return 127
  fi
}

apt_install_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    return 1
  fi
  confirm_system_package_install "$@" || return 1
  install_status_update "running" "Installing host requirements." "Packages: $*"
  if [[ "$APT_UPDATED" != "true" ]]; then
    log "Updating apt package metadata"
    run_privileged env DEBIAN_FRONTEND=noninteractive apt-get update
    APT_UPDATED=true
  fi
  log "Installing system packages: $*"
  run_privileged env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
}

install_node_runtime() {
  if ! system_package_install_enabled; then
    return 1
  fi
  if command -v apt-get >/dev/null 2>&1; then
    apt_install_packages ca-certificates curl gnupg || return 1
    run_privileged install -d -m 0755 /etc/apt/keyrings
    run_privileged rm -f /etc/apt/keyrings/nodesource.gpg
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | run_privileged gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
    printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main\n' \
      | run_privileged tee /etc/apt/sources.list.d/nodesource.list >/dev/null
    APT_UPDATED=false
    apt_install_packages nodejs
    return 0
  fi
  return 1
}

ensure_command() {
  local command_name="$1"
  shift
  if command -v "$command_name" >/dev/null 2>&1; then
    return 0
  fi
  if system_package_install_enabled && [[ "$#" -gt 0 ]] && apt_install_packages "$@"; then
    command -v "$command_name" >/dev/null 2>&1 && return 0
  fi
  printf 'Missing required command: %s\n' "$command_name" >&2
  if ! system_package_install_enabled; then
    printf 'Automatic system package install is disabled by HEXEVOICE_INSTALL_SYSTEM_PACKAGES=%s\n' "$INSTALL_SYSTEM_PACKAGES" >&2
    printf 'Show prerequisite commands with: curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_PRINT_PREREQ_COMMANDS=true bash\n' >&2
  elif ! command -v apt-get >/dev/null 2>&1; then
    printf 'Automatic install currently supports apt-get based hosts only.\n' >&2
    printf 'Show Debian/Ubuntu prerequisite commands with: curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_PRINT_PREREQ_COMMANDS=true bash\n' >&2
  else
    printf 'Install it manually or rerun with sudo available.\n' >&2
    printf 'Show prerequisite commands with: curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_PRINT_PREREQ_COMMANDS=true bash\n' >&2
  fi
  exit 1
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

ensure_python_venv() {
  local temp_dir
  temp_dir="$(mktemp -d)"
  if python3 -m venv "$temp_dir/venv" >/dev/null 2>&1; then
    rm -rf "$temp_dir"
    return 0
  fi
  rm -rf "$temp_dir"
  if system_package_install_enabled && apt_install_packages python3-venv; then
    temp_dir="$(mktemp -d)"
    if python3 -m venv "$temp_dir/venv" >/dev/null 2>&1; then
      rm -rf "$temp_dir"
      return 0
    fi
    rm -rf "$temp_dir"
  fi
  printf 'Python venv support is required. Install python3-venv and rerun the installer.\n' >&2
  printf 'Show prerequisite commands with: curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_PRINT_PREREQ_COMMANDS=true bash\n' >&2
  exit 1
}

ensure_node_runtime() {
  local node_major=""
  if command -v node >/dev/null 2>&1; then
    node_major="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || true)"
  fi
  if [[ -z "$node_major" || "$node_major" -lt "$MIN_NODE_MAJOR" ]] || ! command -v npm >/dev/null 2>&1; then
    install_node_runtime || true
  fi
  if ! command -v node >/dev/null 2>&1; then
    printf 'Missing required command: node\n' >&2
    printf 'Show prerequisite commands with: curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_PRINT_PREREQ_COMMANDS=true bash\n' >&2
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    printf 'Missing required command: npm\n' >&2
    printf 'Show prerequisite commands with: curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_PRINT_PREREQ_COMMANDS=true bash\n' >&2
    exit 1
  fi
  node_major="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || true)"
  if [[ -z "$node_major" || "$node_major" -lt "$MIN_NODE_MAJOR" ]]; then
    printf 'Node.js %s+ is required; found %s.\n' "$MIN_NODE_MAJOR" "$(node --version 2>/dev/null || printf 'unknown')" >&2
    printf 'Install a newer Node.js runtime and rerun the installer.\n' >&2
    printf 'Show prerequisite commands with: curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_PRINT_PREREQ_COMMANDS=true bash\n' >&2
    exit 1
  fi
}

if truthy "$PRINT_PREREQ_COMMANDS" || [[ "$INSTALL_SYSTEM_PACKAGES" == "print" || "$INSTALL_SYSTEM_PACKAGES" == "PRINT" ]]; then
  print_prereq_commands
  exit 0
fi

ensure_command python3 python3
start_install_status_ui
if [[ -n "$INSTALL_STATUS_UI_PID" ]]; then
  quiet_redirect_output
fi
install_status_update "running" "Checking host requirements." "Verifying Git, Python venv support, Node.js, and npm."
ensure_command git git
ensure_python_venv
ensure_node_runtime

bootstrap_status_update() {
  local phase="$1"
  local current_action="${2:-}"
  local completed_action="${3:-}"
  local failure_id="${4:-}"
  local failure_message="${5:-}"
  local failure_retryable="${6:-true}"
  local pending_downloads="${7:-}"
  STATUS_PATH="${SETUP_BOOTSTRAP_STATUS_PATH:-$APP_DIR/runtime/setup/bootstrap-status.json}" \
    STATUS_PHASE="$phase" \
    STATUS_CURRENT_ACTION="$current_action" \
    STATUS_COMPLETED_ACTION="$completed_action" \
    STATUS_FAILURE_ID="$failure_id" \
    STATUS_FAILURE_MESSAGE="$failure_message" \
    STATUS_FAILURE_RETRYABLE="$failure_retryable" \
    STATUS_PENDING_DOWNLOADS="$pending_downloads" \
    python3 - <<'PY'
from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path

path = Path(os.environ["STATUS_PATH"])
try:
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except (OSError, json.JSONDecodeError):
    payload = {}
payload = payload if isinstance(payload, dict) else {}

payload["phase"] = os.environ.get("STATUS_PHASE") or payload.get("phase") or "running"
payload["current_action"] = os.environ.get("STATUS_CURRENT_ACTION") or None
payload["updated_at"] = datetime.now(UTC).isoformat()

pending_csv = os.environ.get("STATUS_PENDING_DOWNLOADS", "")
if pending_csv:
    payload["pending_downloads"] = [item.strip() for item in pending_csv.split(",") if item.strip()]
else:
    payload.setdefault("pending_downloads", [])

completed_action = os.environ.get("STATUS_COMPLETED_ACTION") or ""
completed = payload.setdefault("completed_actions", [])
if completed_action and completed_action not in completed:
    completed.append(completed_action)

failure_id = os.environ.get("STATUS_FAILURE_ID") or ""
if completed_action or failure_id:
    payload["pending_downloads"] = [
        item for item in payload.get("pending_downloads", []) if item not in {completed_action, failure_id}
    ]

if failure_id:
    failures = payload.setdefault("failures", [])
    failures = [item for item in failures if not (isinstance(item, dict) and item.get("id") == failure_id)]
    failures.append(
        {
            "id": failure_id,
            "message": os.environ.get("STATUS_FAILURE_MESSAGE") or failure_id,
            "retryable": os.environ.get("STATUS_FAILURE_RETRYABLE", "true").lower() in {"1", "true", "yes", "on"},
        }
    )
    payload["failures"] = failures

path.parent.mkdir(parents=True, exist_ok=True)
temp_path = path.with_suffix(f"{path.suffix}.tmp")
temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
temp_path.replace(path)
PY
}

run_artifact_step() {
  local step_id="$1"
  local label="$2"
  shift 2
  log "$label"
  bootstrap_status_update "running" "$step_id"
  if "$@"; then
    bootstrap_status_update "running" "" "$step_id"
  else
    local status=$?
    bootstrap_status_update "running" "" "" "$step_id" "$label failed with exit code $status." "true"
    log "$label failed; continuing so setup can show a retryable failure."
  fi
}

mkdir -p "$INSTALL_ROOT"

if [[ -d "$APP_DIR/.git" ]]; then
  log "Updating existing checkout at $APP_DIR"
  install_status_update "running" "Updating HexeVoice checkout." "$APP_DIR"
  git -C "$APP_DIR" fetch --prune origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
elif [[ -e "$APP_DIR" ]]; then
  printf 'Destination exists but is not a git checkout: %s\n' "$APP_DIR" >&2
  printf 'Move it aside or set HEXEVOICE_APP_DIR to a different path.\n' >&2
  exit 1
else
  log "Cloning $REPO_URL into $APP_DIR"
  install_status_update "running" "Downloading HexeVoice." "$REPO_URL"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

log "Initializing submodules"
install_status_update "running" "Preparing bundled documentation and submodules." "Initializing Git submodules."
git submodule update --init --recursive

log "Creating Python virtual environment"
install_status_update "running" "Preparing Python runtime." "Creating virtual environment."
python3 -m venv .venv

log "Installing Python dependencies"
install_status_update "running" "Installing Python requirements." "This can take a few minutes on a fresh host."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

log "Installing frontend dependencies"
install_status_update "running" "Installing frontend requirements." "Preparing the setup UI."
cd frontend
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi

log "Building frontend"
install_status_update "running" "Building frontend." "Preparing the production setup UI."
npm run build
cd ..

if [[ ! -f scripts/stack.env ]]; then
  log "Creating scripts/stack.env from example"
  cp scripts/stack.env.example scripts/stack.env
else
  log "Keeping existing scripts/stack.env"
fi

chmod +x scripts/*.sh

log "Preparing runtime directory skeleton"
install_status_update "running" "Preparing runtime directories." "Creating local runtime folders."
./scripts/prepare-runtime-dirs.sh

pending_downloads=()
if truthy "$DOWNLOAD_STT_MODEL"; then
  pending_downloads+=("stt:${DEFAULT_STT_MODEL}")
fi
if truthy "$DOWNLOAD_TTS_MODEL"; then
  pending_downloads+=("tts:${DEFAULT_PIPER_VOICE}.onnx")
fi
if truthy "$DOWNLOAD_WAKE_MODEL"; then
  pending_downloads+=("wake:${DEFAULT_WAKE_MODEL}")
fi
if truthy "$DOWNLOAD_FIRMWARE"; then
  pending_downloads+=("firmware")
fi
pending_downloads_csv="$(IFS=,; printf '%s' "${pending_downloads[*]}")"
bootstrap_status_update "running" "starting-setup" "" "" "" "true" "$pending_downloads_csv"

if truthy "$START_SETUP_RUNNER"; then
  mkdir -p runtime/logs
  setup_handoff_url="http://$(install_public_host):${INSTALL_STATUS_UI_PORT}/setup/host"
  install_status_update "handoff" "Opening HexeVoice setup." "The setup UI is starting now." "$setup_handoff_url"
  sleep 2
  stop_install_status_ui
  log "Starting temporary setup runner"
  SETUP_BOOTSTRAP_STATUS_PATH="${SETUP_BOOTSTRAP_STATUS_PATH:-$APP_DIR/runtime/setup/bootstrap-status.json}" \
    nohup ./scripts/setup-runner.sh --handoff none --lan-host "$(install_public_host)" --open-browser > runtime/logs/setup-runner.log 2>&1 &
  log "Temporary setup runner log: $APP_DIR/runtime/logs/setup-runner.log"
else
  install_status_update "running" "Setup runner skipped." "Continuing install in terminal."
  log "Temporary setup runner skipped by HEXEVOICE_START_SETUP_RUNNER=$START_SETUP_RUNNER"
fi

if truthy "$DOWNLOAD_STT_MODEL"; then
  run_artifact_step "stt:${DEFAULT_STT_MODEL}" "Downloading default STT model ${DEFAULT_STT_MODEL}" \
    env VOICE_STT_FASTER_WHISPER_MODEL="$DEFAULT_STT_MODEL" ./scripts/faster-whisper-stt-control.sh download-model
fi

if truthy "$DOWNLOAD_TTS_MODEL"; then
  run_artifact_step "tts:${DEFAULT_PIPER_VOICE}.onnx" "Downloading default Piper TTS voice ${DEFAULT_PIPER_VOICE}.onnx" \
    env PIPER_TTS_MODEL_PATH="/models/${DEFAULT_PIPER_VOICE}.onnx" \
      PIPER_TTS_DOWNLOAD_VOICES="$DEFAULT_PIPER_VOICE" \
      ./scripts/piper-tts-control.sh download-models
fi

if truthy "$DOWNLOAD_WAKE_MODEL"; then
  run_artifact_step "wake:${DEFAULT_WAKE_MODEL}" "Preparing default wake model ${DEFAULT_WAKE_MODEL}" \
    env OPENWAKEWORD_DEFAULT_MODEL="$DEFAULT_WAKE_MODEL" ./scripts/openwakeword-control.sh sync-models
fi

if truthy "$DOWNLOAD_FIRMWARE"; then
  run_artifact_step "firmware" "Downloading firmware artifacts" ./scripts/firmware-artifacts-control.sh download
fi

if truthy "$SETUP_HOST_ALIAS"; then
  log "Installing optional local hostname alias"
  HEXEVOICE_ENABLE_HOST_ALIAS=true ./scripts/hostname-alias-control.sh install
fi

if truthy "$RUN_BOOTSTRAP"; then
  require_command systemctl
  log "Installing and starting user services"
  ./scripts/bootstrap.sh
  if truthy "$SETUP_STT"; then
    log "Verifying and preloading external faster-whisper STT"
    ./scripts/faster-whisper-stt-control.sh ready
  fi
  if truthy "$SETUP_TTS"; then
    log "Installing, starting, and warming Piper TTS"
    ./scripts/piper-tts-control.sh ready
  fi
  if truthy "$SETUP_WAKE"; then
    log "Installing, starting, and checking openWakeWord"
    ./scripts/openwakeword-control.sh ready
  fi
  if truthy "$SETUP_FIRMWARE"; then
    log "Downloading firmware artifacts"
    ./scripts/firmware-artifacts-control.sh download
  fi
  bootstrap_status_update "complete" ""
  log "Install complete"
else
  if truthy "$SETUP_STT"; then
    log "Building, starting, and preloading external faster-whisper STT"
    ./scripts/faster-whisper-stt-control.sh ready
  fi
  if truthy "$SETUP_TTS"; then
    log "Installing, starting, and warming Piper TTS"
    ./scripts/piper-tts-control.sh ready
  fi
  if truthy "$SETUP_WAKE"; then
    log "Installing, starting, and checking openWakeWord"
    ./scripts/openwakeword-control.sh ready
  fi
  if truthy "$SETUP_FIRMWARE"; then
    log "Downloading firmware artifacts"
    ./scripts/firmware-artifacts-control.sh download
  fi
  bootstrap_status_update "complete" ""
  log "Install complete"
  printf '\nNext steps:\n'
  printf '  cd %s\n' "$APP_DIR"
  printf '  edit scripts/stack.env for this host\n'
  printf '  ./scripts/faster-whisper-stt-control.sh ready   # optional: install/start/preload STT\n'
  printf '  ./scripts/piper-tts-control.sh ready            # optional: download/start/warm Piper TTS\n'
  printf '  ./scripts/openwakeword-control.sh ready         # optional: sync/start/check wake word\n'
  printf '  ./scripts/firmware-artifacts-control.sh download # optional: fetch endpoint firmware artifacts\n'
  printf '  HEXEVOICE_SETUP_HOST_ALIAS=true ./install.sh    # optional: add HexeVoice host alias\n'
  printf '  ./scripts/bootstrap.sh\n'
fi
