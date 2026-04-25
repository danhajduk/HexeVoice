#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-9010}"

if command -v nc >/dev/null 2>&1; then
  exec nc -klu "${PORT}"
fi

echo "Missing netcat. Install nc/netcat to listen for firmware UDP logs." >&2
exit 1
