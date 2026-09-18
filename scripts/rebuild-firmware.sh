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
VERBOSE=0
CLEAN_ONLY=0
PROJECT_VERSION="${FIRMWARE_PROJECT_VERSION:-}"
REQUESTED_PROFILES=()

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Rebuild firmware for all available Hexe board profiles.

Options:
  --clean             Use fresh temporary build directories under /tmp.
  --clean-only        Remove generated build directories for selected profiles and exit.
  --include-recovery  Also build recovery firmware for supported S3 profiles.
  --include-minimal   Also build minimal factory/onboarding firmware for supported S3 profiles.
  --minimal-only      Build only minimal factory/onboarding firmware.
  --profile PROFILE   Rebuild only one profile. Can be repeated.
  --project-version V Use an explicit shared firmware version.
  --list              List selected profiles without building.
  --dry-run           Print build commands without running them.
  --verbose           Stream complete ESP-IDF build output instead of writing logs quietly.
  -h, --help          Show this help.

Environment:
  FIRMWARE_PROJECT_VERSION  Shared project version. Generated once by default.
  PYTHON_BIN                Python interpreter. Default: python3.
  HEXE_IDF_INSTALL_ROOT     Parent of versioned ESP-IDF installs. Default: $HOME.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean)
      CLEAN_BUILD=1
      ;;
    --clean-only)
      CLEAN_ONLY=1
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
    --verbose)
      VERBOSE=1
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

profile_required_idf_version() {
  local profile="$1"
  "${PYTHON_BIN}" - "${BOARD_PROFILE_ROOT}" "${profile}" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
profile = sys.argv[2]
sys.path.insert(0, str(root.parent / "tools"))
from validate_board_profiles import load_profile, validate_profile  # noqa: E402

path = root / profile / "board.yaml"
payload = load_profile(path)
validate_profile(payload, path)
print(payload["build"].get("required_idf_version", ""))
PY
}

profile_build_metadata() {
  local profile="$1"
  "${PYTHON_BIN}" - "${BOARD_PROFILE_ROOT}" "${profile}" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
profile = sys.argv[2]
sys.path.insert(0, str(root.parent / "tools"))
from validate_board_profiles import load_profile, validate_profile  # noqa: E402

path = root / profile / "board.yaml"
payload = load_profile(path)
validate_profile(payload, path)
build = payload["build"]
hardware = payload["hardware"]
print("\t".join((
    str(build["idf_target"]),
    str(build["partition_schema"]),
    str(build["app_slot_size"]),
    str(hardware["flash_size"]),
)))
PY
}

