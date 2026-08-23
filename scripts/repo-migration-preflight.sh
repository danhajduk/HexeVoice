#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_DIR="${HEXEVOICE_PREFLIGHT_VENV:-$ROOT_DIR/.venv}"
PYTHON_BIN="${HEXEVOICE_PREFLIGHT_PYTHON:-python3}"
LOG_ROOT="${HEXEVOICE_PREFLIGHT_LOG_ROOT:-$ROOT_DIR/runtime/preflight}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="$LOG_ROOT/repo-migration-$RUN_ID"
REQUIRED_NODE_RANGE="^20.19.0 || >=22.12.0"
SKIP_PYTHON_INSTALL=false
SKIP_FRONTEND_INSTALL=false
FAILED=0
DEV_AUDIT_STATUS="not-run"
REQUIRED_FAILURES=()

usage() {
  cat <<USAGE
Usage: $0 [--skip-python-install] [--skip-frontend-install]

Runs HexeVoice repo move-readiness checks from one command:
  - Python dependency install unless skipped
  - full backend pytest suite
  - frontend dependency install unless skipped
  - frontend production build
  - required production npm audit: npm audit --omit=dev
  - non-blocking full dev audit review: npm audit

Logs are written to:
  $LOG_DIR

Environment:
  HEXEVOICE_PREFLIGHT_PYTHON       Python executable for creating .venv (default: python3)
  HEXEVOICE_PREFLIGHT_VENV         Virtualenv path (default: .venv)
  HEXEVOICE_PREFLIGHT_LOG_ROOT     Log root (default: runtime/preflight)
  HEXEVOICE_PREFLIGHT_PYTEST_ARGS  Extra pytest args (default: -q)
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-python-install)
      SKIP_PYTHON_INSTALL=true
      shift
      ;;
    --skip-frontend-install)
      SKIP_FRONTEND_INSTALL=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$LOG_DIR"

log() {
  printf '[repo-preflight] %s\n' "$*"
}

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

show_failure_tail() {
  local log_file="$1"
  if [[ -s "$log_file" ]]; then
    printf '  Last log lines from %s:\n' "$log_file" >&2
    tail -n 20 "$log_file" >&2
  fi
}

run_required() {
  local label="$1"
  local log_file="$LOG_DIR/$2"
  shift 2
  log "RUN required: $label"
  if "$@" >"$log_file" 2>&1; then
    log "PASS: $label"
    return 0
  fi
  local code=$?
  log "FAIL: $label (exit $code, log: $log_file)"
  REQUIRED_FAILURES+=("$label -> $log_file")
  FAILED=1
  show_failure_tail "$log_file"
  return 0
}

run_dev_audit() {
  local log_file="$LOG_DIR/npm-audit-dev.log"
  log "RUN review: full npm audit including dev dependencies"
  if (cd "$FRONTEND_DIR" && npm audit) >"$log_file" 2>&1; then
    DEV_AUDIT_STATUS="clean"
    log "PASS: full npm audit including dev dependencies"
    return 0
  fi
  local code=$?
  DEV_AUDIT_STATUS="review-needed"
  log "REVIEW: full npm audit reported advisories (exit $code, log: $log_file)"
  show_failure_tail "$log_file"
  return 0
}

write_versions() {
  local log_file="$LOG_DIR/tool-versions.log"
  {
    printf 'Root: %s\n' "$ROOT_DIR"
    printf 'Python launcher: '
    command -v "$PYTHON_BIN" || true
    "$PYTHON_BIN" --version || true
    printf 'Virtualenv Python: '
    "$VENV_DIR/bin/python" --version || true
    "$VENV_DIR/bin/python" -m pip --version || true
    "$VENV_DIR/bin/pytest" --version || true
    printf 'Node: '
    node --version || true
    printf 'npm: '
    npm --version || true
    printf 'Vite: '
    (cd "$FRONTEND_DIR" && npm exec vite -- --version) || true
  } | tee "$log_file"
}

log "Starting repo migration preflight"
log "Logs: $LOG_DIR"

run_required "Node.js $REQUIRED_NODE_RANGE is available" "node-version-check.log" bash -c '
  required="$1"
  if ! command -v node >/dev/null 2>&1; then
    echo "Missing node; install Node.js $required."
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "Missing npm; install Node.js/npm $required."
    exit 1
  fi
  node - <<'"'"'NODE'"'"'
const [major, minor, patch] = process.versions.node.split(".").map(Number);
const ok =
  (major === 20 && (minor > 19 || (minor === 19 && patch >= 0))) ||
  (major === 22 && (minor > 12 || (minor === 12 && patch >= 0))) ||
  major > 22;
if (!ok) {
  console.error(`Unsupported Node.js ${process.version}`);
  process.exit(1);
}
NODE
' bash "$REQUIRED_NODE_RANGE"

if [[ "$SKIP_PYTHON_INSTALL" == "false" ]]; then
  run_required "create Python virtualenv if missing" "python-venv.log" bash -c '
    venv_dir="$1"
    python_bin="$2"
    if [[ ! -x "$venv_dir/bin/python" ]]; then
      "$python_bin" -m venv "$venv_dir"
    fi
  ' bash "$VENV_DIR" "$PYTHON_BIN"
  run_required "install Python dependencies" "python-deps.log" "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
fi

if [[ "$SKIP_FRONTEND_INSTALL" == "false" ]]; then
  if [[ -f "$FRONTEND_DIR/package-lock.json" ]]; then
    run_required "install frontend dependencies with npm ci" "frontend-install.log" bash -c 'cd "$1" && npm ci' bash "$FRONTEND_DIR"
  else
    run_required "install frontend dependencies with npm install" "frontend-install.log" bash -c 'cd "$1" && npm install' bash "$FRONTEND_DIR"
  fi
fi

write_versions

PYTEST_ARGS_STRING="${HEXEVOICE_PREFLIGHT_PYTEST_ARGS:--q}"
# shellcheck disable=SC2206
PYTEST_ARGS=( $PYTEST_ARGS_STRING )
run_required "backend pytest suite" "pytest.log" env \
  "PYTHONPATH=$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$VENV_DIR/bin/pytest" "${PYTEST_ARGS[@]}"

run_required "frontend production build" "frontend-build.log" bash -c 'cd "$1" && npm run build' bash "$FRONTEND_DIR"
run_required "production npm audit" "npm-audit-production.log" bash -c 'cd "$1" && npm audit --omit=dev' bash "$FRONTEND_DIR"
run_dev_audit

printf '\nRepo migration preflight summary\n'
printf '  Logs: %s\n' "$LOG_DIR"
printf '  Dev audit: %s\n' "$DEV_AUDIT_STATUS"

if [[ "$FAILED" -ne 0 ]]; then
  printf '  Required checks: failed\n' >&2
  printf '  Failures:\n' >&2
  for failure in "${REQUIRED_FAILURES[@]}"; do
    printf '    - %s\n' "$failure" >&2
  done
  exit 1
fi

printf '  Required checks: passed\n'
if [[ "$DEV_AUDIT_STATUS" == "review-needed" ]]; then
  printf '  Full dev audit needs review; required production checks still passed.\n'
fi
