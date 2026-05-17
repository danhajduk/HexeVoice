#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HEXEVOICE_APP_DIR:-$HOME/hexe/HexeVoice}"
REMOVE_APP_DIR=false
REMOVE_IMAGES=false
REMOVE_HOST_ALIAS=false
ASSUME_YES=false
DRY_RUN=false

log() {
  printf '[hexevoice-uninstall] %s\n' "$*"
}

usage() {
  cat <<USAGE
Usage: uninstall.sh [options]

Stops HexeVoice runtime pieces and removes generated local service/container state.

Options:
  --app-dir PATH       HexeVoice checkout/runtime path. Default: ~/hexe/HexeVoice
  --remove-app-dir    Delete the checkout/runtime directory after stopping services.
  --remove-images     Also remove local HexeVoice Docker images when possible.
  --remove-host-alias Remove HexeVoice aliases from /etc/hosts when present.
  --yes               Do not prompt before destructive actions.
  --dry-run           Print actions without changing anything.
  -h, --help          Show this help.

Examples:
  ./uninstall.sh
  ./uninstall.sh --remove-app-dir
  curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/uninstall.sh | bash -s -- --remove-app-dir
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-dir)
      APP_DIR="${2:-}"
      shift 2
      ;;
    --remove-app-dir)
      REMOVE_APP_DIR=true
      shift
      ;;
    --remove-images)
      REMOVE_IMAGES=true
      shift
      ;;
    --remove-host-alias)
      REMOVE_HOST_ALIAS=true
      shift
      ;;
    --yes|-y)
      ASSUME_YES=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$APP_DIR" ]]; then
  printf 'APP_DIR cannot be empty.\n' >&2
  exit 2
fi

