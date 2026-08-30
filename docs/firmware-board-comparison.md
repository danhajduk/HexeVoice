# Firmware Board Profile Comparison

Status: generated comparison from committed `firmware/boards/*/board.yaml`
profiles

This comparison is based on the board-profile YAML files, not on vendor prose.
Column names include the source YAML path where that helps future refactors.

## Profile and Build

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `board_profile` | `xvf3800_xiao_s3` | `ha_voice_pe` | `esp_box_3` | `waveshare_s3_touch_lcd_1_85c_box_v2` | `waveshare_p4_wifi6_touch_lcd_7b` |
| `display_name` | ReSpeaker XVF3800 with XIAO ESP32-S3 | Home Assistant Voice Preview Edition | ESP32-S3-BOX-3 | Waveshare ESP32-S3-Touch-LCD-1.85C-BOX V2 | Waveshare ESP32-P4-WIFI6-Touch-LCD-7B |
| `vendor` | Seeed Studio | Nabu Casa / Home Assistant | Espressif | Waveshare | Waveshare |
| `model` | reSpeaker XVF3800 USB 4-Mic Array with XIAO ESP32S3 | Home Assistant Voice Preview Edition | ESP32-S3-BOX-3 | ESP32-S3-Touch-LCD-1.85C-BOX | ESP32-P4-WIFI6-Touch-LCD-7B |
| `support_status` | `active` | `active` | `active` | `planned` | `planned` |
| `hardware_revision.required` | `false` | `false` | `false` | `true` | `false` |
| `hardware_revision.supported` | `production` | `production` | `production` | `v2`, `rev2.0` | `standard`, `camera_option` |
| `hardware_revision.unsupported` | none | none | none | `v1` | none |
| `build.idf_target` | `esp32s3` | `esp32s3` | `esp32s3` | `esp32s3` | `esp32p4` |
| `build.partition_schema` | `s3-8m-recovery-v1` | `s3-16m-recovery-v1` | `s3-16m-recovery-v1` | `s3-16m-recovery-v1` | `p4-32m-v1` |
| `build.app_slot_size` | `2560K` | `4MiB` | `4MiB` | `4MiB` | `8MiB` |
| `build.recovery_app` | `true` | `true` | `true` | `true` | `true` |
| `build.compile_definitions` | `HEXE_BOARD_PROFILE_XVF3800_XIAO_S3=1` | `HEXE_BOARD_PROFILE_HA_VOICE_PE=1` | `HEXE_BOARD_PROFILE_ESP_BOX_3=1` | `HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2=1` | `HEXE_BOARD_PROFILE_WAVESHARE_P4_WIFI6_TOUCH_LCD_7B=1` |

## Firmware Adapters

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `adapters.buildable` | `true` | `true` | `true` | `false` | `false` |
| `adapters.source_files` | `board/xvf3800_control.cpp`, `board/xvf3800_audio_bus.cpp`, `board/audio_xvf3800_xiao_s3.cpp`, `board/buttons_xvf3800_xiao_s3.cpp`, `board/display_none.cpp`, `board/led_ring_xvf3800_xiao_s3.cpp`, `board/storage_nvs_only.cpp`, `board/touch_none.cpp`, `voice/tts_player_xvf3800_xiao_s3.cpp` | `board/audio_ha_voice_pe.cpp`, `board/buttons_ha_voice_pe.cpp`, `board/display_none.cpp`, `board/led_ring_ha_voice_pe.cpp`, `board/storage_nvs_only.cpp`, `board/touch_none.cpp`, `voice/tts_player_ha_voice_pe.cpp` | `board/audio.cpp`, `board/buttons.cpp`, `board/display.cpp`, `board/led_ring.cpp`, `board/storage.cpp`, `board/touch.cpp`, `voice/tts_player.cpp` | none | none |

