#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/scripts/stack.env"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy scripts/stack.env.example first."
  exit 1
fi

. "$ENV_FILE"

BACKEND_SERVICE_NAME="${BACKEND_SERVICE_NAME:-hexevoice-backend.service}"
STT_SERVICE_NAME="${STT_SERVICE_NAME:-hexevoice-stt.service}"
SPEAKER_ID_SERVICE_NAME="${SPEAKER_ID_SERVICE_NAME:-hexevoice-speaker-id.service}"
FRONTEND_SERVICE_NAME="${FRONTEND_SERVICE_NAME:-hexevoice-frontend.service}"
OPENWAKEWORD_SERVICE_NAME="${OPENWAKEWORD_SERVICE_NAME:-hexevoice-openwakeword.service}"
PIPER_TTS_SERVICE_NAME="${PIPER_TTS_SERVICE_NAME:-hexevoice-piper-tts.service}"

speaker_id_configured=false
if [[ "${VOICE_SPEAKER_ID_ENABLED:-false}" == "true" || "$BACKEND_CMD" == *"VOICE_SPEAKER_ID_ENABLED=true"* ]]; then
  speaker_id_configured=true
fi

mkdir -p "$SYSTEMD_DIR"
if [[ -x "$ROOT_DIR/scripts/prepare-runtime-dirs.sh" ]]; then
  "$ROOT_DIR/scripts/prepare-runtime-dirs.sh"
fi

render_unit() {
  local template="$1"
  local service_name="$2"
  sed "s|__ROOT_DIR__|$ROOT_DIR|g; s|__ENV_FILE__|$ENV_FILE|g" \
    "$ROOT_DIR/scripts/systemd/$template" > "$SYSTEMD_DIR/$service_name"
}

render_unit "hexevoice-openwakeword.service.in" "$OPENWAKEWORD_SERVICE_NAME"
render_unit "hexevoice-piper-tts.service.in" "$PIPER_TTS_SERVICE_NAME"
render_unit "hexevoice-stt.service.in" "$STT_SERVICE_NAME"
render_unit "hexevoice-speaker-id.service.in" "$SPEAKER_ID_SERVICE_NAME"
render_unit "hexevoice-backend.service.in" "$BACKEND_SERVICE_NAME"
render_unit "hexevoice-frontend.service.in" "$FRONTEND_SERVICE_NAME"

systemctl --user daemon-reload

enabled_units=(
  "$OPENWAKEWORD_SERVICE_NAME"
  "$PIPER_TTS_SERVICE_NAME"
  "$STT_SERVICE_NAME"
  "$BACKEND_SERVICE_NAME"
  "$FRONTEND_SERVICE_NAME"
)
start_units=("${enabled_units[@]}")
if [[ "$speaker_id_configured" == "true" ]]; then
  enabled_units=(
    "$OPENWAKEWORD_SERVICE_NAME"
    "$PIPER_TTS_SERVICE_NAME"
    "$STT_SERVICE_NAME"
    "$SPEAKER_ID_SERVICE_NAME"
    "$BACKEND_SERVICE_NAME"
    "$FRONTEND_SERVICE_NAME"
  )
  start_units=("${enabled_units[@]}")
fi

systemctl --user enable "${enabled_units[@]}"

if command -v loginctl >/dev/null 2>&1; then
  if ! loginctl enable-linger "$USER" >/dev/null 2>&1; then
    echo "Warning: could not enable lingering for $USER. Run: loginctl enable-linger $USER"
  fi
fi

for service_name in "${start_units[@]}"; do
  systemctl --user restart "$service_name"
done

echo "Installed, enabled, and started: ${start_units[*]}"
