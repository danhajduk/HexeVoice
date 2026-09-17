#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

python3 firmware/tools/generate-board-media-assets.py \
  waveshare_p4_wifi6_touch_lcd_7b

"${ROOT_DIR}/scripts/reboot-7b.sh"
