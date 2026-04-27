#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONVERTER="${SCRIPT_DIR}/convert_image.py"
DEFAULT_SD_DIR="/media/${USER}/Hexe Voice/hexe/sprites"

WIDTH="${WIDTH:-64}"
HEIGHT="${HEIGHT:-64}"
X="${X:-0}"
Y="${Y:-0}"
FIT="${FIT:-contain}"
BYTE_ORDER="${BYTE_ORDER:-little}"
SPRITES_DIR="${HEXE_SPRITES_DIR:-${DEFAULT_SD_DIR}}"
LAYER_JSON_NAME="${LAYER_JSON_NAME:-${MANIFEST_NAME:-}}"
ALPHA_MASK_FORMAT="${ALPHA_MASK_FORMAT:-alpha8}"
ALPHA_COLOR="${ALPHA_COLOR:-#FF00FF}"

find_converter_python() {
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

parse_size() {
  local size="$1"
  if [[ "${size}" =~ ^([0-9]+)x([0-9]+)$ ]]; then
    WIDTH="${BASH_REMATCH[1]}"
    HEIGHT="${BASH_REMATCH[2]}"
    return
  fi
  echo "Invalid size '${size}'. Use WIDTHxHEIGHT, for example 160x160." >&2
  exit 1
}

require_positive_int() {
  local label="$1"
  local value="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ || "${value}" -lt 1 ]]; then
    echo "${label} must be a positive integer." >&2
    exit 1
  fi
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] INPUT_IMAGE [OUTPUT_PATH_OR_DIR]

Converts an image into an RGB565 sprite and writes a layer JSON snippet for ui_manifest.json.

Defaults:
  output dir: ${SPRITES_DIR} when present, otherwise next to INPUT_IMAGE
  size:       ${WIDTH}x${HEIGHT}
  position:   ${X},${Y}
  fit:        ${FIT}
  layer json: ${LAYER_JSON_NAME:-<input-stem>.layer.json}

Environment overrides:
  HEXE_SPRITES_DIR  Output directory when OUTPUT_PATH_OR_DIR is omitted
  WIDTH             Sprite width, default 64
  HEIGHT            Sprite height, default 64
  X                 Overlay x position, default 0
  Y                 Overlay y position, default 0
  FIT               stretch, contain, or cover; default contain
  BYTE_ORDER        little or big; default little
  LAYER_JSON_NAME   Layer JSON filename, default <input-stem>.layer.json
  ALPHA_MASK_FORMAT alpha8 or alpha1; default alpha8
  ALPHA_COLOR       RGB color key treated as transparent, default #FF00FF; set empty to disable
  TRANSPARENT_RGB565 Optional decimal RGB565 color value to skip while drawing
  PYTHON            Python executable to use

Options:
  --size WxH        Sprite/avatar size, for example 160x160
  --width N         Sprite/avatar width
  --height N        Sprite/avatar height
  --x N             Overlay x position
  --y N             Overlay y position
  --fit MODE        stretch, contain, or cover
  -h, --help        Show this help

Examples:
  $(basename "$0") ~/Downloads/badge.png
  $(basename "$0") --size 160x160 ~/Downloads/avatar_idle.png
  $(basename "$0") --width 48 --height 48 --x 260 --y 180 ~/Downloads/settings.png
  WIDTH=48 HEIGHT=48 X=260 Y=180 $(basename "$0") ~/Downloads/badge.png
EOF
}

positionals=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --size)
      [[ $# -ge 2 ]] || { echo "--size requires WIDTHxHEIGHT." >&2; exit 1; }
      parse_size "$2"
      shift 2
      ;;
    --size=*)
      parse_size "${1#*=}"
      shift
      ;;
    --width)
      [[ $# -ge 2 ]] || { echo "--width requires a value." >&2; exit 1; }
      WIDTH="$2"
      shift 2
      ;;
    --width=*)
      WIDTH="${1#*=}"
      shift
      ;;
    --height)
      [[ $# -ge 2 ]] || { echo "--height requires a value." >&2; exit 1; }
      HEIGHT="$2"
      shift 2
      ;;
    --height=*)
      HEIGHT="${1#*=}"
      shift
      ;;
    --x)
      [[ $# -ge 2 ]] || { echo "--x requires a value." >&2; exit 1; }
      X="$2"
      shift 2
      ;;
    --x=*)
      X="${1#*=}"
      shift
      ;;
    --y)
      [[ $# -ge 2 ]] || { echo "--y requires a value." >&2; exit 1; }
      Y="$2"
      shift 2
      ;;
    --y=*)
      Y="${1#*=}"
      shift
      ;;
    --fit)
      [[ $# -ge 2 ]] || { echo "--fit requires a mode." >&2; exit 1; }
      FIT="$2"
      shift 2
      ;;
    --fit=*)
      FIT="${1#*=}"
      shift
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        positionals+=("$1")
        shift
      done
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
    *)
      positionals+=("$1")
      shift
      ;;
  esac
