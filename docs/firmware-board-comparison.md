# Firmware Board Profile Comparison

Status: generated comparison from committed `firmware/boards/*/board.yaml`
profiles

This comparison is based on the board-profile YAML files, not on vendor prose.
Column names include the source YAML path where that helps future refactors.

## Profile and Build

| YAML Field | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- |
| `board_profile` | `ha_voice_pe` | `esp_box_3` | `waveshare_s3_touch_lcd_1_85c_box_v2` | `waveshare_p4_wifi6_touch_lcd_7b` |
| `display_name` | Home Assistant Voice Preview Edition | ESP32-S3-BOX-3 | Waveshare ESP32-S3-Touch-LCD-1.85C-BOX V2 | Waveshare ESP32-P4-WIFI6-Touch-LCD-7B |
| `vendor` | Nabu Casa / Home Assistant | Espressif | Waveshare | Waveshare |
| `model` | Home Assistant Voice Preview Edition | ESP32-S3-BOX-3 | ESP32-S3-Touch-LCD-1.85C-BOX | ESP32-P4-WIFI6-Touch-LCD-7B |
| `support_status` | `active` | `active` | `planned` | `planned` |
| `hardware_revision.required` | `false` | `false` | `true` | `false` |
| `hardware_revision.supported` | `production` | `production` | `v2`, `rev2.0` | `standard`, `camera_option` |
| `hardware_revision.unsupported` | none | none | `v1` | none |
| `build.idf_target` | `esp32s3` | `esp32s3` | `esp32s3` | `esp32p4` |
| `build.partition_schema` | `s3-16m-v1` | `s3-16m-v1` | `s3-16m-v1` | `p4-32m-v1` |
| `build.app_slot_size` | `4MiB` | `4MiB` | `4MiB` | `8MiB` |
| `build.recovery_app` | `true` | `true` | `true` | `true` |
| `build.compile_definitions` | `HEXE_BOARD_PROFILE_HA_VOICE_PE=1` | `HEXE_BOARD_PROFILE_ESP_BOX_3=1` | `HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2=1` | `HEXE_BOARD_PROFILE_WAVESHARE_P4_WIFI6_TOUCH_LCD_7B=1` |

## Hardware

| YAML Field | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- |
| `hardware.soc` | `esp32s3` | `esp32s3` | `esp32s3` | `esp32p4` |
| `hardware.cpu` | dual-core Xtensa LX7 up to 240MHz | dual-core Xtensa LX7 up to 240MHz | dual-core Xtensa LX7 up to 240MHz | dual-core HP RISC-V up to 360MHz plus LP RISC-V up to 40MHz |
| `hardware.flash_size` | `16MiB` | `16MiB` | `16MiB` | `32MiB` |
| `hardware.psram_size` | `8MiB` | `16MiB` | `8MiB` | `32MiB` |
| `hardware.wireless.wifi` | 2.4GHz 802.11 b/g/n | 2.4GHz 802.11 b/g/n | 2.4GHz 802.11 b/g/n | Wi-Fi 6 through ESP32-C6 coprocessor |
| `hardware.wireless.bluetooth` | Bluetooth 5.0 LE | Bluetooth 5 LE | Bluetooth 5 LE | Bluetooth 5 LE through ESP32-C6 coprocessor |
| `hardware.wireless.coprocessor` | `null` | `null` | `null` | `esp32c6` |
| `hardware.wireless.transport` | `native` | `native` | `native` | `sdio` |

## Feature Flags

| YAML Field | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- |
| `features.display` | `false` | `true` | `true` | `true` |
| `features.touch` | `false` | `true` | `true` | `true` |
| `features.sd_card` | `false` | `true` | `true` | `true` |
| `features.speaker` | `true` | `true` | `true` | `true` |
| `features.microphone` | `true` | `true` | `true` | `true` |
| `features.led_ring` | `true` | `false` | `false` | `false` |
| `features.status_led` | `false` | `true` | `true` | `true` |
| `features.rotary_encoder` | `true` | `false` | `false` | `false` |
| `features.buttons` | `true` | `true` | `true` | `true` |
| `features.hardware_mute` | `true` | `false` | `false` | `false` |
| `features.battery` | `false` | `false` | `true` | `true` |
| `features.camera` | `false` | `false` | `false` | `true` |
| `features.usb_otg` | `false` | `true` | `false` | `true` |

## Display

| YAML Field | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- |
| `display.available` | `false` | `true` | `true` | `true` |
| `display.kind` | `null` | `lcd` | `lcd` | `ips_lcd` |
| `display.driver` | `null` | `ili9341_or_st7789_bsp` | `waveshare_round_lcd` | `mipi_dsi` |
| `display.interface` | `null` | `spi` | `spi` | `mipi_dsi` |
| `display.size_inches` | `null` | `2.4` | `1.85` | `7` |
| `display.shape` | `null` | `rectangular` | `round` | `rectangular` |
| `display.width_px` | `null` | `320` | `360` | `1024` |
| `display.height_px` | `null` | `240` | `360` | `600` |
| `display.color_depth` | `null` | `rgb565` | `262k_colors` | `rgb565_or_rgb888` |
| `display.touch` | `false` | `true` | `true` | `true` |

## Audio

