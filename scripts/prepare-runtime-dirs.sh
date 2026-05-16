#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${HEXEVOICE_RUNTIME_DIR:-${RUNTIME_DIR:-$ROOT_DIR/runtime}}"
RUNTIME_DIR_CONFIG="${HEXEVOICE_RUNTIME_DIR_CONFIG:-$ROOT_DIR/config/runtime-dirs.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

runtime_path() {
  local path="$1"
  case "$RUNTIME_DIR" in
    /*) printf '%s/%s\n' "${RUNTIME_DIR%/}" "$path" ;;
    *) printf '%s/%s/%s\n' "$ROOT_DIR" "${RUNTIME_DIR%/}" "$path" ;;
  esac
}

mapfile -t DIRS < <("$PYTHON_BIN" - "$RUNTIME_DIR_CONFIG" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import PurePosixPath

config_path = sys.argv[1]
with open(config_path, encoding="utf-8") as handle:
    payload = json.load(handle)
dirs = payload.get("runtime_dirs")
if not isinstance(dirs, list):
    raise SystemExit("runtime_dirs_missing")
for item in dirs:
    if not isinstance(item, str) or not item.strip():
        raise SystemExit(f"runtime_dir_invalid:{item!r}")
    normalized = str(PurePosixPath(item.strip()))
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        raise SystemExit(f"runtime_dir_unsafe:{item}")
    print(normalized)
PY
)

for dir in "${DIRS[@]}"; do
  mkdir -p "$(runtime_path "$dir")"
done

printf 'Prepared HexeVoice runtime directories under %s\n' "$RUNTIME_DIR"
