#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CONVERTER="${SCRIPT_DIR}/convert_image.py"
DEFAULT_SD_DIR="/media/${USER}/Hexe Voice/hexe/pictures"

WIDTH="${WIDTH:-320}"
HEIGHT="${HEIGHT:-240}"
FIT="${FIT:-cover}"
BYTE_ORDER="${BYTE_ORDER:-little}"
PICTURES_DIR="${HEXE_PICTURES_DIR:-${DEFAULT_SD_DIR}}"

usage() {
  cat <<EOF
Usage: $(basename "$0") INPUT_IMAGE [OUTPUT_PATH_OR_DIR]

Converts a PNG/JPEG/etc. into raw RGB565 for the Hexe endpoint display.

Defaults:
  output dir: ${PICTURES_DIR} when present, otherwise next to INPUT_IMAGE
  size:       ${WIDTH}x${HEIGHT}
  fit:        ${FIT}

Environment overrides:
  HEXE_PICTURES_DIR  Output directory when OUTPUT_PATH_OR_DIR is omitted
  WIDTH              Output width, default 320
  HEIGHT             Output height, default 240
  FIT                stretch, contain, or cover; default cover
  BYTE_ORDER         little or big; default little
  PYTHON             Python executable to use

Examples:
  $(basename "$0") ~/Downloads/photo.png
  $(basename "$0") ~/Downloads/photo.png "/media/${USER}/Hexe Voice/hexe/pictures"
  FIT=contain $(basename "$0") ~/Downloads/photo.png
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit $([[ $# -lt 1 || $# -gt 2 ]] && echo 1 || echo 0)
fi

input_path="$1"
if [[ ! -f "${input_path}" ]]; then
  echo "Input image not found: ${input_path}" >&2
  exit 1
fi

if [[ -n "${PYTHON:-}" ]]; then
  python_bin="${PYTHON}"
elif [[ -x "${HOME}/.espressif/python_env/idf6.1_py3.11_env/bin/python" ]]; then
  python_bin="${HOME}/.espressif/python_env/idf6.1_py3.11_env/bin/python"
else
  python_bin="python3"
fi

input_name="$(basename "${input_path}")"
input_stem="${input_name%.*}"

if [[ $# -eq 2 ]]; then
  output_arg="$2"
  if [[ -d "${output_arg}" || "${output_arg}" == */ || "${output_arg}" != *.rgb565 ]]; then
    output_dir="${output_arg%/}"
    output_path="${output_dir}/${input_stem}.rgb565"
  else
    output_path="${output_arg}"
    output_dir="$(dirname "${output_path}")"
  fi
else
  if [[ -d "${PICTURES_DIR}" ]]; then
    output_dir="${PICTURES_DIR}"
  else
    output_dir="$(dirname "${input_path}")"
  fi
  output_path="${output_dir}/${input_stem}.rgb565"
fi

mkdir -p "${output_dir}"

"${python_bin}" "${CONVERTER}" "${input_path}" "${output_path}" \
  --format raw-rgb565 \
  --width "${WIDTH}" \
  --height "${HEIGHT}" \
  --fit "${FIT}" \
  --byte-order "${BYTE_ORDER}"

actual_size="$(wc -c < "${output_path}")"
expected_size=$((WIDTH * HEIGHT * 2))

echo "Created: ${output_path}"
echo "Size: ${actual_size} bytes"

if [[ "${actual_size}" -ne "${expected_size}" ]]; then
  echo "Warning: expected ${expected_size} bytes for ${WIDTH}x${HEIGHT} RGB565" >&2
fi

echo "Copy/use on endpoint path: /sdcard/hexe/pictures/$(basename "${output_path}")"
