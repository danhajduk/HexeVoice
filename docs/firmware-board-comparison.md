# Firmware Board Comparison

Status: candidate hardware comparison for firmware portability work

This table compares the four board profiles currently in scope for the portable
Hexe firmware board-profile work.

| Board Profile | Device | Status | SoC / Compute | Flash / PSRAM | Screen | Touch | Audio Input | Audio Output | Storage / Expansion | Hexe Firmware Notes | Sources |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ha_voice_pe` | Home Assistant Voice Preview Edition | active | ESP32-S3, dual-core Xtensa LX7 class | 16 MiB flash / 8 MiB octal PSRAM | none | no | XMOS XU316, dual-mic array, dedicated I2S input, AEC, stationary noise removal, AGC | internal speaker, 3.5 mm output, TI AIC3204 DAC, dedicated I2S output | Grove port, exposed pads, USB-C power/data | Best current voice-first reference. No display stack. Strong audio frontend. Already supported by the current firmware profile. | [Home Assistant specs](https://www.home-assistant.io/voice-pe/), [ESPHome source](https://github.com/esphome/home-assistant-voice-pe) |
| `esp_box_3` | Espressif ESP32-S3-BOX-3 | active | ESP32-S3-WROOM-1, 2.4 GHz Wi-Fi + Bluetooth 5 LE | 16 MiB Quad/QSPI flash / 16 MiB Octal PSRAM | 2.4 inch rectangular LCD, 320 x 240 px | yes, capacitive | two digital microphones, ES7210 codec path per BSP | speaker, ES8311 codec path per BSP | USB-C, dock/high-density expansion, Pmod-compatible headers; microSD via accessory/dock profile | Best small display development reference. Existing Hexe firmware profile supports display, touch, SD media behavior, audio, and OTA. | [Espressif BSP](https://components.espressif.com/components/espressif/esp-box-3), [hardware overview](https://github.com/espressif/esp-box/blob/master/docs/hardware_overview/esp32_s3_box_3/hardware_overview_for_box_3.md) |
| `waveshare_s3_touch_lcd_1_85c_box_v2` | Waveshare ESP32-S3-Touch-LCD-1.85C-BOX V2 | planned | ESP32-S3R8, dual-core Xtensa LX7 up to 240 MHz | 16 MiB flash / 8 MiB PSRAM | 1.85 inch round LCD, 360 x 360 px, 262K colors | yes, I2C touch | V2-only target: ES7210 input, dual microphones, echo-cancellation path | ES8311 output, speaker box, 4 ohm 5 W speaker noted for BOX variant | TF/microSD slot, RTC, battery support/charging, USB-C, UART/I2C/GPIO | Small smart-speaker-with-display candidate. Must require V2/Rev2.0 and reject V1 because the audio path changed materially. | [Waveshare docs](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85C) |
| `waveshare_p4_wifi6_touch_lcd_7b` | Waveshare ESP32-P4-WIFI6-Touch-LCD-7B | planned | ESP32-P4 dual-core HP RISC-V up to 360 MHz plus LP RISC-V up to 40 MHz; ESP32-C6 Wi-Fi/BLE coprocessor over SDIO | 32 MiB NOR flash / 32 MiB PSRAM | 7 inch landscape IPS LCD, 1024 x 600 px | yes, 5-point capacitive touch class | ES7210 dual-mic input / echo-cancellation path | ES8311 codec, speaker terminal | SDIO 3.0 TF slot, MIPI-CSI camera option, USB OTG HS, USB-C, GPIO, I2C, UART, CAN, RS485 | Best central-room display/panel candidate. Needs separate ESP32-P4 build class, ESP32-C6 radio transport support, and `p4-32m-v1` partition schema. | [Waveshare docs](https://docs.waveshare.com/ESP32-P4-WIFI6-Touch-LCD-7B), [Waveshare examples](https://github.com/waveshareteam/ESP32-P4-WIFI6-Touch-LCD-7B) |

## Quick Fit Summary

| Use Case | Best Fit | Why |
| --- | --- | --- |
| Voice-only satellite | `ha_voice_pe` | Strongest existing audio hardware, no display overhead, already active in firmware. |
| Small display voice dev target | `esp_box_3` | Existing support and known ESP-IDF BSP path. |
| Compact countertop smart speaker | `waveshare_s3_touch_lcd_1_85c_box_v2` | Round display, speaker box, microSD, and V2 audio path in a small package. |
| Wall/tabletop control panel | `waveshare_p4_wifi6_touch_lcd_7b` | Large display, 32 MiB flash/PSRAM, SD storage, and richer HMI peripherals. |

## Portability Implications

- `ha_voice_pe` and `esp_box_3` stay in the `s3-16m-v1` partition class.
- `waveshare_s3_touch_lcd_1_85c_box_v2` should also start in `s3-16m-v1`, but
  must be treated as a separate board profile because display, touch, audio,
  battery, and storage wiring differ.
- `waveshare_p4_wifi6_touch_lcd_7b` needs a separate `p4-32m-v1` build and
  partition class because it is not an ESP32-S3 device and uses an ESP32-C6
  radio coprocessor.
- The board-profile schema must keep screen size as both diagonal inches and
  pixel dimensions so UI/assets can be selected without probing hardware at
  runtime.
- Endpoint-specific values such as endpoint id, backend host, Wi-Fi, and keys
  still belong outside the board profile.
