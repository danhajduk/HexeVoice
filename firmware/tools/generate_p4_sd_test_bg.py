#!/usr/bin/env python3
"""Generate a simple 1024x600 RGB565 background for P4 SD-card display tests."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def rgb565(red: int, green: int, blue: int) -> int:
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


def color_at(x: int, y: int, width: int, height: int) -> int:
    horizontal = x / max(1, width - 1)
    vertical = y / max(1, height - 1)
    red = int(18 + 28 * horizontal + 12 * vertical)
    green = int(34 + 128 * vertical)
    blue = int(46 + 146 * horizontal)

    band_center = int(width * 0.34) + int((y - height / 2) * 0.32)
    band_distance = abs(x - band_center)
    if band_distance < 64:
        intensity = 1.0 - (band_distance / 64.0)
        red = int(red + 18 * intensity)
        green = int(green + 118 * intensity)
        blue = int(blue + 96 * intensity)

    if x % 128 == 0 or y % 96 == 0:
        red = min(255, red + 24)
        green = min(255, green + 24)
        blue = min(255, blue + 24)

    return rgb565(min(red, 255), min(green, 255), min(blue, 255))


def generate(output: Path, width: int, height: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        for y in range(height):
            row = bytearray()
            for x in range(width):
                row.extend(struct.pack("<H", color_at(x, y, width, height)))
            handle.write(row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Output bg.rgb565 path, for example /media/$USER/SD/hexe/pictures/bg.rgb565")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=600)
    args = parser.parse_args()
    generate(args.output, args.width, args.height)
    print(f"Wrote {args.output} ({args.width}x{args.height} RGB565)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
