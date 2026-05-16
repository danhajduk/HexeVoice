#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
HOSTS_PATH="${HEXEVOICE_HOSTS_PATH:-/etc/hosts}"
ALIAS="${HEXEVOICE_HOST_ALIAS:-HexeVoice}"
LOCAL_ALIAS="${HEXEVOICE_HOST_ALIAS_LOCAL:-${ALIAS}.local}"
ADDRESS="${HEXEVOICE_HOST_ALIAS_ADDRESS:-127.0.1.1}"
CURRENT_HOSTNAME="${HEXEVOICE_CURRENT_HOSTNAME:-$(hostname -s 2>/dev/null || hostname 2>/dev/null || printf 'localhost')}"

truthy() {
  case "${1:-}" in
    true|1|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

alias_present() {
  local alias_value="$1"
  [[ -f "$HOSTS_PATH" ]] && grep -Eq "(^|[[:space:]])${alias_value}([[:space:]]|$)" "$HOSTS_PATH"
}

all_aliases_present() {
  alias_present "$ALIAS" && alias_present "$LOCAL_ALIAS"
}

json_bool() {
  if "$@"; then
    printf 'true'
  else
    printf 'false'
  fi
}

print_status() {
  printf '{\n'
  printf '  "hosts_path": "%s",\n' "$HOSTS_PATH"
  printf '  "hostname": "%s",\n' "$CURRENT_HOSTNAME"
  printf '  "address": "%s",\n' "$ADDRESS"
  printf '  "aliases": ["%s", "%s"],\n' "$ALIAS" "$LOCAL_ALIAS"
  printf '  "present": %s\n' "$(json_bool all_aliases_present)"
  printf '}\n'
}

append_alias_line() {
  mkdir -p "$(dirname "$HOSTS_PATH")"
  touch "$HOSTS_PATH"
  local backup_path="${HOSTS_PATH}.hexevoice-backup-$(date +%Y%m%dT%H%M%S)"
  cp "$HOSTS_PATH" "$backup_path"
  {
    if [[ -s "$HOSTS_PATH" ]]; then
      tail -c 1 "$HOSTS_PATH" | od -An -t x1 | grep -q '0a' || printf '\n'
    fi
    printf '%s %s %s %s\n' "$ADDRESS" "$CURRENT_HOSTNAME" "$ALIAS" "$LOCAL_ALIAS"
  } >> "$HOSTS_PATH"
  printf 'host_alias_installed:%s:%s,%s\n' "$HOSTS_PATH" "$ALIAS" "$LOCAL_ALIAS"
  printf 'backup:%s\n' "$backup_path"
}

case "$ACTION" in
  status)
    print_status
    ;;
  dry-run)
    print_status
    if all_aliases_present; then
      printf 'already_present:%s,%s\n' "$ALIAS" "$LOCAL_ALIAS"
    else
      printf 'would_append:%s %s %s %s\n' "$ADDRESS" "$CURRENT_HOSTNAME" "$ALIAS" "$LOCAL_ALIAS"
    fi
    ;;
  install)
    if ! truthy "${HEXEVOICE_ENABLE_HOST_ALIAS:-false}"; then
      printf 'host_alias_not_enabled: set HEXEVOICE_ENABLE_HOST_ALIAS=true to modify %s\n' "$HOSTS_PATH" >&2
      exit 2
    fi
    if all_aliases_present; then
      printf 'host_alias_already_present:%s,%s\n' "$ALIAS" "$LOCAL_ALIAS"
      exit 0
    fi
    if [[ "$HOSTS_PATH" == "/etc/hosts" && "${EUID:-$(id -u)}" != "0" ]]; then
      if command -v sudo >/dev/null 2>&1; then
        exec sudo \
          HEXEVOICE_ENABLE_HOST_ALIAS="${HEXEVOICE_ENABLE_HOST_ALIAS:-false}" \
          HEXEVOICE_HOST_ALIAS="$ALIAS" \
          HEXEVOICE_HOST_ALIAS_LOCAL="$LOCAL_ALIAS" \
          HEXEVOICE_HOST_ALIAS_ADDRESS="$ADDRESS" \
          HEXEVOICE_CURRENT_HOSTNAME="$CURRENT_HOSTNAME" \
          "$0" install
      fi
      printf 'host_alias_requires_root: rerun with sudo or set HEXEVOICE_HOSTS_PATH for testing\n' >&2
      exit 1
    fi
    append_alias_line
    ;;
  *)
    printf 'usage: %s [status|dry-run|install]\n' "$0" >&2
    exit 64
    ;;
esac