| YAML Field | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- |
| `audio.input.available` | `true` | `true` | `true` | `true` |
| `audio.input.frontend` | `xmos_xu316` | `es7210` | `es7210` | `es7210` |
| `audio.input.sample_rate_hz` | `16000` | `16000` | `16000` | `16000` |
| `audio.input.channels` | `1` | `1` | `1` | `1` |
| `audio.input.transport` | `i2s` | `i2s` | `i2s` | `i2s_tdm` |
| `audio.input.codec` | `xmos_xu316` | `es7210` | `es7210` | `es7210` |
| `audio.input.microphones` | `2` | `2` | `2` | `2` |
| `audio.input.dsp.aec` | `true` | `false` | `true` | `true` |
| `audio.input.dsp.noise_suppression` | `true` | `false` | `false` | `false` |
| `audio.input.dsp.agc` | `true` | `false` | `false` | `false` |
| `audio.input.dsp.vad` | `true` | `false` | `false` | `false` |
| `audio.output.available` | `true` | `true` | `true` | `true` |
| `audio.output.codec` | `ti_aic3204` | `es8311` | `es8311` | `es8311` |
| `audio.output.sample_rate_hz` | `48000` | `48000` | `48000` | `48000` |
| `audio.output.transport` | `i2s` | `i2s` | `i2s` | `i2s` |
| `audio.output.speaker` | `true` | `true` | `true` | `true` |
| `audio.output.line_out` | `true` | `false` | `false` | `false` |

## Wake and Storage

| YAML Field | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- |
| `wake.local_micro_wake_word` | `true` | `true` | `true` | `true` |
| `wake.primary_model` | `alexa` | `alexa` | `alexa` | `alexa` |
| `wake.alias` | `Hexe` | `Hexe` | `Hexe` | `Hexe` |
| `wake.backend_fallback` | `true` | `true` | `true` | `true` |
| `wake.stop_model` | `stop` | `stop` | `stop` | `stop` |
| `storage.internal` | `nvs_and_internal_flash` | `nvs_and_spiffs` | `nvs_and_internal_flash` | `nvs_and_internal_flash` |
| `storage.sd_card.available` | `false` | `true` | `true` | `true` |
| `storage.sd_card.driver` | `null` | `bsp_sdcard` | `sdmmc_or_spi_tbd` | `sdmmc` |
| `storage.sd_card.interface` | `null` | `sdmmc` | `tf_card` | `sdio_3_0_tf_card` |
| `storage.config` | `encrypted_nvs` | `encrypted_nvs` | `encrypted_nvs` | `encrypted_nvs` |
| `storage.calibration` | `encrypted_nvs_or_internal_metrics_store` | `encrypted_nvs_or_spiffs_metrics_store` | `encrypted_nvs_or_internal_metrics_store` | `encrypted_nvs_or_internal_metrics_store` |
| `storage.media` | `embedded_minimal_tones_only` | `sd_preferred_with_embedded_fallback` | `sd_preferred_with_embedded_fallback` | `sd_versioned_bundles_with_embedded_fallback` |
| `storage.models` | `embedded_fallback_then_internal_bundle_bank` | `embedded_fallback_then_internal_or_sd_bundle` | `embedded_fallback_then_internal_or_sd_bundle` | `embedded_fallback_then_sd_bundle` |

## Controls and Indicators

| YAML Field | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- |
| `controls` | `center` button, `rotary_dial` encoder, `hardware_mute` switch | `front_buttons` button, `touchscreen` touch | `boot` button, `reset` button, `touchscreen` touch | `boot` button, `reset` button, `touchscreen` touch |
| `indicators` | `led_ring` using `ws2812_rmt`, GPIO 21, 12 pixels, GRB | `display` using `esp_box_3_bsp`; `status_led` using `board_bsp` | `display` using `waveshare_round_lcd`; `status_led` using `board_led` | `display` using `mipi_dsi`; `status_led` using `board_led` |

## Capability Overrides

| YAML Field | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- |
| `capability_overrides.display` | `false` | `true` | `true` | `true` |
| `capability_overrides.touchscreen` | `false` | `true` | `true` | `true` |
| `capability_overrides.storage` | `false` | `true` | `true` | `true` |
| `capability_overrides.audio_input` | `true` | `true` | `true` | `true` |
| `capability_overrides.audio_output` | `true` | `true` | `true` | `true` |

## Config-Driven Conclusions

- `ha_voice_pe`, `esp_box_3`, and `waveshare_s3_touch_lcd_1_85c_box_v2` share
  `build.idf_target: esp32s3` and `build.partition_schema: s3-16m-v1`.
- `waveshare_p4_wifi6_touch_lcd_7b` is the only `esp32p4` profile and requires
  `p4-32m-v1` plus `hardware.wireless.coprocessor: esp32c6`.
- `ha_voice_pe` is the only profile with `features.led_ring`,
  `features.rotary_encoder`, and `features.hardware_mute` enabled.
- The three display profiles all expose `display.size_inches`,
  `display.width_px`, and `display.height_px`; `ha_voice_pe` intentionally keeps
  them `null`.
- `waveshare_s3_touch_lcd_1_85c_box_v2` is the only profile with
  `hardware_revision.required: true`; V1 is explicitly unsupported.
- All four profiles keep `wake.backend_fallback: true`, preserving backend
  wake-word fallback while local microWakeWord is enabled.
