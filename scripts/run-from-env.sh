#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/scripts/stack.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy scripts/stack.env.example first."
  exit 1
fi

. "$ENV_FILE"

case "${1:-}" in
  backend)
    eval "$BACKEND_CMD"
    ;;
  frontend)
    eval "$FRONTEND_CMD"
    ;;
  *)
    echo "Usage: $0 {backend|frontend}"
    exit 1
    ;;
esac
