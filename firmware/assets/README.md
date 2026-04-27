# Firmware Assets

This directory is reserved for native firmware assets such as:

- boot logo variants
- icons
- UI bitmaps
- tones or small audio cues

The canonical LCD source images are the 320x240 PNG files in this directory:

- `Logo 320x240.png`
- `Idle.png`
- `Listen.png`
- `Thinking.png`
- `Talk.png`
- `Work.png`
- `Error.png`

These PNG files are source/reference assets only. The firmware does not compile UI pictures into the binary; endpoint UI pictures should be converted to RGB565 and loaded from the SD card.

To convert an image for the SD card:

```bash
python3 firmware/tools/convert_image.py input.png output.rgb565 --format raw-rgb565 --width 320 --height 240 --fit cover
```

To convert an image into an LVGL C descriptor:

```bash
python3 firmware/tools/convert_image.py input.png output_lvgl.c --format lvgl-c --width 320 --height 240 --fit cover --lvgl-version 8
```

Use `--lvgl-version 9` for LVGL 9 projects. The raw and LVGL byte-array formats default to little-endian RGB565 bytes; if colors appear swapped in a target renderer, retry with `--byte-order big`.
