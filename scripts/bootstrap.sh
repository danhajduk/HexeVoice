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

mkdir -p "$SYSTEMD_DIR"
sed "s|__ROOT_DIR__|$ROOT_DIR|g; s|__ENV_FILE__|$ENV_FILE|g; s|__BACKEND_CMD__|$BACKEND_CMD|g" \
  "$ROOT_DIR/scripts/systemd/hexevoice-backend.service.in" > "$SYSTEMD_DIR/$BACKEND_SERVICE_NAME"
sed "s|__ROOT_DIR__|$ROOT_DIR|g; s|__ENV_FILE__|$ENV_FILE|g; s|__FRONTEND_CMD__|$FRONTEND_CMD|g" \
  "$ROOT_DIR/scripts/systemd/hexevoice-frontend.service.in" > "$SYSTEMD_DIR/$FRONTEND_SERVICE_NAME"

systemctl --user daemon-reload
systemctl --user restart "$BACKEND_SERVICE_NAME" "$FRONTEND_SERVICE_NAME"
echo "Installed and started: $BACKEND_SERVICE_NAME, $FRONTEND_SERVICE_NAME"