done

require_positive_int "WIDTH" "${WIDTH}"
require_positive_int "HEIGHT" "${HEIGHT}"

if [[ "${FIT}" != "stretch" && "${FIT}" != "contain" && "${FIT}" != "cover" ]]; then
  echo "FIT must be stretch, contain, or cover." >&2
  exit 1
fi

if [[ ${#positionals[@]} -lt 1 || ${#positionals[@]} -gt 2 ]]; then
  usage
  exit 1
fi

input_path="${positionals[0]}"
if [[ ! -f "${input_path}" ]]; then
  echo "Input image not found: ${input_path}" >&2
  exit 1
fi

python_bin="$(find_converter_python)"

input_name="$(basename "${input_path}")"
input_stem="${input_name%.*}"

if [[ ${#positionals[@]} -eq 2 ]]; then
  output_arg="${positionals[1]}"
  if [[ -d "${output_arg}" || "${output_arg}" == */ || "${output_arg}" != *.rgb565 ]]; then
    output_dir="${output_arg%/}"
    output_path="${output_dir}/${input_stem}.rgb565"
  else
    output_path="${output_arg}"
    output_dir="$(dirname "${output_path}")"
  fi
else
  if [[ -d "${SPRITES_DIR}" ]]; then
    output_dir="${SPRITES_DIR}"
  else
    output_dir="$(dirname "${input_path}")"
  fi
  output_path="${output_dir}/${input_stem}.rgb565"
fi

mkdir -p "${output_dir}"
alpha_ext="${ALPHA_MASK_FORMAT}"
alpha_path="${output_path%.rgb565}.${alpha_ext}"
if [[ -z "${LAYER_JSON_NAME}" ]]; then
  LAYER_JSON_NAME="${input_stem}.layer.json"
fi

converter_args=(
  "${python_bin}" "${CONVERTER}" "${input_path}" "${output_path}"
  --format raw-rgb565
  --width "${WIDTH}"
  --height "${HEIGHT}"
  --fit "${FIT}"
  --byte-order "${BYTE_ORDER}"
  --alpha-output "${alpha_path}"
  --alpha-mask-format "${ALPHA_MASK_FORMAT}"
)
if [[ -n "${ALPHA_COLOR}" ]]; then
  converter_args+=(--alpha-color "${ALPHA_COLOR}")
fi
"${converter_args[@]}"

layer_json_path="${output_dir}/${LAYER_JSON_NAME}"
"${python_bin}" - "${output_path}" "${alpha_path}" "${layer_json_path}" "${WIDTH}" "${HEIGHT}" "${X}" "${Y}" "${TRANSPARENT_RGB565:-}" "${ALPHA_MASK_FORMAT}" <<'PY'
import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
alpha_path = Path(sys.argv[2])
layer_json_path = Path(sys.argv[3])
transparent = sys.argv[8].strip()
payload = {
    "filename": output_path.name,
    "alpha": alpha_path.name,
    "alpha_format": sys.argv[9],
    "width": int(sys.argv[4]),
    "height": int(sys.argv[5]),
    "x": int(sys.argv[6]),
    "y": int(sys.argv[7]),
}
if transparent:
    payload["transparent_rgb565"] = int(transparent, 0)
layer_json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

actual_size="$(wc -c < "${output_path}")"
expected_size=$((WIDTH * HEIGHT * 2))

echo "Created: ${output_path}"
echo "Alpha: ${alpha_path}"
echo "Layer JSON: ${layer_json_path}"
echo "Size: ${actual_size} bytes"

if [[ "${actual_size}" -ne "${expected_size}" ]]; then
  echo "Warning: expected ${expected_size} bytes for ${WIDTH}x${HEIGHT} RGB565" >&2
fi

echo "Copy/use on endpoint path: /sdcard/hexe/sprites/$(basename "${output_path}")"
echo "Alpha mask path: /sdcard/hexe/sprites/$(basename "${alpha_path}")"
echo "Add the layer JSON object to /sdcard/hexe/sprites/ui_manifest.json"