profile_idf_export() {
  local profile="$1"
  local required_version
  required_version="$(profile_required_idf_version "${profile}")"
  if [[ -n "${required_version}" ]]; then
    printf '%s/esp-idf-v%s/export.sh\n' "${HEXE_IDF_INSTALL_ROOT:-${HOME}}" "${required_version}"
    return
  fi
  if [[ -n "${IDF_PATH:-}" ]]; then
    printf '%s/export.sh\n' "${IDF_PATH}"
    return
  fi
  printf '%s/esp-idf/export.sh\n' "${HOME}"
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
  local idf_export
  local interactive_progress
  local log_path
  local idf_target
  local partition_schema
  local app_slot_size
  local flash_size
  local started_at
  local terminal_columns
  local warning_count
  shift 2
  idf_export="$(profile_idf_export "${profile}")"
  IFS=$'\t' read -r idf_target partition_schema app_slot_size flash_size \
    < <(profile_build_metadata "${profile}")
  log_path="${BUILD_BASE}/logs/${app}-${profile}.log"
  if [[ ! -f "${idf_export}" ]]; then
    echo "ESP-IDF environment for ${profile} was not found: ${idf_export}" >&2
    return 1
  fi
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
    printf 'cd %q && source %q' "${ROOT_DIR}" "${idf_export}"
    printf ' >/dev/null 2>&1 && env'
    printf ' %q' "${env_args[@]}" "${FIRMWARE_DIR}/build.sh" build
    echo
    return
  fi

  echo
  echo "Building ${app} firmware for ${profile}"
  echo "  SDK: ${idf_export%/export.sh}"
  echo "  Log: ${log_path}"
  started_at="${SECONDS}"
  if [[ "${VERBOSE}" == "1" ]]; then
    (
      cd "${ROOT_DIR}"
      # ESP-IDF's activation banner is redundant with the SDK line above.
      # shellcheck disable=SC1090
      if ! source "${idf_export}" >/dev/null 2>&1; then
        echo "Failed to activate ESP-IDF from ${idf_export}." >&2
        return 1
      fi
      env "${env_args[@]}" "${FIRMWARE_DIR}/build.sh" build
    )
    return
  fi

  mkdir -p "$(dirname "${log_path}")"
  interactive_progress=0
  if [[ -t 1 ]]; then
    interactive_progress=1
  fi
  terminal_columns="${COLUMNS:-120}"
  if [[ -t 1 && -r /dev/tty ]]; then
    terminal_columns="$(stty size </dev/tty 2>/dev/null | awk '{print $2}' || true)"
  fi
  if [[ ! "${terminal_columns}" =~ ^[0-9]+$ || "${terminal_columns}" -lt 40 ]]; then
    terminal_columns=120
  fi
  if ! (
    cd "${ROOT_DIR}"
    # shellcheck disable=SC1090
    if ! source "${idf_export}" >/dev/null 2>&1; then
      echo "Failed to activate ESP-IDF from ${idf_export}." >&2
      return 1
    fi
    env "${env_args[@]}" "${FIRMWARE_DIR}/build.sh" build
  ) 2>&1 | tee "${log_path}" | awk \
    -v app="${app}" \
    -v app_slot_size="${app_slot_size}" \
    -v flash_size="${flash_size}" \
    -v idf_target="${idf_target}" \
    -v interactive="${interactive_progress}" \
    -v partition_schema="${partition_schema}" \
    -v profile="${profile}" \
    -v started_at="$(date +%s)" \
    -v terminal_columns="${terminal_columns}" '
    function stage_for(action) {
      if (action ~ /^Building (C|CXX|ASM) object/) return "Compiling"
      if (action ~ /^Linking/) return "Linking"
      if (action ~ /^Generating/) return "Generating"
      if (action ~ /^(Creating|Packaging|Built target)/) return "Packaging"
      return "Other"
    }
    BEGIN {
      image_status = "pending"
      last_bucket = -1
    }
    function repeat_char(char, count,    result) {
      result = ""
      while (length(result) < count) result = result char
      return result
    }
    function fit(text, width) {
      if (length(text) > width) return substr(text, 1, width - 3) "..."
      return sprintf("%-*s", width, text)
    }
    function panel_border(inner_width) {
      printf "\r\033[2K  +%s+\n", repeat_char("-", inner_width)
    }
    function panel_row(text, inner_width) {
      printf "\r\033[2K  |%s|\n", fit(" " text, inner_width)
    }
    function dashboard(percent, completed, total, stage, action,    bar, bar_width, filled, i, inner_width, summary) {
      inner_width = terminal_columns - 5
      bar_width = terminal_columns >= 100 ? 30 : 16
      filled = int((percent * bar_width) / 100)
      bar = ""
      for (i = 1; i <= bar_width; i++) bar = bar (i <= filled ? "#" : "-")
      if (dashboard_drawn) printf "\033[14A"
      panel_border(inner_width)
      panel_row("HEXE FIRMWARE BUILD", inner_width)
      panel_border(inner_width)
      panel_row(sprintf("BOARD      %s    APP  %s", profile, app), inner_width)
      panel_row(sprintf("TARGET     %s    FLASH  %s    APP SLOT  %s", idf_target, flash_size, app_slot_size), inner_width)
      panel_row("PARTITION  " partition_schema, inner_width)
      panel_border(inner_width)
      panel_row(sprintf("OVERALL    [%s]  %3d%%  %d/%d", bar, percent, completed, total), inner_width)
      summary = sprintf("TASKS      compile %d | link %d | generate %d | package %d | other %d",
        counts["Compiling"], counts["Linking"], counts["Generating"], counts["Packaging"], counts["Other"])
      panel_row(summary, inner_width)
      panel_row("ACTIVITY   " stage, inner_width)
      panel_row("CURRENT    " action, inner_width)
      panel_row("IMAGE      " image_status, inner_width)
      panel_row(sprintf("ELAPSED    %ds", systime() - started_at), inner_width)
      panel_border(inner_width)
      fflush()
      dashboard_drawn = 1
    }
    /^Executing action:/ || /^Running ninja/ || /^-- (Configuring|Generating) done/ {
      if (!dashboard_drawn) {
        print "  " $0
        fflush()
      }
      next
    }
    /^Project build complete/ {
      if (interactive && dashboard_drawn) dashboard(100, total_steps, total_steps, "Complete", "Firmware image ready")
      print "  " $0
      fflush()
      next
    }
    /hexe_firmware\.bin binary size/ {
      image_status = $0
      sub(/^.*hexe_firmware\.bin /, "", image_status)
      if (interactive && dashboard_drawn) dashboard(last_percent, last_completed, total_steps, last_stage, last_action)
      next
    }
    /^\[[0-9]+\/[0-9]+\]/ {
      line = $0
      sub(/^\[/, "", line)
      split(line, fields, "]")
      split(fields[1], step, "/")
      percent = int((step[1] * 100) / step[2])
      action = substr($0, index($0, "]") + 2)
      stage = stage_for(action)
      counts[stage]++
      total_steps = step[2]
      last_action = action
      last_completed = step[1]
      last_percent = percent
      last_stage = stage
      if (interactive) {
        dashboard(percent, step[1], step[2], stage, action)
      } else {
        bucket = int(percent / 5)
        if (bucket > last_bucket || step[1] == step[2]) {
          printf "  Progress: %3d%% (%d/%d) %s\n", percent, step[1], step[2], action
          fflush()
          last_bucket = bucket
        }
      }
    }
  '; then
    echo "Build failed. Last 80 log lines:" >&2
    tail -n 80 "${log_path}" >&2
    echo "Full log: ${log_path}" >&2
    return 1
  fi
  warning_count="$(grep -c 'warning:' "${log_path}" || true)"
  echo "  Complete in $((SECONDS - started_at))s (${warning_count} compiler warnings)."
}

clean_profile_builds() {
  local profile="$1"
  "${PYTHON_BIN}" - "${FIRMWARE_DIR}" "${profile}" "${DRY_RUN}" <<'PY'
import shutil
import sys
from pathlib import Path

firmware_dir = Path(sys.argv[1]).resolve()
profile = sys.argv[2]
dry_run = sys.argv[3] == "1"
names = {
    f"build-{profile}",
    f"build-min-{profile}",
    f"build-recovery-{profile}",
    f"build-audio-probe-{profile}",
}
if profile == "ha_voice_pe":
    names.add("build-ha-voice-pe")
if profile == "esp_box_3":
    names.add("build")
for name in sorted(names):
    path = (firmware_dir / name).resolve()
    if path.parent != firmware_dir or not path.name.startswith("build"):
        raise SystemExit(f"Refusing unsafe build path: {path}")
    if path.exists():
        print(f"{'Would remove' if dry_run else 'Removing'} {path}")
        if not dry_run:
            shutil.rmtree(path)
PY
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

if [[ "${DRY_RUN}" != "1" && "${LIST_ONLY}" != "1" && "${VERBOSE}" != "1" && -t 1 ]]; then
  printf '\033[2J\033[H'
fi

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

if [[ "${CLEAN_ONLY}" == "1" ]]; then
  for profile in "${ENDPOINT_PROFILES[@]}"; do
    clean_profile_builds "${profile}"
  done
  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "Firmware clean dry run complete."
  else
    echo "Firmware build directories cleaned."
  fi
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
