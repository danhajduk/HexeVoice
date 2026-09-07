# Waveshare P4 7B Roadmap

Status: planning and bring-up tracker from upstream Waveshare repo inspection

Upstream source: https://github.com/waveshareteam/ESP32-P4-WIFI6-Touch-LCD-7B

## Current Hexe State

`firmware/boards/waveshare_p4_wifi6_touch_lcd_7b/board.yaml` now defines the
target board and pinout, but runtime support is intentionally still planned:

- `support_status: planned`
- `wiring.status: complete`
- `adapters.buildable: false`
- `build.idf_target: esp32p4`
- `build.partition_schema: p4-32m-v1`
- `display`: 1024 x 600 MIPI-DSI
- `touch`: GT911
- `audio`: ES7210 microphone input, ES8311 speaker output
- `wireless`: ESP32-C6 coprocessor over SDIO

The first goal is not to copy the Waveshare demo firmware into Hexe. The useful
path is to use Waveshare's BSP and examples as board bring-up evidence, then
implement Hexe-native adapters behind the existing board-profile boundary.

Product direction: the 7B should boot directly into one Hexe endpoint
experience. The current factory/demo firmware looks like a tablet/car-display
launcher because it uses Waveshare/Espressif demo UI layers. Hexe should not
ship that app-shell model; it should run a single voice endpoint app with Hexe
states, pairing, diagnostics, update status, and touch controls.

## Schematic And BSP Pinout Baseline

Pinout source:

- Schematic PDF:
  `https://files.waveshare.com/wiki/ESP32-P4-WIFI6-Touch-LCD-7B/ESP32-P4-WIFI6-Touch-LCD-7B.pdf`
- Managed BSP archive:
  `waveshare/esp32_p4_wifi6_touch_lcd_7b` version `3.0.1`

The schematic and BSP agree on the core pins we need for a Hexe voice-node
bring-up:

| Function | Signal | GPIO / Value | Notes |
| --- | --- | --- | --- |
| I2C | `ESP_I2C_SDA` | GPIO7 | Shared control bus for touch/audio configuration. BSP macro: `BSP_I2C_SDA`. |
| I2C | `ESP_I2C_SCL` | GPIO8 | BSP macro: `BSP_I2C_SCL`; default BSP bus index is `CONFIG_BSP_I2C_NUM`, default `1`. |
| I2S audio | `DSDIN` / speaker data out from ESP32-P4 | GPIO9 | BSP macro: `BSP_I2S_DOUT`. |
| I2S audio | `LRCK` | GPIO10 | BSP macro: `BSP_I2S_LCLK`. |
| I2S audio | `ASDOUT` / mic data into ESP32-P4 | GPIO11 | BSP macro: `BSP_I2S_DSIN`. |
| I2S audio | `SCLK` | GPIO12 | BSP macro: `BSP_I2S_SCLK`. |
| I2S audio | `MCLK` | GPIO13 | BSP macro: `BSP_I2S_MCLK`. |
| Speaker amp | `PA_CTRL` | GPIO53 | Active-high amplifier enable. BSP macro: `BSP_POWER_AMP_IO`. |
| LCD backlight | `BL_CTRL` | GPIO32 | PWM backlight via LEDC. BSP macro: `BSP_LCD_BACKLIGHT`. |
| LCD reset | `RESET_LCD` | GPIO33 | BSP macro: `BSP_LCD_RST`. |
| LCD panel | EK79007 MIPI-DSI | 1024 x 600, 2 lanes | BSP uses 52 MHz pixel clock and 1000 Mbps lane bitrate. |
| MIPI DSI PHY power | DPHY LDO | channel 3, 2500 mV | BSP macro: `BSP_MIPI_DSI_PHY_PWR_LDO_CHAN`. |
| Touch | GT911 I2C | addresses `0x5d`, fallback `0x14` | BSP probes both addresses. |
| Touch reset/int | `RESET_TP`, `INT_TP` | no firmware GPIO in BSP | BSP sets both to `GPIO_NUM_NC`; treat as connector-level nets unless later evidence says otherwise. |
| microSD | D0 | GPIO39 | BSP macro: `BSP_SD_D0`. |
| microSD | D1 | GPIO40 | BSP macro: `BSP_SD_D1`. |
| microSD | D2 | GPIO41 | BSP macro: `BSP_SD_D2`. |
| microSD | D3 / CD | GPIO42 | BSP macro: `BSP_SD_D3`; slot config uses no explicit card-detect GPIO. |
| microSD | CLK | GPIO43 | BSP macro: `BSP_SD_CLK`. |
| microSD | CMD | GPIO44 | BSP macro: `BSP_SD_CMD`. |
| microSD power | SD LDO | LDO channel 4 | BSP SD mount uses on-chip LDO channel 4 and SDMMC high-speed mode. |
| ESP32-C6 hosted link | SDIO / control nets | GPIO14-GPIO19 plus C6 nets | Use upstream hosted Wi-Fi example and BSP/ESP-Hosted contracts before coding this path. |

