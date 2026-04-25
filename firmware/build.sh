#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${IDF_TARGET:-esp32s3}"
EXPORT_AFTER_BUILD="${EXPORT_AFTER_BUILD:-1}"
CONVERTER_PYTHON="python3"

if [[ -z "${IDF_PATH:-}" ]]; then
  if [[ -f "${HOME}/esp-idf/export.sh" ]]; then
    # shellcheck disable=SC1090
    . "${HOME}/esp-idf/export.sh"
  else
    echo "ESP-IDF environment is not loaded and ${HOME}/esp-idf/export.sh was not found." >&2
    echo "Run '. ~/esp-idf/export.sh' first, or install ESP-IDF under ~/esp-idf." >&2
    exit 1
  fi
fi

cd "${ROOT_DIR}"

LOGO_SOURCE="${ROOT_DIR}/assets/Logo 320x240.png"
LOGO_HEADER="${ROOT_DIR}/main/assets/logo_rgb565.h"
IDLE_SOURCE="${ROOT_DIR}/assets/idle-320x240.png"
IDLE_HEADER="${ROOT_DIR}/main/assets/idle_rgb565.h"
LISTENING_SOURCE="${ROOT_DIR}/assets/listening- 320x240.png"
LISTENING_HEADER="${ROOT_DIR}/main/assets/listening_rgb565.h"
THINKING_SOURCE="${ROOT_DIR}/assets/thinking-320x240.png"
THINKING_HEADER="${ROOT_DIR}/main/assets/thinking_rgb565.h"
ERROR_SOURCE="${ROOT_DIR}/assets/error-320x240.png"
ERROR_HEADER="${ROOT_DIR}/main/assets/error_rgb565.h"

if [[ -x "${HOME}/.espressif/python_env/idf6.1_py3.11_env/bin/python" ]]; then
  CONVERTER_PYTHON="${HOME}/.espressif/python_env/idf6.1_py3.11_env/bin/python"
fi

if [[ -f "${LOGO_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_logo.py" "${LOGO_SOURCE}" "${LOGO_HEADER}" --width 320 --height 240
fi

if [[ -f "${IDLE_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_logo.py" "${IDLE_SOURCE}" "${IDLE_HEADER}" --width 320 --height 240
fi

if [[ -f "${LISTENING_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_logo.py" "${LISTENING_SOURCE}" "${LISTENING_HEADER}" --width 320 --height 240
fi

if [[ -f "${THINKING_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_logo.py" "${THINKING_SOURCE}" "${THINKING_HEADER}" --width 320 --height 240
fi

if [[ -f "${ERROR_SOURCE}" ]]; then
  "${CONVERTER_PYTHON}" "${ROOT_DIR}/tools/convert_logo.py" "${ERROR_SOURCE}" "${ERROR_HEADER}" --width 320 --height 240
fi

if [[ ! -f "${ROOT_DIR}/sdkconfig" ]]; then
  idf.py set-target "${TARGET}"
fi

idf.py build

if [[ "${EXPORT_AFTER_BUILD}" == "1" ]]; then
  "${ROOT_DIR}/export-artifacts.sh"
fi

echo "Firmware build complete."
