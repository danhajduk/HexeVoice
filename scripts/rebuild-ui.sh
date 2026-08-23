#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
REQUIRED_NODE_RANGE="^20.19.0 || >=22.12.0"

if [[ ! -d "$FRONTEND_DIR" ]]; then
  echo "Missing frontend directory at $FRONTEND_DIR"
  exit 1
fi

cd "$FRONTEND_DIR"

node_runtime_supported() {
  command -v node >/dev/null 2>&1 || return 1
  node - <<'NODE' >/dev/null 2>&1
const [major, minor, patch] = process.versions.node.split(".").map(Number);
const ok =
  (major === 20 && (minor > 19 || (minor === 19 && patch >= 0))) ||
  (major === 22 && (minor > 12 || (minor === 12 && patch >= 0))) ||
  major > 22;
process.exit(ok ? 0 : 1);
NODE
}

if ! node_runtime_supported; then
  echo "Node.js $REQUIRED_NODE_RANGE is required; found $(node --version 2>/dev/null || echo unknown)."
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Missing required command: npm"
  exit 1
fi

if [[ ! -d node_modules ]]; then
  if [[ -f package-lock.json ]]; then
    npm ci
  else
    npm install
  fi
fi

npm run build
