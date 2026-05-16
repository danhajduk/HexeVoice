#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${HEXEVOICE_REPO_URL:-https://github.com/danhajduk/HexeVoice.git}"
BRANCH="${HEXEVOICE_BRANCH:-main}"
INSTALL_ROOT="${HEXEVOICE_INSTALL_ROOT:-$HOME/hexe}"
APP_DIR="${HEXEVOICE_APP_DIR:-$INSTALL_ROOT/HexeVoice}"
RUN_BOOTSTRAP="${HEXEVOICE_RUN_BOOTSTRAP:-false}"

log() {
  printf '[hexevoice-install] %s\n' "$*"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

require_command git
require_command python3
require_command npm

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

if [[ "$RUN_BOOTSTRAP" == "true" || "$RUN_BOOTSTRAP" == "1" || "$RUN_BOOTSTRAP" == "yes" ]]; then
  require_command systemctl
  log "Installing and starting user services"
  ./scripts/bootstrap.sh
else
  log "Install complete"
  printf '\nNext steps:\n'
  printf '  cd %s\n' "$APP_DIR"
  printf '  edit scripts/stack.env for this host\n'
  printf '  ./scripts/bootstrap.sh\n'
fi
