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

These PNG files are source/reference assets only. The firmware does not compile UI pictures into the binary; endpoint UI pictures are converted to the board's native raw RGB format and loaded from the SD card.

To convert one image for the SD card:

```bash
python3 firmware/tools/convert_image.py input.png output.rgb565 --format raw-rgb565 --width 320 --height 240 --fit cover
```

To convert all source images for a board and regenerate its endpoint asset
manifest, place full-screen picture PNGs directly in
`firmware/assets/<board_profile>/` and sprite PNGs in
`firmware/assets/<board_profile>/sprites/`, then run:

```bash
python3 firmware/tools/generate-board-media-assets.py <board_profile>
```

The board media generator keeps each PNG's native dimensions unless `--width`
and `--height` are provided, writes RGB888 files for the P4 7-inch board and
RGB565 files for other boards under the board's `assets/picture/` and
`assets/sprite/` folders, then regenerates `assets.json`.

For the Waveshare P4 7-inch display, place a 1024x600 packed RGB888 file at
`/sdcard/hexe/pictures/bg.rgb888`. Generate it and the board manifest from the
source PNG with:

```bash
python3 firmware/tools/generate-board-media-assets.py waveshare_p4_wifi6_touch_lcd_7b
```

To convert an image into an LVGL C descriptor:

```bash
python3 firmware/tools/convert_image.py input.png output_lvgl.c --format lvgl-c --width 320 --height 240 --fit cover --lvgl-version 8
```

Use `--lvgl-version 9` for LVGL 9 projects. RGB565 raw and LVGL byte-array formats default to little-endian pixels; if colors appear swapped in a target renderer, retry with `--byte-order big`. Raw RGB888 assets use packed red, green, blue byte order.