## Firmware Wiring

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `wiring.status` | `complete` | `complete` | `complete` | `partial` | `partial` |
| `wiring.i2c_buses` | `audio_control`: port 0, SDA GPIO5, SCL GPIO6, 400 kHz, devices `voice_processor` 44 / `speaker_codec` 24 | `audio_control`: port 0, SDA GPIO5, SCL GPIO6, 400 kHz, devices `voice_kit` 66 / `speaker_codec` 24 | `primary`: port 0, SDA GPIO8, SCL GPIO18, 400 kHz | pending schematic transcription | pending schematic transcription |
| `wiring.i2s_buses` | `audio`: port 0, BCLK GPIO8, LRCLK GPIO7, DIN GPIO43, DOUT GPIO44 | `microphone`: port 0, BCLK GPIO13, LRCLK GPIO14, DIN GPIO15; `speaker`: port 1, BCLK GPIO8, LRCLK GPIO7, DOUT GPIO10 | `audio`: port 0, MCLK GPIO2, BCLK GPIO17, LRCLK GPIO45, DIN GPIO16, DOUT GPIO15 | pending schematic transcription | pending schematic transcription |
| `wiring.spi_buses` | none | none | `display`: CLK GPIO7, MOSI GPIO6, MISO GPIO13, CS GPIO5, DC GPIO4, reset GPIO48, backlight GPIO47 | pending schematic transcription | pending schematic transcription |
| `wiring.gpios` | XVF3800-side GPIO via I2C | reset GPIO4, amp GPIO47, center GPIO0, mute GPIO3, dial GPIO16/GPIO18 | button GPIO0, backlight GPIO47, speaker enable GPIO46 | pending schematic transcription | pending schematic transcription |
| `wiring.led_strips` | XVF3800 LED engine via I2C | `led_ring`: data GPIO21, power GPIO45, 12 pixels, GRB | none | none | none |

## Hardware

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `hardware.soc` | `esp32s3` | `esp32s3` | `esp32s3` | `esp32s3` | `esp32p4` |
| `hardware.cpu` | dual-core Xtensa LX7 up to 240MHz | dual-core Xtensa LX7 up to 240MHz | dual-core Xtensa LX7 up to 240MHz | dual-core Xtensa LX7 up to 240MHz | dual-core HP RISC-V up to 360MHz plus LP RISC-V up to 40MHz |
| `hardware.flash_size` | `8MiB` | `16MiB` | `16MiB` | `16MiB` | `32MiB` |
| `hardware.psram_size` | `8MiB` | `8MiB` | `16MiB` | `8MiB` | `32MiB` |
| `hardware.wireless.wifi` | 2.4GHz 802.11 b/g/n | 2.4GHz 802.11 b/g/n | 2.4GHz 802.11 b/g/n | 2.4GHz 802.11 b/g/n | Wi-Fi 6 through ESP32-C6 coprocessor |
| `hardware.wireless.bluetooth` | Bluetooth 5.0 LE | Bluetooth 5.0 LE | Bluetooth 5 LE | Bluetooth 5 LE | Bluetooth 5 LE through ESP32-C6 coprocessor |
| `hardware.wireless.coprocessor` | `null` | `null` | `null` | `null` | `esp32c6` |
| `hardware.wireless.transport` | `native` | `native` | `native` | `native` | `sdio` |

## Feature Flags

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `features.display` | `false` | `false` | `true` | `true` | `true` |
| `features.touch` | `false` | `false` | `true` | `true` | `true` |
| `features.sd_card` | `false` | `false` | `true` | `true` | `true` |
| `features.speaker` | `true` | `true` | `true` | `true` | `true` |
| `features.microphone` | `true` | `true` | `true` | `true` | `true` |
| `features.led_ring` | `true` | `true` | `false` | `false` | `false` |
| `features.status_led` | `false` | `false` | `true` | `true` | `true` |
| `features.rotary_encoder` | `false` | `true` | `false` | `false` | `false` |
| `features.buttons` | `true` | `true` | `true` | `true` | `true` |
| `features.hardware_mute` | `true` | `true` | `false` | `false` | `false` |
| `features.battery` | `false` | `false` | `false` | `true` | `true` |
| `features.camera` | `false` | `false` | `false` | `false` | `true` |
| `features.usb_otg` | `false` | `false` | `true` | `false` | `true` |

## Display

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `display.available` | `false` | `false` | `true` | `true` | `true` |
| `display.kind` | `null` | `null` | `lcd` | `lcd` | `ips_lcd` |
| `display.driver` | `null` | `null` | `ili9341_or_st7789_bsp` | `waveshare_round_lcd` | `mipi_dsi` |
| `display.interface` | `null` | `null` | `spi` | `spi` | `mipi_dsi` |
| `display.size_inches` | `null` | `null` | `2.4` | `1.85` | `7` |
| `display.shape` | `null` | `null` | `rectangular` | `round` | `rectangular` |
| `display.width_px` | `null` | `null` | `320` | `360` | `1024` |
| `display.height_px` | `null` | `null` | `240` | `360` | `600` |
| `display.color_depth` | `null` | `null` | `rgb565` | `262k_colors` | `rgb565_or_rgb888` |
| `display.touch` | `false` | `false` | `true` | `true` | `true` |