run() {
  if "$DRY_RUN"; then
    printf '[dry-run] '
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

run_shell() {
  local command="$1"
  if "$DRY_RUN"; then
    printf '[dry-run] %s\n' "$command"
  else
    bash -lc "$command"
  fi
}

confirm() {
  local prompt="$1"
  if "$ASSUME_YES"; then
    return 0
  fi
  if ! true 2>/dev/null </dev/tty >/dev/tty; then
    printf '%s Use --yes to approve non-interactively.\n' "$prompt" >&2
    return 1
  fi
  printf '%s [y/N] ' "$prompt" >/dev/tty
  local answer
  IFS= read -r answer </dev/tty || answer=""
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

process_in_app_dir() {
  local pid="$1"
  local cwd
  cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
  cwd="${cwd% (deleted)}"
  [[ "$cwd" == "$APP_DIR" || "$cwd" == "$APP_DIR"/* ]]
}

kill_matching_processes() {
  local pattern="$1"
  local label="$2"
  if ! command -v pgrep >/dev/null 2>&1; then
    return
  fi
  local pids
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return
  fi
  local announced=false
  while IFS= read -r pid; do
    [[ -z "$pid" || "$pid" == "$$" ]] && continue
    process_in_app_dir "$pid" || continue
    if ! "$announced"; then
      log "Stopping $label"
      announced=true
    fi
    run kill "$pid" 2>/dev/null || true
  done <<<"$pids"

  if "$DRY_RUN"; then
    return
  fi

  sleep 1
  while IFS= read -r pid; do
    [[ -z "$pid" || "$pid" == "$$" ]] && continue
    [[ -e "/proc/$pid" ]] || continue
    process_in_app_dir "$pid" || continue
    run kill -9 "$pid" 2>/dev/null || true
  done <<<"$pids"
}

docker_compose_down() {
  if ! command -v docker >/dev/null 2>&1; then
    return
  fi
  local compose_files=(
    "$APP_DIR/compose.faster-whisper-stt.yaml"
    "$APP_DIR/compose.faster-whisper-stt.cuda.yaml"
    "$APP_DIR/compose.piper-tts.yaml"
    "$APP_DIR/compose.openwakeword.yaml"
  )
  log "Stopping HexeVoice compose stacks if present."
  for compose_file in "${compose_files[@]}"; do
    [[ -f "$compose_file" ]] || continue
    run docker compose -f "$compose_file" down --remove-orphans 2>/dev/null || true
  done
}

docker_remove_containers() {
  if ! command -v docker >/dev/null 2>&1; then
    log "Docker not found; skipping containers."
    return
  fi
  local containers=(
    hexevoice-faster-whisper-stt
    hexevoice-piper-tts
    hexevoice-openwakeword
  )
  log "Removing HexeVoice containers if present."
  run docker rm -f "${containers[@]}" 2>/dev/null || true
}

docker_remove_images() {
  if ! "$REMOVE_IMAGES"; then
    return
  fi
  if ! command -v docker >/dev/null 2>&1; then
    return
  fi
  log "Removing HexeVoice Docker images if present."
  run_shell "docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^hexevoice/' | xargs -r docker rmi"
}

load_service_names() {
  BACKEND_SERVICE_NAME="hexevoice-backend.service"
  STT_SERVICE_NAME="hexevoice-stt.service"
  FRONTEND_SERVICE_NAME="hexevoice-frontend.service"
  if [[ -f "$APP_DIR/scripts/stack.env" ]]; then
    # shellcheck disable=SC1090
    . "$APP_DIR/scripts/stack.env" || true
    BACKEND_SERVICE_NAME="${BACKEND_SERVICE_NAME:-hexevoice-backend.service}"
    STT_SERVICE_NAME="${STT_SERVICE_NAME:-hexevoice-stt.service}"
    FRONTEND_SERVICE_NAME="${FRONTEND_SERVICE_NAME:-hexevoice-frontend.service}"
  fi
}

remove_user_services() {
  if ! command -v systemctl >/dev/null 2>&1; then
    log "systemctl not found; skipping user services."
    return
  fi
  load_service_names
  local systemd_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  local services=("$BACKEND_SERVICE_NAME" "$FRONTEND_SERVICE_NAME" "$STT_SERVICE_NAME")
  local service_file
  shopt -s nullglob
  for service_file in "$systemd_dir"/hexevoice-*.service; do
    services+=("$(basename "$service_file")")
  done
  shopt -u nullglob
  local unique_services=()
  local seen=" "
  local service
  for service in "${services[@]}"; do
    [[ -n "$service" ]] || continue
    if [[ "$seen" != *" $service "* ]]; then
      unique_services+=("$service")
      seen+="$service "
    fi
  done
  if [[ "${#unique_services[@]}" -eq 0 ]]; then
    return
  fi
  log "Stopping HexeVoice user services if present."
  run systemctl --user stop "${unique_services[@]}" 2>/dev/null || true
  log "Disabling HexeVoice user services if present."
  run systemctl --user disable "${unique_services[@]}" 2>/dev/null || true
  log "Removing generated user service files."
  for service in "${unique_services[@]}"; do
    run rm -f "$systemd_dir/$service"
  done
  run systemctl --user daemon-reload 2>/dev/null || true
  run systemctl --user reset-failed "${unique_services[@]}" 2>/dev/null || true
}

remove_host_alias() {
  if ! "$REMOVE_HOST_ALIAS"; then
    return
  fi
  if [[ ! -f /etc/hosts ]]; then
    return
  fi
  if ! grep -Eq '(^|[[:space:]])HexeVoice(\.local)?([[:space:]]|$)' /etc/hosts; then
    log "No HexeVoice host alias found."
    return
  fi
  if ! confirm "Remove HexeVoice aliases from /etc/hosts?"; then
    log "Keeping /etc/hosts unchanged."
    return
  fi
  local command
  command="sudo cp /etc/hosts /etc/hosts.hexevoice-uninstall-\$(date +%Y%m%dT%H%M%S) && sudo sed -i -E '/(^|[[:space:]])HexeVoice(\\.local)?([[:space:]]|$)/d' /etc/hosts"
  log "Removing HexeVoice host alias lines from /etc/hosts."
  run_shell "$command"
}

remove_app_dir() {
  if ! "$REMOVE_APP_DIR"; then
    log "Keeping app directory: $APP_DIR"
    log "Pass --remove-app-dir to delete it."
    return
  fi
  if [[ ! -e "$APP_DIR" ]]; then
    log "App directory already absent: $APP_DIR"
    return
  fi
  case "$APP_DIR" in
    "$HOME"/hexe/HexeVoice|"$HOME"/hexe/HexeVoice/) ;;
    */HexeVoice|*/HexeVoice/) ;;
    *)
      printf 'Refusing to remove non-HexeVoice-looking path: %s\n' "$APP_DIR" >&2
      printf 'Set HEXEVOICE_APP_DIR to the exact HexeVoice checkout or remove it manually.\n' >&2
      exit 1
      ;;
  esac
  if ! confirm "Delete HexeVoice app directory $APP_DIR?"; then
    log "Keeping app directory: $APP_DIR"
    return
  fi
  log "Deleting app directory: $APP_DIR"
  if run rm -rf "$APP_DIR"; then
    return
  fi
  if "$DRY_RUN"; then
    return
  fi
  log "Normal delete failed."
  if command -v findmnt >/dev/null 2>&1; then
    findmnt -R "$APP_DIR" || true
  fi
  if command -v sudo >/dev/null 2>&1 && confirm "Retry deleting $APP_DIR with sudo?"; then
    run sudo rm -rf "$APP_DIR"
  else
    printf 'Could not remove %s. Check for mounted paths or root-owned files.\n' "$APP_DIR" >&2
    return 1
  fi
}

main() {
  log "Using app directory: $APP_DIR"
  kill_matching_processes "install.sh" "installer process"
  kill_matching_processes "scripts/setup-runner.sh|setup-runner.sh" "setup runner"
  kill_matching_processes "python -m hexevoice.main|hexevoice.main" "backend process"
  kill_matching_processes "vite .*--port (8180|8084)|npm run (dev|preview)" "frontend process"
  remove_user_services
  docker_compose_down
  docker_remove_containers
  docker_remove_images
  run rm -f "/tmp/hexevoice-install-status-$(id -u).json"
  remove_host_alias
  remove_app_dir
  log "Uninstall cleanup complete."
  cd ~/hexe/ || true
}

main
