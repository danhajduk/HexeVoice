#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/scripts/stack.env"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

STT_SERVICE_NAME="${STT_SERVICE_NAME:-${VOICE_STT_SERVICE_NAME:-hexevoice-stt.service}}"

ACTION="${1:-status}"
case "$ACTION" in
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
    echo "Usage: $0 {start|stop|restart|status|logs}"
    exit 1
    ;;
esac
