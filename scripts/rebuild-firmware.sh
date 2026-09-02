#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIRMWARE_DIR="${ROOT_DIR}/firmware"
BOARD_PROFILE_ROOT="${FIRMWARE_DIR}/boards"
PYTHON_BIN="${PYTHON_BIN:-python3}"

CLEAN_BUILD=0
DRY_RUN=0
INCLUDE_RECOVERY=0
INCLUDE_MINIMAL=0
MINIMAL_ONLY=0
LIST_ONLY=0
PROJECT_VERSION="${FIRMWARE_PROJECT_VERSION:-}"
REQUESTED_PROFILES=()

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Rebuild firmware for all available Hexe board profiles.

Options:
  --clean             Use fresh temporary build directories under /tmp.
  --include-recovery  Also build recovery firmware for supported S3 profiles.
  --include-minimal   Also build minimal factory/onboarding firmware for supported S3 profiles.
  --minimal-only      Build only minimal factory/onboarding firmware.
  --profile PROFILE   Rebuild only one profile. Can be repeated.
  --project-version V Use an explicit shared firmware version.
  --list              List selected profiles without building.
  --dry-run           Print build commands without running them.
  -h, --help          Show this help.

Environment:
  FIRMWARE_PROJECT_VERSION  Shared project version. Generated once by default.
  PYTHON_BIN                Python interpreter. Default: python3.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean)
      CLEAN_BUILD=1
      ;;
    --include-recovery)
      INCLUDE_RECOVERY=1
      ;;
    --include-minimal)
      INCLUDE_MINIMAL=1
      ;;
    --minimal-only)
      INCLUDE_MINIMAL=1
      MINIMAL_ONLY=1
      ;;
    --profile)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "--profile requires a value." >&2
        exit 2
      fi
      REQUESTED_PROFILES+=("$2")
      shift
      ;;
    --project-version)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "--project-version requires a value." >&2
        exit 2
      fi
      PROJECT_VERSION="$2"
      shift
      ;;
    --list)
      LIST_ONLY=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

discover_profiles() {
  local mode="$1"
  shift
  "${PYTHON_BIN}" - "${BOARD_PROFILE_ROOT}" "${mode}" "$@" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
mode = sys.argv[2]
requested = sys.argv[3:]

sys.path.insert(0, str(root.parent / "tools"))
from validate_board_profiles import discover_profiles, load_profile, validate_profile  # noqa: E402

if requested:
    paths = []
    for profile in requested:
        path = root / profile / "board.yaml"
        if not path.exists():
            raise SystemExit(f"Unknown board profile: {profile}")
        paths.append(path)
else:
    paths = discover_profiles(root)

selected = []
for path in paths:
    payload = load_profile(path)
    validate_profile(payload, path)
    profile = payload["board_profile"]
    build = payload["build"]
    if mode == "endpoint":
        if payload["adapters"]["buildable"]:
            selected.append(profile)
    elif mode == "recovery":
        if build.get("recovery_app") is True and build.get("idf_target") == "esp32s3":
            selected.append(profile)
    else:
        raise SystemExit(f"Unsupported discovery mode: {mode}")

if selected:
    sys.stdout.write("\n".join(selected))
    sys.stdout.write("\n")
PY
}

generated_project_version() {
  local git_sha
  git_sha="$(git -C "${ROOT_DIR}" rev-parse --short HEAD 2>/dev/null || echo nogit)"
  printf 'z%s-%s\n' "$(date -u +"%Y%m%d%H%M%S")" "${git_sha}"
}

print_profiles() {
  local title="$1"
  shift
  echo "${title}"
  if [[ $# -eq 0 ]]; then
    echo "  none"
    return
  fi
  local profile
  for profile in "$@"; do
    echo "  ${profile}"
  done
}

run_build() {
  local app="$1"
  local profile="$2"
  shift 2
  local -a env_args=(
    "FIRMWARE_PROJECT_VERSION=${PROJECT_VERSION}"
    "HEXE_FIRMWARE_APP=${app}"
    "HEXE_BOARD_PROFILE=${profile}"
  )
  if [[ "${CLEAN_BUILD}" == "1" ]]; then
    env_args+=("BUILD_DIR=${BUILD_BASE}/${app}-${profile}")
  fi
  if [[ "${app}" == "recovery" ]]; then
    env_args+=("RUNTIME_FIRMWARE_DIR=${BUILD_BASE}/recovery-runtime-artifacts")
  fi
  env_args+=("$@")

  if [[ "${DRY_RUN}" == "1" ]]; then
    printf 'cd %q && env' "${ROOT_DIR}"
    printf ' %q' "${env_args[@]}" "${FIRMWARE_DIR}/build.sh" build
    echo
    return
  fi

  echo
  echo "Building ${app} firmware for ${profile}"
  (
    cd "${ROOT_DIR}"
    env "${env_args[@]}" "${FIRMWARE_DIR}/build.sh" build
  )
}

mapfile -t ENDPOINT_PROFILES < <(discover_profiles endpoint "${REQUESTED_PROFILES[@]}")
mapfile -t RECOVERY_PROFILES < <(discover_profiles recovery "${REQUESTED_PROFILES[@]}")
mapfile -t MINIMAL_PROFILES < <(discover_profiles recovery "${REQUESTED_PROFILES[@]}")

if [[ "${MINIMAL_ONLY}" != "1" && "${#ENDPOINT_PROFILES[@]}" -eq 0 ]]; then
  echo "No buildable endpoint board profiles selected." >&2
  exit 1
fi
if [[ "${INCLUDE_MINIMAL}" == "1" && "${#MINIMAL_PROFILES[@]}" -eq 0 ]]; then
  echo "No minimal-capable S3 board profiles selected." >&2
  exit 1
fi

if [[ -z "${PROJECT_VERSION}" ]]; then
  PROJECT_VERSION="$(generated_project_version)"
fi

BUILD_BASE="/tmp/hexevoice-fw-build-${PROJECT_VERSION}"

if [[ "${MINIMAL_ONLY}" != "1" ]]; then
  print_profiles "Endpoint firmware profiles:" "${ENDPOINT_PROFILES[@]}"
fi
if [[ "${INCLUDE_RECOVERY}" == "1" ]]; then
  print_profiles "Recovery firmware profiles:" "${RECOVERY_PROFILES[@]}"
fi
if [[ "${INCLUDE_MINIMAL}" == "1" ]]; then
  print_profiles "Minimal firmware profiles:" "${MINIMAL_PROFILES[@]}"
fi
echo "Firmware version: ${PROJECT_VERSION}"
if [[ "${CLEAN_BUILD}" == "1" ]]; then
  echo "Clean build base: ${BUILD_BASE}"
fi

if [[ "${LIST_ONLY}" == "1" ]]; then
  exit 0
fi

if [[ "${MINIMAL_ONLY}" != "1" ]]; then
  for profile in "${ENDPOINT_PROFILES[@]}"; do
    run_build endpoint "${profile}"
  done
fi

if [[ "${INCLUDE_RECOVERY}" == "1" ]]; then
  for profile in "${RECOVERY_PROFILES[@]}"; do
    run_build recovery "${profile}"
  done
fi

if [[ "${INCLUDE_MINIMAL}" == "1" ]]; then
  for profile in "${MINIMAL_PROFILES[@]}"; do
    run_build minimal "${profile}" "RUNTIME_FIRMWARE_DIR=${BUILD_BASE}/minimal-runtime-artifacts"
  done
fi

echo
if [[ "${DRY_RUN}" == "1" ]]; then
  echo "Firmware rebuild dry run complete."
else
  echo "Firmware rebuild complete."
fi
