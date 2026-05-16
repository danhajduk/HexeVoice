#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${HEXEVOICE_REPO_URL:-https://github.com/danhajduk/HexeVoice.git}"
BRANCH="${HEXEVOICE_BRANCH:-main}"
INSTALL_ROOT="${HEXEVOICE_INSTALL_ROOT:-$HOME/hexe}"
APP_DIR="${HEXEVOICE_APP_DIR:-$INSTALL_ROOT/HexeVoice}"
RUN_BOOTSTRAP="${HEXEVOICE_RUN_BOOTSTRAP:-false}"
SETUP_STT="${HEXEVOICE_SETUP_STT:-false}"
SETUP_TTS="${HEXEVOICE_SETUP_TTS:-false}"
SETUP_WAKE="${HEXEVOICE_SETUP_WAKE:-false}"
SETUP_FIRMWARE="${HEXEVOICE_SETUP_FIRMWARE:-false}"

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

if [[ "$RUN_BOOTSTRAP" == "true" || "$RUN_BOOTSTRAP" == "1" || "$RUN_BOOTSTRAP" == "yes" ]]; then
  require_command systemctl
  log "Installing and starting user services"
  ./scripts/bootstrap.sh
  if [[ "$SETUP_STT" == "true" || "$SETUP_STT" == "1" || "$SETUP_STT" == "yes" ]]; then
    log "Verifying and preloading external faster-whisper STT"
    ./scripts/faster-whisper-stt-control.sh ready
  fi
  if [[ "$SETUP_TTS" == "true" || "$SETUP_TTS" == "1" || "$SETUP_TTS" == "yes" ]]; then
    log "Installing, starting, and warming Piper TTS"
    ./scripts/piper-tts-control.sh ready
  fi
  if [[ "$SETUP_WAKE" == "true" || "$SETUP_WAKE" == "1" || "$SETUP_WAKE" == "yes" ]]; then
    log "Installing, starting, and checking openWakeWord"
    ./scripts/openwakeword-control.sh ready
  fi
  if [[ "$SETUP_FIRMWARE" == "true" || "$SETUP_FIRMWARE" == "1" || "$SETUP_FIRMWARE" == "yes" ]]; then
    log "Downloading firmware artifacts"
    ./scripts/firmware-artifacts-control.sh download
  fi
else
  if [[ "$SETUP_STT" == "true" || "$SETUP_STT" == "1" || "$SETUP_STT" == "yes" ]]; then
    log "Building, starting, and preloading external faster-whisper STT"
    ./scripts/faster-whisper-stt-control.sh ready
  fi
  if [[ "$SETUP_TTS" == "true" || "$SETUP_TTS" == "1" || "$SETUP_TTS" == "yes" ]]; then
    log "Installing, starting, and warming Piper TTS"
    ./scripts/piper-tts-control.sh ready
  fi
  if [[ "$SETUP_WAKE" == "true" || "$SETUP_WAKE" == "1" || "$SETUP_WAKE" == "yes" ]]; then
    log "Installing, starting, and checking openWakeWord"
    ./scripts/openwakeword-control.sh ready
  fi
  if [[ "$SETUP_FIRMWARE" == "true" || "$SETUP_FIRMWARE" == "1" || "$SETUP_FIRMWARE" == "yes" ]]; then
    log "Downloading firmware artifacts"
    ./scripts/firmware-artifacts-control.sh download
  fi
  log "Install complete"
  printf '\nNext steps:\n'
  printf '  cd %s\n' "$APP_DIR"
  printf '  edit scripts/stack.env for this host\n'
  printf '  ./scripts/faster-whisper-stt-control.sh ready   # optional: install/start/preload STT\n'
  printf '  ./scripts/piper-tts-control.sh ready            # optional: download/start/warm Piper TTS\n'
  printf '  ./scripts/openwakeword-control.sh ready         # optional: sync/start/check wake word\n'
  printf '  ./scripts/firmware-artifacts-control.sh download # optional: fetch endpoint firmware artifacts\n'
  printf '  ./scripts/bootstrap.sh\n'
fi