Implementation rule: where the BSP exposes a macro or helper, prefer that over
duplicating pin constants in Hexe C++. The board YAML can still record the
pinout for validation and documentation, but runtime adapters should lean on
the managed BSP boundary first.

## Upstream Pieces We Can Use

| Area | Upstream Source | Use In Hexe |
| --- | --- | --- |
| Managed board BSP | `waveshare/esp32_p4_wifi6_touch_lcd_7b` version `3.0.1` | Preferred hardware abstraction for display, touch, audio, SD card, and board constants. |
| ESP32-P4 build defaults | `config/esp32p4_rev*_*.defaults`, example `sdkconfig.defaults` | Create a P4-specific Hexe sdkconfig layer: 32 MiB flash, 200 MHz PSRAM, cache settings, revision choice. |
| Board smoke test | `examples/esp-idf/00_board_check` | First hardware validation before Hexe runtime work. |
| I2C diagnostics | `examples/esp-idf/03_i2c_tools` | Validate GT911/audio/control bus visibility; default example pins are SDA GPIO7, SCL GPIO8. |
| SD card | `examples/esp-idf/04_sdmmc` | Transcribe SDMMC pins into board YAML and adapt Hexe media/model bundle mounting from SD/assets. |
| Hosted Wi-Fi | `examples/esp-idf/05_wifistation` | Source for ESP32-C6 hosted Wi-Fi dependencies and initialization expectations. |
| Audio codec | `examples/esp-idf/06_i2s_codec` | Reuse BSP audio entrypoints for ES8311 output and ES7210 microphone input; I2S pins are GPIO9-GPIO13 and amp enable is GPIO53. |
| Low-level panel | `examples/esp-idf/07_color_panel` | Validate EK79007 MIPI-DSI before LVGL; useful constants include 1024 x 600, reset GPIO33, backlight GPIO32, 2 DSI lanes, 2500 mV MIPI PHY LDO channel 3. |
| LVGL + touch | `examples/esp-idf/08_lvgl_display_panel`, `09_lvgl_demo_v9` | Source for `bsp_display_start_with_config`, GT911 touch flags, LVGL 9, and `esp_lvgl_adapter` integration. |
| Factory-style UI | `firmware/brookesia` and `examples/esp-idf/11_esp_brookesia_phone` | Reference only for large-screen layout, touch rotation, media loading, and performance settings. Do not reuse the launcher/car-display UI model. |
| USB display/media | `examples/esp-idf/12_usb_extend_screen`, `18_mp4_player` | Later expansion candidates, not needed for a voice-node MVP. |

## Pieces To Avoid Or Defer

- Do not flash or ship `firmware/ESP32-P4-WIFI6-Touch-LCD-7B-FactoryOnly.bin`
  as a Hexe artifact. Keep it as a factory baseline only.
- Do not copy Brookesia sample apps, games, media, or customer-facing text into
  Hexe firmware.
- Do not ship a phone/tablet/car-launcher style home screen. The 7B target is a
  dedicated Hexe endpoint, not a multi-app appliance.
- Do not add legacy `esp_lvgl_port` beside Waveshare's BSP-provided
  `esp_lvgl_adapter`.
