#!/usr/bin/env python3
"""Generate a 1024x600 Hexe shell background for P4 displays."""

from __future__ import annotations

import argparse
import math
import struct
from pathlib import Path


def rgb565(red: int, green: int, blue: int) -> int:
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


def blend(base: tuple[float, float, float], overlay: tuple[int, int, int], alpha: float) -> tuple[float, float, float]:
    alpha = max(0.0, min(1.0, alpha))
    return tuple((channel * (1.0 - alpha)) + (accent * alpha) for channel, accent in zip(base, overlay))


def smooth_rect_alpha(x: float, y: float, left: float, top: float, right: float, bottom: float, softness: float) -> float:
    inside_x = min(x - left, right - x)
    inside_y = min(y - top, bottom - y)
    edge = min(inside_x, inside_y)
    if edge >= softness:
        return 1.0
    if edge <= -softness:
        return 0.0
    return (edge + softness) / (softness * 2.0)


def line_alpha(distance: float, center: float, thickness: float, softness: float = 1.4) -> float:
    edge_distance = abs(distance - center)
    if edge_distance <= thickness:
        return 1.0
    if edge_distance >= thickness + softness:
        return 0.0
    return 1.0 - ((edge_distance - thickness) / softness)


def color_at(x: int, y: int, width: int, height: int) -> int:
    horizontal = x / max(1, width - 1)
    vertical = y / max(1, height - 1)
    rail_width = 104
    header_height = 78
    cx = rail_width + ((width - rail_width) * 0.52)
    edge_distance = min(x, width - 1 - x, y, height - 1 - y)
    vignette = 1.0 - min(1.0, edge_distance / 260.0)
    red = 2 + 8 * horizontal + 4 * vertical
    green = 6 + 13 * horizontal + 8 * vertical
    blue = 14 + 24 * horizontal + 8 * vertical
    base = (red * (1 - 0.46 * vignette), green * (1 - 0.40 * vignette), blue * (1 - 0.28 * vignette))

    cyan = (23, 215, 230)
    deep_cyan = (8, 116, 132)
    purple = (118, 84, 255)
    lavender = (182, 167, 255)
    ice = (215, 255, 255)
    rail = (4, 10, 20)
    panel = (7, 16, 29)

    diagonal_cyan = max(0.0, 1.0 - abs((x - rail_width) - (y * 1.45)) / 520.0)
    diagonal_purple = max(0.0, 1.0 - abs((width - x) - (y * 1.15)) / 560.0)
    color = blend(base, cyan, 0.040 * diagonal_cyan)
    color = blend(color, purple, 0.036 * diagonal_purple)

    grid = 0.0
    if x % 64 in (0, 1) or y % 64 in (0, 1):
        grid = 0.020
    color = blend(color, deep_cyan, grid)

    if x < rail_width:
        color = blend(color, rail, 0.88)
        color = blend(color, cyan, 0.08 * max(0.0, 1.0 - x / rail_width))
    if y < header_height:
        color = blend(color, panel, 0.82)
        color = blend(color, purple, 0.035 * max(0.0, 1.0 - y / header_height))

    color = blend(color, cyan, 0.62 * line_alpha(x, rail_width, 1.2))
    color = blend(color, cyan, 0.40 * line_alpha(y, header_height, 1.2))
    color = blend(color, purple, 0.20 * line_alpha(y, header_height + 4, 0.9))

    for top in (108, 188, 268, 348, 428):
        slot_alpha = smooth_rect_alpha(x, y, 18, top, rail_width - 18, top + 54, 5.0)
        color = blend(color, (10, 25, 42), 0.52 * slot_alpha)
        color = blend(color, cyan, 0.20 * slot_alpha * max(0.0, 1.0 - abs(x - 22) / 4.0))
        color = blend(color, purple, 0.10 * slot_alpha * max(0.0, 1.0 - abs(y - (top + 27)) / 28.0))

    clock_zone = smooth_rect_alpha(x, y, rail_width + 28, 18, rail_width + 286, header_height - 18, 5.0)
    status_zone = smooth_rect_alpha(x, y, width - 284, 18, width - 28, header_height - 18, 5.0)
    color = blend(color, (9, 22, 38), 0.40 * clock_zone)
    color = blend(color, (9, 22, 38), 0.40 * status_zone)

    for marker_x in (width - 252, width - 214, width - 176, width - 138, width - 100, width - 62):
        marker = max(0.0, 1.0 - math.hypot(x - marker_x, y - 39) / 6.0)
        color = blend(color, cyan if marker_x in (width - 252, width - 62) else deep_cyan, 0.38 * marker)

    reserved = smooth_rect_alpha(x, y, rail_width + 64, header_height + 34, width - 42, height - 42, 7.0)
    color = blend(color, (2, 5, 10), 0.08 * reserved)
    color = blend(color, deep_cyan, 0.045 * reserved * max(0.0, 1.0 - abs(x - cx) / 460.0))

    red, green, blue = (int(max(0, min(255, channel))) for channel in color)
    return rgb565(red, green, blue)


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
