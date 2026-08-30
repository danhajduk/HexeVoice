# Firmware New-Board Bring-Up

Status: Task 268 implementation guide

## Purpose

New firmware boards start as board-profile YAML, not as hardcoded C++ pin
tables. The profile captures what is known, keeps incomplete pin maps explicit,
and prevents accidental builds until the board wiring and adapters are ready.

## Bring-Up Levels

| Level | Profile State | Meaning |
| --- | --- | --- |
| planned | `support_status: planned`, `wiring.status: partial`, `adapters.buildable: false` | Basic board specs are known, but schematic/dev-board wiring is not complete. |
| pinmapped | `wiring.status: complete`, `adapters.buildable: false` | GPIO/I2C/I2S/SPI/device-address mappings are complete, but firmware adapters still need implementation or selection. |
| buildable | `wiring.status: complete`, `adapters.buildable: true`, non-empty `adapters.source_files` | The selected adapters exist, validate, and compile for the board. |

## Scaffold A Profile

Use `firmware/tools/create_board_profile.py` to create a valid planned profile:

```bash
python firmware/tools/create_board_profile.py \
  --profile waveshare_s3_touch_lcd_1_85c_box_v2 \
  --display-name "Waveshare ESP32-S3-Touch-LCD-1.85C-BOX V2" \
  --vendor Waveshare \
  --model ESP32-S3-Touch-LCD-1.85C-BOX \
  --source-url https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85C \
  --with-display \
  --with-touch \
  --with-sd-card \
  --with-battery \
  --display-size-inches 1.85 \
  --display-shape round \
  --display-width-px 360 \
  --display-height-px 360 \
  --audio-input-frontend es7210 \
  --audio-input-codec es7210 \
  --audio-output-codec es8311 \
  --microphone-count 2 \
  --revision-required \
  --supported-revision v2 \
  --unsupported-revision v1 \
  --dry-run
```

Remove `--dry-run` once the output looks right. Existing profiles are protected
unless `--force` is passed.

## Fill The Wiring

Transcribe the dev-board connection table into `wiring`:

- `gpios`: buttons, mute switches, reset lines, amplifier enables, backlights
- `i2c_buses`: port, SDA, SCL, clock, pullups, and device addresses
- `i2s_buses`: port, role, MCLK, BCLK, LRCLK, DIN, and DOUT
- `spi_buses`: display or storage SPI pins
- `led_strips`: data pin, optional power pin, pixel count, and color order

The ESP32 SoC pinout is useful for checking whether a GPIO can perform a role,
but the dev-board schematic is authoritative because it tells us what the
manufacturer actually wired.

## Validate

```bash
python firmware/tools/validate_board_profiles.py
```

Buildable profiles must have complete wiring and existing adapter source files.
Planned profiles may keep empty wiring arrays while the schematic is being
transcribed.

## Generated Firmware Files

During CMake configure, `firmware/tools/generate_board_profile_config.py`
generates:

- `board_profile_config.cmake`: compile definitions and adapter source list
- `board_profile_pins.h`: pin, bus, and device-address constants

Firmware adapters include `firmware/main/board/pins.h`, which forwards to the
generated `board_profile_pins.h`.

## Promotion Checklist

- board docs and schematic URL are listed in `sources`
- hardware revision support/unsupported list is explicit
- display inches and pixels are present when display is enabled
- audio frontend, codec, transport, and DSP flags are present
- `wiring.status` is `complete`
- every required I2C/I2S/SPI/GPIO value is filled
- adapter source files are selected or implemented
- profile validates
- selected profile builds locally before OTA is considered
