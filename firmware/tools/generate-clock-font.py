#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import struct

from PIL import Image, ImageDraw, ImageFont


GLYPHS = "0123456789:"
HEADER = struct.Struct("<4sHhH")
RECORD = struct.Struct("<BhhhHHI")


def main() -> int:
    parser = argparse.ArgumentParser(description="Rasterize clock glyphs into a compact Hexe SD font.")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pixel-size", type=int, default=42)
    args = parser.parse_args()

    font = ImageFont.truetype(args.source, args.pixel_size)
    ascent, _ = font.getmetrics()
    records: list[tuple[int, int, int, int, int, int, int]] = []
    bitmaps: list[bytes] = []
    offset = HEADER.size + (len(GLYPHS) * RECORD.size)
    for character in GLYPHS:
        left, top, right, bottom = font.getbbox(character, anchor="ls")
        width = max(0, right - left)
        height = max(0, bottom - top)
        image = Image.new("L", (width, height))
        ImageDraw.Draw(image).text((-left, -top), character, font=font, fill=255, anchor="ls")
        bitmap = image.tobytes()
        records.append(
            (ord(character), left, -top, round(font.getlength(character)), width, height, offset)
        )
        bitmaps.append(bitmap)
        offset += len(bitmap)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as handle:
        handle.write(HEADER.pack(b"HXF1", args.pixel_size, ascent, len(records)))
        for record in records:
            handle.write(RECORD.pack(*record))
        for bitmap in bitmaps:
            handle.write(bitmap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
