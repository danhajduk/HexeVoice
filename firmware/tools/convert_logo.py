#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path

from convert_image import image_to_rgb565_pixels, prepare_image, write_cpp_header


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a PNG into an RGB565 C++ header")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    args = parser.parse_args()

    image = prepare_image(args.input, args.width, args.height, "stretch", (0, 0, 0), "discard")
    write_cpp_header(image_to_rgb565_pixels(image), args.output, args.width, args.height, None)


if __name__ == "__main__":
    main()
