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

`firmware/build.sh` converts these PNG files into RGB565 headers under `firmware/main/assets/` before building the firmware.