## Audio

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `audio.input.available` | `true` | `true` | `true` | `true` | `true` |
| `audio.input.frontend` | `xmos_xvf3800` | `xmos_xu316` | `es7210` | `es7210` | `es7210` |
| `audio.input.sample_rate_hz` | `16000` | `16000` | `16000` | `16000` | `16000` |
| `audio.input.channels` | `2` | `1` | `1` | `1` | `1` |
| `audio.input.transport` | `i2s` | `i2s` | `i2s` | `i2s` | `i2s_tdm` |
| `audio.input.codec` | `xmos_xvf3800` | `xmos_xu316` | `es7210` | `es7210` | `es7210` |
| `audio.input.microphones` | `4` | `2` | `2` | `2` | `2` |
| `audio.input.dsp.aec` | `true` | `true` | `false` | `true` | `true` |
| `audio.input.dsp.noise_suppression` | `true` | `true` | `false` | `false` | `false` |
| `audio.input.dsp.agc` | `true` | `true` | `false` | `false` | `false` |
| `audio.input.dsp.vad` | `true` | `true` | `false` | `false` | `false` |
| `audio.output.available` | `true` | `true` | `true` | `true` | `true` |
| `audio.output.codec` | `tlv320aic3104_via_xvf3800` | `ti_aic3204` | `es8311` | `es8311` | `es8311` |
| `audio.output.sample_rate_hz` | `16000` | `48000` | `48000` | `48000` | `48000` |
| `audio.output.transport` | `i2s` | `i2s` | `i2s` | `i2s` | `i2s` |
| `audio.output.speaker` | `true` | `true` | `true` | `true` | `true` |
| `audio.output.line_out` | `true` | `true` | `false` | `false` | `false` |

## Firmware VAD

`audio.input.dsp.vad` is a hardware/audio-frontend DSP flag. The fields below
describe Hexe firmware VAD running from PCM audio frames.

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `vad.firmware.available` | `true` | `true` | `true` | `true` | `true` |
| `vad.firmware.status` | `active` | `active` | `active` | `planned` | `planned` |
| `vad.firmware.algorithm` | `energy_threshold` | `energy_threshold` | `energy_threshold` | `energy_threshold` | `energy_threshold` |
| `vad.firmware.input_source` | `pcm_audio_frames` | `pcm_audio_frames` | `pcm_audio_frames` | `pcm_audio_frames` | `pcm_audio_frames` |
| `vad.firmware.configurable` | `true` | `true` | `true` | `true` | `true` |
| `vad.firmware.adaptive_noise_floor` | `true` | `true` | `false` | `false` | `false` |
| `vad.firmware.frame_ms` | `20` | `20` | `20` | `20` | `20` |
| `vad.firmware.default_energy_threshold` | `900` | `900` | `900` | `900` | `900` |
| `vad.firmware.default_pause_ms` | `190` | `190` | `190` | `190` | `190` |
| `vad.firmware.silence_hold_ms` | `1200` | `1200` | `2500` | `1200` | `1200` |

