#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-9004}"
FRONTEND_PORT="${FRONTEND_PORT:-8084}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
BACKEND_PUBLIC_HOST="${BACKEND_PUBLIC_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
BACKEND_PUBLIC_HOST="${BACKEND_PUBLIC_HOST:-127.0.0.1}"
PUBLIC_API_BASE_URL="${PUBLIC_API_BASE_URL:-http://${BACKEND_PUBLIC_HOST}:${BACKEND_PORT}}"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  local exit_code=$?

  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi

  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi

  wait 2>/dev/null || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  echo "Missing backend virtualenv at $ROOT_DIR/.venv/bin/python"
  echo "Create it with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "Missing frontend dependencies at $ROOT_DIR/frontend/node_modules"
  echo "Install them with: cd frontend && npm install"
  exit 1
fi

echo "Starting HexeVoice backend on http://$BACKEND_HOST:$BACKEND_PORT"
(
  cd "$ROOT_DIR"
  API_HOST="$BACKEND_HOST" API_PORT="$BACKEND_PORT" PUBLIC_API_BASE_URL="$PUBLIC_API_BASE_URL" PYTHONPATH=src .venv/bin/python -m hexevoice.main
) &
BACKEND_PID=$!

echo "Starting HexeVoice frontend on http://$FRONTEND_HOST:$FRONTEND_PORT"
(
  cd "$ROOT_DIR/frontend"
  VITE_PROXY_TARGET="http://127.0.0.1:$BACKEND_PORT" npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

echo
echo "HexeVoice dev stack is starting:"
echo "  API: http://127.0.0.1:$BACKEND_PORT"
echo "  Endpoint API: $PUBLIC_API_BASE_URL"
echo "  UI:  http://127.0.0.1:$FRONTEND_PORT"
echo
echo "Press Ctrl+C to stop both processes."

wait -n "$BACKEND_PID" "$FRONTEND_PID"
