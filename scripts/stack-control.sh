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
STACK_CONTROL_TIMEOUT_S="${STACK_CONTROL_TIMEOUT_S:-45}"

services=("$BACKEND_SERVICE_NAME" "$FRONTEND_SERVICE_NAME")
if systemctl --user cat "$STT_SERVICE_NAME" >/dev/null 2>&1; then
  services=("$BACKEND_SERVICE_NAME" "$STT_SERVICE_NAME" "$FRONTEND_SERVICE_NAME")
else
  echo "Skipping $STT_SERVICE_NAME: not installed. Supervisor should install it with POST /api/services/install target=stt."
fi

run_systemctl() {
  local action="$1"
  local service="$2"
  echo "$action $service ..."
  if timeout "${STACK_CONTROL_TIMEOUT_S}s" systemctl --user "$action" "$service"; then
    return 0
  else
    local status=$?
    echo "$action $service failed or timed out after ${STACK_CONTROL_TIMEOUT_S}s." >&2
    systemctl --user status "$service" --no-pager || true
    return "$status"
  fi
}

ACTION="${1:-status}"
case "$ACTION" in
  start|stop|restart)
    for service in "${services[@]}"; do
      run_systemctl "$ACTION" "$service"
    done
    ;;
  status)
    systemctl --user status "${services[@]}" --no-pager
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