## Wake and Storage

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `wake.local_micro_wake_word` | `true` | `true` | `true` | `true` | `true` |
| `wake.primary_model` | `alexa` | `alexa` | `alexa` | `alexa` | `alexa` |
| `wake.alias` | `Hexe` | `Hexe` | `Hexe` | `Hexe` | `Hexe` |
| `wake.backend_fallback` | `true` | `true` | `true` | `true` | `true` |
| `wake.stop_model` | `stop` | `stop` | `stop` | `stop` | `stop` |
| `storage.internal` | `nvs_and_internal_flash` | `nvs_and_internal_flash` | `nvs_and_spiffs` | `nvs_and_internal_flash` | `nvs_and_internal_flash` |
| `storage.sd_card.available` | `false` | `false` | `true` | `true` | `true` |
| `storage.sd_card.driver` | `null` | `null` | `bsp_sdcard` | `sdmmc_or_spi_tbd` | `sdmmc` |
| `storage.sd_card.interface` | `null` | `null` | `sdmmc` | `tf_card` | `sdio_3_0_tf_card` |
| `storage.config` | `encrypted_nvs` | `encrypted_nvs` | `encrypted_nvs` | `encrypted_nvs` | `encrypted_nvs` |
| `storage.calibration` | `encrypted_nvs_or_internal_metrics_store` | `encrypted_nvs_or_internal_metrics_store` | `encrypted_nvs_or_spiffs_metrics_store` | `encrypted_nvs_or_internal_metrics_store` | `encrypted_nvs_or_internal_metrics_store` |
| `storage.media` | `embedded_minimal_tones_only` | `embedded_minimal_tones_only` | `sd_preferred_with_embedded_fallback` | `sd_preferred_with_embedded_fallback` | `sd_versioned_bundles_with_embedded_fallback` |
| `storage.models` | `embedded_fallback_then_internal_bundle_bank` | `embedded_fallback_then_internal_bundle_bank` | `embedded_fallback_then_internal_or_sd_bundle` | `embedded_fallback_then_internal_or_sd_bundle` | `embedded_fallback_then_sd_bundle` |

## Controls and Indicators

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `controls` | `mute_button` button, `microphone_mute` hardware mute through XVF3800 | `center` button, `rotary_dial` encoder, `hardware_mute` switch | `front_buttons` button, `touchscreen` touch | `boot` button, `reset` button, `touchscreen` touch | `boot` button, `reset` button, `touchscreen` touch |
| `indicators` | `led_ring` using `xvf3800_i2c_led_engine`; `mute_led` using `xvf3800_gpo30_mute_indicator` | `led_ring` using `ws2812_rmt`, GPIO 21, 12 pixels, GRB | `display` using `esp_box_3_bsp`; `status_led` using `board_bsp` | `display` using `waveshare_round_lcd`; `status_led` using `board_led` | `display` using `mipi_dsi`; `status_led` using `board_led` |

## Capability Overrides

| YAML Field | XVF3800 + XIAO S3 | HA Voice PE | ESP32-S3-BOX-3 | Waveshare 1.85C BOX V2 | Waveshare P4 7B |
| --- | --- | --- | --- | --- | --- |
| `capability_overrides.display` | `false` | `false` | `true` | `true` | `true` |
| `capability_overrides.touchscreen` | `false` | `false` | `true` | `true` | `true` |
| `capability_overrides.storage` | `false` | `false` | `true` | `true` | `true` |
| `capability_overrides.audio_input` | `true` | `true` | `true` | `true` | `true` |
| `capability_overrides.audio_output` | `true` | `true` | `true` | `true` | `true` |

## Config-Driven Conclusions

- `xvf3800_xiao_s3`, `ha_voice_pe`, `esp_box_3`, and
  `waveshare_s3_touch_lcd_1_85c_box_v2` share `build.idf_target: esp32s3`.
  The XVF3800 profile uses `s3-8m-recovery-v1`, which reserves a 2 MiB factory
  recovery app plus two 2.5 MiB endpoint OTA slots; the 16 MiB S3 profiles use
  `s3-16m-recovery-v1`, which reserves a 2 MiB factory recovery app plus two 4
  MiB endpoint OTA slots.
- `waveshare_p4_wifi6_touch_lcd_7b` is the only `esp32p4` profile and requires
  `p4-32m-v1` plus `hardware.wireless.coprocessor: esp32c6`.
- `xvf3800_xiao_s3` and `ha_voice_pe` enable `features.led_ring` and
  `features.hardware_mute`; `ha_voice_pe` remains the only rotary encoder
  profile.
- `audio.input.dsp.vad` is true for the XVF3800 and HA Voice PE hardware audio
  frontends. Firmware VAD is separately available on all five profiles and
  active today on the three active firmware profiles.
- The three display profiles all expose `display.size_inches`,
  `display.width_px`, and `display.height_px`; `xvf3800_xiao_s3` and
  `ha_voice_pe` intentionally keep them `null`.
- `waveshare_s3_touch_lcd_1_85c_box_v2` is the only profile with
  `hardware_revision.required: true`; V1 is explicitly unsupported.
- All five profiles keep `wake.backend_fallback: true`, preserving backend
  wake-word fallback while local microWakeWord is enabled.
