#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Pillow is required to convert images. Install it with "
        "`python -m pip install pillow` or use the ESP-IDF Python environment."
    ) from exc


def parse_rgb(value: str) -> tuple[int, int, int]:
    normalized = value.strip()
    if normalized.startswith("#"):
        normalized = normalized[1:]
    if len(normalized) != 6:
        raise argparse.ArgumentTypeError("color must be in #RRGGBB format")
    try:
        return tuple(int(normalized[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("color must be in #RRGGBB format") from exc


def rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def rgb565_to_bytes(value: int, byte_order: str) -> bytes:
    return value.to_bytes(2, byte_order)


def to_pascal_symbol(stem: str) -> str:
    parts = [part for part in re.split(r"[^a-zA-Z0-9]+", stem) if part]
    if not parts:
        return "Image"
    return "".join(part[:1].upper() + part[1:] for part in parts)


def to_c_symbol(stem: str) -> str:
    parts = [part.lower() for part in re.split(r"[^a-zA-Z0-9]+", stem) if part]
    if not parts:
        return "image"
    symbol = "_".join(parts)
    if symbol[0].isdigit():
        return f"image_{symbol}"
    return symbol


def prepare_image(
    image_path: Path,
    width: int,
    height: int,
    fit: str,
    background: tuple[int, int, int],
    alpha_mode: str = "composite",
) -> Image.Image:
    image = Image.open(image_path).convert("RGBA" if alpha_mode == "composite" else "RGB")
    target_size = (width, height)

    if fit == "stretch":
        image = image.resize(target_size, Image.LANCZOS)
    else:
        source_width, source_height = image.size
        scale = max(width / source_width, height / source_height) if fit == "cover" else min(width / source_width, height / source_height)
        resized_size = (max(1, round(source_width * scale)), max(1, round(source_height * scale)))
        image = image.resize(resized_size, Image.LANCZOS)

        if fit == "cover":
            left = max(0, (resized_size[0] - width) // 2)
            top = max(0, (resized_size[1] - height) // 2)
            image = image.crop((left, top, left + width, top + height))
        else:
            canvas_mode = "RGBA" if alpha_mode == "composite" else "RGB"
            canvas_color = (*background, 255) if alpha_mode == "composite" else background
            canvas = Image.new(canvas_mode, target_size, canvas_color)
            offset = ((width - resized_size[0]) // 2, (height - resized_size[1]) // 2)
            if alpha_mode == "composite":
                canvas.alpha_composite(image, offset)
            else:
                canvas.paste(image, offset)
            image = canvas

    if alpha_mode == "discard":
        return image.convert("RGB")

    background_image = Image.new("RGBA", target_size, (*background, 255))
    background_image.alpha_composite(image)
    return background_image.convert("RGB")


def prepare_rgba_image(
    image_path: Path,
    width: int,
    height: int,
    fit: str,
    alpha_color: tuple[int, int, int] | None = None,
) -> Image.Image:
    image = Image.open(image_path).convert("RGBA")
    if alpha_color is not None:
        image = apply_color_key_alpha(image, alpha_color)
    target_size = (width, height)

    if fit == "stretch":
        return image.resize(target_size, Image.LANCZOS)

    source_width, source_height = image.size
    scale = max(width / source_width, height / source_height) if fit == "cover" else min(width / source_width, height / source_height)
    resized_size = (max(1, round(source_width * scale)), max(1, round(source_height * scale)))
    image = image.resize(resized_size, Image.LANCZOS)

    if fit == "cover":
        left = max(0, (resized_size[0] - width) // 2)
        top = max(0, (resized_size[1] - height) // 2)
        return image.crop((left, top, left + width, top + height))

    canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
    offset = ((width - resized_size[0]) // 2, (height - resized_size[1]) // 2)
    canvas.alpha_composite(image, offset)
    return canvas


def apply_color_key_alpha(image: Image.Image, alpha_color: tuple[int, int, int]) -> Image.Image:
    keyed = image.copy()
    keyed.putdata(
        [
            (r, g, b, 0 if (r, g, b) == alpha_color else a)
            for r, g, b, a in keyed.getdata()
        ]
    )
    return keyed


def image_to_rgb565_pixels(image: Image.Image) -> list[int]:
    width, height = image.size
    pixels: list[int] = []
    for y in range(height):
        for x in range(width):
            r, g, b = image.getpixel((x, y))
            pixels.append(rgb888_to_rgb565(r, g, b))
    return pixels


def write_raw_rgb565(pixels: list[int], output_path: Path, byte_order: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        for pixel in pixels:
            handle.write(rgb565_to_bytes(pixel, byte_order))


def write_alpha_mask(image: Image.Image, output_path: Path, mask_format: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    alpha = image.getchannel("A")
    if mask_format == "alpha8":
        output_path.write_bytes(alpha.tobytes())
        return

    values = list(alpha.getdata())
    packed = bytearray()
    for index in range(0, len(values), 8):
        byte = 0
        for bit, value in enumerate(values[index : index + 8]):
            if value >= 128:
                byte |= 1 << bit
        packed.append(byte)
    output_path.write_bytes(bytes(packed))


def write_cpp_header(pixels: list[int], output_path: Path, width: int, height: int, symbol: str | None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    symbol_prefix = symbol or to_pascal_symbol(output_path.stem.replace("_rgb565", ""))
    guard = re.sub(r"[^A-Z0-9_]", "_", output_path.stem.upper()) + "_H"

    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(f"#ifndef {guard}\n")
        handle.write(f"#define {guard}\n\n")
        handle.write("#include <cstdint>\n\n")
        handle.write("namespace hexe::assets {\n\n")
        handle.write(f"constexpr int k{symbol_prefix}Width = {width};\n")
        handle.write(f"constexpr int k{symbol_prefix}Height = {height};\n")
        handle.write(f"constexpr uint16_t k{symbol_prefix}Rgb565[{len(pixels)}] = {{\n")
        for index in range(0, len(pixels), 12):
            chunk = ", ".join(f"0x{value:04X}" for value in pixels[index : index + 12])
            handle.write(f"  {chunk},\n")
        handle.write("};\n\n")
        handle.write("}  // namespace hexe::assets\n\n")
        handle.write(f"#endif  // {guard}\n")


def write_lvgl_c(
    pixels: list[int],
    output_path: Path,
    width: int,
    height: int,
    symbol: str | None,
    lvgl_version: int,
    byte_order: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_symbol = symbol or to_c_symbol(output_path.stem.replace("_lvgl", ""))
    data_symbol = f"{image_symbol}_map"
    pixel_bytes = b"".join(rgb565_to_bytes(pixel, byte_order) for pixel in pixels)

    with output_path.open("w", encoding="utf-8") as handle:
        handle.write('#include "lvgl.h"\n\n')
        handle.write(f"static const uint8_t {data_symbol}[] = {{\n")
        for index in range(0, len(pixel_bytes), 12):
            chunk = ", ".join(f"0x{value:02X}" for value in pixel_bytes[index : index + 12])
            handle.write(f"  {chunk},\n")
        handle.write("};\n\n")

        if lvgl_version == 8:
            handle.write(f"const lv_img_dsc_t {image_symbol} = {{\n")
            handle.write("  .header.always_zero = 0,\n")
            handle.write(f"  .header.w = {width},\n")
            handle.write(f"  .header.h = {height},\n")
            handle.write("  .header.cf = LV_IMG_CF_TRUE_COLOR,\n")
            handle.write(f"  .data_size = sizeof({data_symbol}),\n")
            handle.write(f"  .data = {data_symbol},\n")
            handle.write("};\n")
        else:
            handle.write(f"const lv_image_dsc_t {image_symbol} = {{\n")
            handle.write("  .header.magic = LV_IMAGE_HEADER_MAGIC,\n")
            handle.write("  .header.cf = LV_COLOR_FORMAT_RGB565,\n")
            handle.write("  .header.flags = 0,\n")
            handle.write(f"  .header.w = {width},\n")
            handle.write(f"  .header.h = {height},\n")
            handle.write(f"  .data_size = sizeof({data_symbol}),\n")
            handle.write(f"  .data = {data_symbol},\n")
            handle.write("};\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert an image to RGB565 firmware, raw, or LVGL formats")
    parser.add_argument("input", type=Path, help="source image, such as PNG or JPEG")
    parser.add_argument("output", type=Path, help="destination file")
    parser.add_argument(
        "--format",
        choices=("cpp-header", "raw-rgb565", "lvgl-c"),
        default="raw-rgb565",
        help="output format; default: raw-rgb565",
    )
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    parser.add_argument(
        "--fit",
        choices=("stretch", "contain", "cover"),
        default="stretch",
        help="resize mode; stretch matches the existing firmware asset pipeline",
    )
    parser.add_argument(
        "--background",
        type=parse_rgb,
        default=(0, 0, 0),
        help="background for transparent pixels or contain mode, as #RRGGBB",
    )
    parser.add_argument(
        "--alpha-mode",
        choices=("composite", "discard"),
        default="composite",
        help="composite transparency over --background or discard alpha like the legacy firmware converter",
    )
    parser.add_argument(
        "--alpha-output",
        type=Path,
        help="optional alpha mask output; when set, RGB565 is written from the PNG RGB plane and alpha is written separately",
    )
    parser.add_argument(
        "--alpha-color",
        type=parse_rgb,
        help="optional #RRGGBB color key to make transparent in --alpha-output masks",
    )
    parser.add_argument(
        "--alpha-mask-format",
        choices=("alpha8", "alpha1"),
        default="alpha8",
        help="alpha mask format for --alpha-output; alpha8 is one byte per pixel, alpha1 packs 8 pixels per byte",
    )
    parser.add_argument(
        "--byte-order",
        choices=("little", "big"),
        default="little",
        help="byte order for raw and LVGL byte arrays; use big if colors appear byte-swapped",
    )
    parser.add_argument("--symbol", help="C/C++ symbol name or prefix; defaults from output filename")
    parser.add_argument("--lvgl-version", type=int, choices=(8, 9), default=8)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.alpha_output is not None:
        rgba_image = prepare_rgba_image(args.input, args.width, args.height, args.fit, args.alpha_color)
        write_alpha_mask(rgba_image, args.alpha_output, args.alpha_mask_format)
        image = rgba_image.convert("RGB")
    else:
        image = prepare_image(args.input, args.width, args.height, args.fit, args.background, args.alpha_mode)
    pixels = image_to_rgb565_pixels(image)

    if args.format == "cpp-header":
        write_cpp_header(pixels, args.output, args.width, args.height, args.symbol)
    elif args.format == "raw-rgb565":
        write_raw_rgb565(pixels, args.output, args.byte_order)
    else:
        write_lvgl_c(pixels, args.output, args.width, args.height, args.symbol, args.lvgl_version, args.byte_order)


if __name__ == "__main__":
    main()
