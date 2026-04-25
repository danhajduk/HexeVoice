#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Pillow is required to convert Logo.png. Install it with "
        "`python -m pip install pillow` or use the repo helper environment."
    ) from exc


def rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def stem_to_symbol_prefix(stem: str) -> str:
    parts = [part for part in re.split(r"[^a-zA-Z0-9]+", stem) if part]
    if not parts:
        return "Image"
    return "".join(part[:1].upper() + part[1:] for part in parts)


def write_header(image_path: Path, output_path: Path, width: int, height: int) -> None:
    img = Image.open(image_path).convert("RGB").resize((width, height), Image.LANCZOS)
    symbol_prefix = stem_to_symbol_prefix(output_path.stem.replace("_rgb565", ""))

    pixels: list[int] = []

    for y in range(height):
        for x in range(width):
            r, g, b = img.getpixel((x, y))
            pixels.append(rgb888_to_rgb565(r, g, b))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        guard = output_path.stem.upper() + "_H"
        f.write(f"#ifndef {guard}\n")
        f.write(f"#define {guard}\n\n")
        f.write("#include <cstdint>\n\n")
        f.write("namespace hexe::assets {\n\n")
        f.write(f"constexpr int k{symbol_prefix}Width = {width};\n")
        f.write(f"constexpr int k{symbol_prefix}Height = {height};\n")
        f.write(f"constexpr uint16_t k{symbol_prefix}Rgb565[{len(pixels)}] = {{\n")
        for i in range(0, len(pixels), 12):
            chunk = ", ".join(f"0x{value:04X}" for value in pixels[i : i + 12])
            f.write(f"  {chunk},\n")
        f.write("};\n\n")
        f.write("}  // namespace hexe::assets\n\n")
        f.write(f"#endif  // {guard}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a PNG into an RGB565 C++ header")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    args = parser.parse_args()

    write_header(args.input, args.output, args.width, args.height)


if __name__ == "__main__":
    main()