- Do not make USB extended display, camera, MP4 playback, RS485, or CAN/TWAI
  part of the first voice-node bring-up.
- Do not trust compile success as hardware validation; display, touch, audio,
  SD, and hosted Wi-Fi each need board-level smoke tests.

## Phase 0: Confirm The Hardware Baseline

Outcome: known silicon/profile and a reproducible upstream validation trail.

Tasks:

- Build and flash upstream `examples/esp-idf/00_board_check`.
- Record board chip revision and decide whether Hexe uses a Rev3.x default or
  needs a Rev1.3 override.
- Build upstream `examples/esp-idf/07_color_panel` and confirm the panel shows
  hardware/software color bars.
- Build upstream `examples/esp-idf/08_lvgl_display_panel` and confirm touch
  coordinates, orientation, and mirroring.
- Build upstream `examples/esp-idf/06_i2s_codec` in echo mode and confirm mic
  capture plus speaker output.
- Build upstream `examples/esp-idf/05_wifistation` and confirm hosted Wi-Fi
  joins the target network through ESP32-C6.

Validation gate:

- Upstream examples pass on the actual device before Hexe-specific adapter work
  begins.

## Phase 1: Promote The Board Profile To Pinmapped

Outcome: `waveshare_p4_wifi6_touch_lcd_7b` has complete machine-readable wiring
while adapters remain disabled.

Status: complete.

Tasks:

- Add SDMMC wiring from schematic/BSP: CLK GPIO43, CMD GPIO44, D0 GPIO39, D1
  GPIO40, D2 GPIO41, D3 GPIO42, no explicit card-detect GPIO.
- Add display wiring facts from upstream direct-panel example: EK79007
  MIPI-DSI, 2 lanes, 1024 x 600, reset GPIO33, backlight GPIO32, MIPI PHY LDO
  channel 3 at 2500 mV, pixel clock 52 MHz, lane bitrate 1000 Mbps.
- Add I2C bus defaults from schematic/BSP: SDA GPIO7, SCL GPIO8, 400 kHz,
  BSP bus index default `1`.
- Add known touch controller entry: GT911 at `0x5d` with fallback `0x14`;
  record touch reset/interrupt as not connected in firmware because the BSP
  sets both to `GPIO_NUM_NC`.
- Add audio device entries for ES7210 and ES8311 on I2S: DSDIN GPIO9, LRCK
  GPIO10, ASDOUT GPIO11, SCLK GPIO12, MCLK GPIO13, amp enable GPIO53.
- Add an ESP32-C6 hosted-link TODO for GPIO14-GPIO19 plus C6 control nets, but
  keep it out of buildable routing until the hosted Wi-Fi example is validated.
- Keep `adapters.buildable: false` until the P4 adapter source files exist.

Validation gate:

- `python firmware/tools/validate_board_profiles.py` passes with
  `wiring.status: complete` and planned adapters.

## Phase 2: Add P4 Build Class And Dependencies

Outcome: Hexe can configure an ESP32-P4 build without enabling the 7B app yet.

Status: in progress. The profile already selects `esp32p4`, 32 MiB flash, and
`p4-32m-v1`; the endpoint runtime now has target-gated Waveshare BSP,
hosted-Wi-Fi, and LVGL dependencies. The expected configure stop remains
`adapters.buildable: false`.

Tasks:

- Add a P4-specific sdkconfig defaults layer instead of mutating S3 defaults.
- Pull in the managed BSP dependency:
  `waveshare/esp32_p4_wifi6_touch_lcd_7b == 3.0.1`.
- Add hosted Wi-Fi dependencies only for P4:
  `espressif/esp_wifi_remote` and `espressif/esp_hosted`.
- Add LVGL 9 only when the display adapter needs it; upstream pins LVGL to
  `9.5.0` with the BSP-managed adapter line.
- Add EK79007 and GT911 dependencies through the BSP when possible, not as
  separate duplicate drivers unless the BSP boundary proves insufficient.
- Teach firmware build scripts to select `esp32p4`, 32 MiB flash, and
  `p4_32m_v1.csv` for this profile.

Validation gate:

- Configure succeeds for the 7B profile up to the expected
  `adapters.buildable: false` stop.
