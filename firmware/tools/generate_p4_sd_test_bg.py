#!/usr/bin/env python3
"""Generate a 1024x600 Hexe visual-focus RGB565 background for P4 displays."""

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


def ring_alpha(distance: float, radius: float, thickness: float, softness: float = 1.8) -> float:
    edge_distance = abs(distance - radius)
    if edge_distance >= thickness + softness:
        return 0.0
    if edge_distance <= thickness:
        return 1.0
    return 1.0 - ((edge_distance - thickness) / softness)


def arc_alpha(angle: float, start: float, sweep: float) -> float:
    normalized = (angle - start) % (math.tau)
    if normalized > sweep:
        return 0.0
    edge = min(normalized, sweep - normalized)
    return min(1.0, edge / 0.08)


def color_at(x: int, y: int, width: int, height: int) -> int:
    horizontal = x / max(1, width - 1)
    vertical = y / max(1, height - 1)
    cx = width * 0.52
    cy = height * 0.49
    dx = x - cx
    dy = y - cy
    distance = math.hypot(dx, dy)
    angle = math.atan2(dy, dx)

    vignette = min(1.0, distance / (width * 0.57))
    red = 2 + 8 * horizontal + 4 * vertical
    green = 6 + 13 * horizontal + 8 * vertical
    blue = 14 + 24 * horizontal + 8 * vertical
    base = (red * (1 - 0.46 * vignette), green * (1 - 0.40 * vignette), blue * (1 - 0.28 * vignette))

    cyan = (23, 215, 230)
    deep_cyan = (8, 116, 132)
    purple = (118, 84, 255)
    lavender = (182, 167, 255)
    ice = (215, 255, 255)

    halo = max(0.0, 1.0 - distance / 330.0)
    color = blend(base, cyan, 0.11 * halo * halo)
    color = blend(color, purple, 0.08 * max(0.0, 1.0 - distance / 430.0))

    grid = 0.0
    if x % 64 in (0, 1) or y % 64 in (0, 1):
        grid = 0.035
    color = blend(color, deep_cyan, grid)

    for radius, thickness, accent, alpha in (
        (238, 2.2, cyan, 0.72),
        (202, 1.1, purple, 0.44),
        (156, 1.5, deep_cyan, 0.36),
        (101, 1.2, cyan, 0.30),
        (52, 1.0, purple, 0.32),
    ):
        color = blend(color, accent, alpha * ring_alpha(distance, radius, thickness))

    for radius, thickness, start, sweep, accent, alpha in (
        (176, 3.5, -1.26, 1.22, cyan, 0.86),
        (176, 3.5, 1.88, 1.08, cyan, 0.74),
        (128, 4.0, 2.62, 0.84, lavender, 0.86),
        (128, 4.0, -0.62, 0.78, lavender, 0.78),
        (74, 2.5, 0.88, 1.35, cyan, 0.68),
    ):
        color = blend(color, accent, alpha * ring_alpha(distance, radius, thickness) * arc_alpha(angle, start, sweep))

    for tx, ty, tw, th in (
        (cx, cy - 238, 4, 18),
        (cx, cy + 238, 4, 18),
        (cx - 238, cy, 18, 4),
        (cx + 238, cy, 18, 4),
    ):
        rect_alpha = max(0.0, 1.0 - max(abs(x - tx) / tw, abs(y - ty) / th))
        color = blend(color, ice, 0.62 * rect_alpha)

    for nx, ny, accent in (
        (cx, cy - 255, cyan),
        (cx + 255, cy, cyan),
        (cx, cy + 255, cyan),
        (cx - 255, cy, cyan),
        (cx + 178, cy - 178, purple),
        (cx - 178, cy + 178, purple),
    ):
        node_distance = math.hypot(x - nx, y - ny)
        node_alpha = max(0.0, 1.0 - node_distance / 8.0)
        color = blend(color, accent, 0.82 * node_alpha)

    center_alpha = max(0.0, 1.0 - distance / 12.0)
    color = blend(color, lavender, 0.92 * center_alpha)

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
