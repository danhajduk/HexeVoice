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
MIN_NODE_MAJOR="${HEXEVOICE_MIN_NODE_MAJOR:-18}"
APT_UPDATED=false
SYSTEM_PACKAGE_INSTALL_APPROVED=false

log() {
  printf '[hexevoice-install] %s\n' "$*"
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

ensure_command git git
ensure_command python3 python3
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
  git -C "$APP_DIR" fetch --prune origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
elif [[ -e "$APP_DIR" ]]; then
  printf 'Destination exists but is not a git checkout: %s\n' "$APP_DIR" >&2
  printf 'Move it aside or set HEXEVOICE_APP_DIR to a different path.\n' >&2
  exit 1
else
  log "Cloning $REPO_URL into $APP_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

log "Initializing submodules"
git submodule update --init --recursive

log "Creating Python virtual environment"
python3 -m venv .venv

log "Installing Python dependencies"
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

log "Installing frontend dependencies"
cd frontend
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi

log "Building frontend"
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
  log "Starting temporary setup runner"
  SETUP_BOOTSTRAP_STATUS_PATH="${SETUP_BOOTSTRAP_STATUS_PATH:-$APP_DIR/runtime/setup/bootstrap-status.json}" \
    nohup ./scripts/setup-runner.sh --handoff none --open-browser > runtime/logs/setup-runner.log 2>&1 &
  log "Temporary setup runner log: $APP_DIR/runtime/logs/setup-runner.log"
else
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
