#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${STACK_ENV_FILE:-$ROOT_DIR/scripts/stack.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy scripts/stack.env.example first."
  exit 1
fi

. "$ENV_FILE"

STT_SERVICE_NAME="${STT_SERVICE_NAME:-hexevoice-stt.service}"
STACK_CONTROL_TIMEOUT_S="${STACK_CONTROL_TIMEOUT_S:-45}"

services=("$BACKEND_SERVICE_NAME" "$FRONTEND_SERVICE_NAME")

systemd_service_exists() {
  systemctl --user cat "$1" >/dev/null 2>&1
}

if systemd_service_exists "$STT_SERVICE_NAME"; then
  services=("$BACKEND_SERVICE_NAME" "$STT_SERVICE_NAME" "$FRONTEND_SERVICE_NAME")
else
  echo "Skipping $STT_SERVICE_NAME: not installed. Supervisor should install it with POST /api/services/install target=stt."
fi

require_core_services() {
  local missing=()

  for service in "$BACKEND_SERVICE_NAME" "$FRONTEND_SERVICE_NAME"; do
    if ! systemd_service_exists "$service"; then
      missing+=("$service")
    fi
  done

  if (( ${#missing[@]} == 0 )); then
    return 0
  fi

  echo "Missing required user systemd service(s): ${missing[*]}" >&2
  echo "Install and start them with: $ROOT_DIR/scripts/bootstrap.sh" >&2
  echo "For a temporary foreground process, run: $ROOT_DIR/scripts/run-from-env.sh backend" >&2
  echo "and in another terminal: $ROOT_DIR/scripts/run-from-env.sh frontend" >&2
  return 3
}

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
    require_core_services
    for service in "${services[@]}"; do
      run_systemctl "$ACTION" "$service"
    done
    ;;
  status)
    require_core_services
    systemctl --user status "${services[@]}" --no-pager
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