- Existing ESP32-S3 profiles still configure and test unchanged.

## Phase 3: Implement Minimal Hexe Adapters

Outcome: the board profile can become buildable with a minimal voice-node
runtime.

Implement these first:

- `board/display_waveshare_p4_7b.cpp`: start BSP display, set rotation/touch
  flags, expose 1024 x 600 drawing surface to Hexe UI.
- `board/touch_waveshare_p4_7b.cpp`: bridge GT911/LVGL touch events into local
  UI controls.
- `board/storage_waveshare_p4_7b.cpp`: mount SD card and expose the SD/assets
  model set; if a required model set fails to load, retry twice and surface a
  device error rather than silently falling back to an embedded bank.
- `board/audio_waveshare_p4_7b.cpp`: use `bsp_audio_codec_microphone_init()`
  for ES7210 capture and feed existing wake/VAD/STT paths.
- `voice/tts_player_waveshare_p4_7b.cpp`: use
  `bsp_audio_codec_speaker_init()` for ES8311 playback while preserving Hexe's
  TTS lifecycle events.
- `board/wifi_waveshare_p4_7b.cpp` or a P4 branch in existing Wi-Fi init:
  initialize hosted Wi-Fi without changing credential ownership or BLE
  provisioning security boundaries.

Validation gate:

- `HEXE_BOARD_PROFILE=waveshare_p4_wifi6_touch_lcd_7b idf.py build` completes.
- Display boots to a Hexe status screen.
- Touch events are visible in logs or UI.
- SD card model/media loading reports a clear error when required assets are
  absent or corrupt, and succeeds when a valid card is present.
- Wake/VAD/STT capture and TTS playback work without codec contention.
- Hosted Wi-Fi reconnects using provisioned Hexe settings.

## Phase 4: Large-Screen Hexe UX

Outcome: the 7-inch display is a dedicated Hexe endpoint surface without
importing Brookesia as the main runtime.

Tasks:

- Make the first screen the actual Hexe endpoint app, not a launcher, desktop,
  app grid, or demo shell.
- Convert existing endpoint states into a 1024 x 600 layout: idle, connecting,
  listening, thinking, replying, updating, error, pairing.
- Prefer SD-delivered UI/media bundles and model sets; keep the firmware model
  contract explicit instead of carrying a silent embedded fallback bank.
- Add touch affordances for volume, pairing, mute/software privacy mode if
  supported, diagnostics, and update status.
- Reserve Brookesia only as a reference for screen density, touch rotation, and
  LVGL performance tuning.

Validation gate:

- UI assets render at native 1024 x 600 without scaling artifacts.
- Boot lands in the Hexe endpoint experience with no intermediate app shell.
- All voice lifecycle states are visible from across a room.
- Touch controls do not block listening/playback lifecycle.

## Phase 5: Optional Device Capabilities

Only after the voice-node path is stable:

- Camera preview or visual context capture.
- USB extended display mode.
- Local MP4/AVI playback.
- RS485 and CAN/TWAI diagnostics.
- Brookesia-style app shell if Hexe needs a multi-app local UI.

## First Implementation Slice

The smallest useful code slice after this planning doc:

1. Update `firmware/boards/waveshare_p4_wifi6_touch_lcd_7b/board.yaml` with
   verified SDMMC, display, and I2C facts.
2. Add a P4 sdkconfig defaults file and build-script selection logic.
3. Add the Waveshare BSP dependency behind the P4 profile.
4. Add a display-only adapter that boots to a static Hexe screen.
5. Validate on-device with upstream color panel first, then Hexe display boot.

## Open Questions

- Which ESP32-P4 silicon revision is on the target board: Rev1.3 or Rev3.x?
- Does the BSP expose every pin/address we need, or do we need to mirror a small
  board constant table in Hexe?
- Should the first 7B Hexe UI use LVGL objects directly, RGB565 frame assets,
  or a hybrid where static plates are assets and live status is LVGL text?
- Should hosted Bluetooth LE provisioning route through the ESP32-C6 path in
  the first buildable slice, or should first hardware validation use serial/NVS
  configuration only?
