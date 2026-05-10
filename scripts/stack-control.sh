#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/scripts/stack.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy scripts/stack.env.example first."
  exit 1
fi

. "$ENV_FILE"

STT_SERVICE_NAME="${STT_SERVICE_NAME:-hexevoice-stt.service}"

ACTION="${1:-status}"
case "$ACTION" in
  start|stop|restart|status)
    services=("$BACKEND_SERVICE_NAME" "$FRONTEND_SERVICE_NAME")
    if systemctl --user cat "$STT_SERVICE_NAME" >/dev/null 2>&1; then
      services=("$BACKEND_SERVICE_NAME" "$STT_SERVICE_NAME" "$FRONTEND_SERVICE_NAME")
    else
      echo "Skipping $STT_SERVICE_NAME: not installed. Supervisor should install it with POST /api/services/install target=stt."
    fi
    systemctl --user "$ACTION" "${services[@]}"
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
