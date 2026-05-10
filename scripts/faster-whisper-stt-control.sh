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

ACTION="${1:-status}"
case "$ACTION" in
  install)
    if [[ ! -f "$ENV_FILE" ]]; then
      echo "Missing $ENV_FILE. Copy scripts/stack.env.example first."
      exit 1
    fi
    mkdir -p "$SYSTEMD_DIR"
    sed "s|__ROOT_DIR__|$ROOT_DIR|g; s|__ENV_FILE__|$ENV_FILE|g" \
      "$STT_UNIT_TEMPLATE" > "$SYSTEMD_DIR/$STT_SERVICE_NAME"
    systemctl --user daemon-reload
    echo "Installed $STT_SERVICE_NAME"
    ;;
  start|stop|restart)
    systemctl --user "$ACTION" "$STT_SERVICE_NAME"
    ;;
  status)
    systemctl --user is-active "$STT_SERVICE_NAME" || true
    ;;
  logs)
    journalctl --user -u "$STT_SERVICE_NAME" -f --lines="${2:-100}"
    ;;
  *)
    echo "Usage: $0 {install|start|stop|restart|status|logs}"
    exit 1
    ;;
esac
