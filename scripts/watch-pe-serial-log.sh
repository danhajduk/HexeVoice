#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PORT="/dev/ttyACM0"
BAUD="115200"
LINES="20"
RESET="1"
LOG_FILE=""

find_serial_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf '%s\n' "${PYTHON}"
    return
  fi
  if [[ -n "${IDF_PYTHON_ENV_PATH:-}" && -x "${IDF_PYTHON_ENV_PATH}/bin/python" ]]; then
    printf '%s\n' "${IDF_PYTHON_ENV_PATH}/bin/python"
    return
  fi
  local candidate
  for candidate in "${HOME}"/.espressif/python_env/idf*_py*_env/bin/python; do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done
  printf '%s\n' "python3"
}

usage() {
  cat <<'USAGE'
Usage: scripts/watch-pe-serial-log.sh [options]

Capture the PE serial stream to a raw log file, reset the device, then watch
the last 20 log lines.

Options:
  --port PATH       Serial device path. Default: /dev/ttyACM0
  --baud RATE      Serial baud rate. Default: 115200
  --lines N        Lines shown by watch/tail. Default: 20
  --log-file PATH  Log file path. Default: runtime/logs/pe-serial-<utc>.log
  --no-reset       Start logging without pulsing the device reset line.
  -h, --help       Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="${2:?Missing value for --port}"
      shift 2
      ;;
    --baud)
      BAUD="${2:?Missing value for --baud}"
      shift 2
      ;;
    --lines)
      LINES="${2:?Missing value for --lines}"
      shift 2
      ;;
    --log-file)
      LOG_FILE="${2:?Missing value for --log-file}"
      shift 2
      ;;
    --no-reset)
      RESET="0"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "${BAUD}" =~ ^[0-9]+$ ]]; then
  echo "Invalid baud rate: ${BAUD}" >&2
  exit 2
fi

if ! [[ "${LINES}" =~ ^[0-9]+$ ]] || [[ "${LINES}" -lt 1 ]]; then
  echo "Invalid line count: ${LINES}" >&2
  exit 2
fi

if [[ -z "${LOG_FILE}" ]]; then
  LOG_DIR="${REPO_ROOT}/runtime/logs"
  mkdir -p "${LOG_DIR}"
  LOG_FILE="${LOG_DIR}/pe-serial-$(date -u +%Y%m%dT%H%M%SZ).log"
else
  mkdir -p "$(dirname "${LOG_FILE}")"
fi

touch "${LOG_FILE}"

SERIAL_PYTHON="$(find_serial_python)"
if ! command -v "${SERIAL_PYTHON}" >/dev/null 2>&1 && [[ ! -x "${SERIAL_PYTHON}" ]]; then
  echo "Missing Python interpreter: ${SERIAL_PYTHON}" >&2
  exit 1
fi

if ! "${SERIAL_PYTHON}" -c "import serial" >/dev/null 2>&1; then
  echo "Missing pyserial for ${SERIAL_PYTHON}." >&2
  echo "Install python3-serial, set PYTHON to an env with pyserial, or source ESP-IDF first." >&2
  exit 1
fi

echo "Capturing ${PORT} at ${BAUD} baud"
echo "Log file: ${LOG_FILE}"
echo "Python: ${SERIAL_PYTHON}"

"${SERIAL_PYTHON}" - "${PORT}" "${BAUD}" "${LOG_FILE}" "${RESET}" <<'PY' &
import signal
import sys
import time
from datetime import datetime, timezone

import serial

port = sys.argv[1]
baud = int(sys.argv[2])
log_path = sys.argv[3]
should_reset = sys.argv[4] == "1"
running = True


def stop(_signum, _frame):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def marker(text):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"\n--- {stamp} {text} ---\n".encode("utf-8")


with serial.Serial(port=port, baudrate=baud, timeout=0.2, exclusive=True) as ser:
    with open(log_path, "ab", buffering=0) as log:
        log.write(marker(f"hexe PE serial capture started port={port} baud={baud}"))
        if should_reset:
            log.write(marker("pulsing reset line"))
            ser.dtr = False
            ser.rts = True
            time.sleep(0.12)
            ser.rts = False
            time.sleep(0.25)
            log.write(marker("reset pulse complete"))

        while running:
            data = ser.read(4096)
            if data:
                log.write(data)

        log.write(marker("hexe PE serial capture stopped"))
PY

LOGGER_PID="$!"

cleanup() {
  kill "${LOGGER_PID}" >/dev/null 2>&1 || true
  wait "${LOGGER_PID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

sleep 1
if ! kill -0 "${LOGGER_PID}" >/dev/null 2>&1; then
  wait "${LOGGER_PID}"
fi

if command -v watch >/dev/null 2>&1; then
  WATCH_CMD="$(printf 'tail -n %q %q' "${LINES}" "${LOG_FILE}")"
  watch -n 1 "${WATCH_CMD}"
else
  echo "Missing watch; falling back to tail refresh loop." >&2
  while true; do
    clear
    tail -n "${LINES}" "${LOG_FILE}"
    sleep 1
  done
fi
