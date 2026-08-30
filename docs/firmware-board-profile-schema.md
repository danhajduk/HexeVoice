# Firmware Board Profile Schema

Status: Task 266 implementation contract

## Purpose

Board profiles describe hardware and compile-time firmware capabilities for a
supported endpoint board. They are checked into the repository and are separate
from local endpoint instance configuration.

Use board profiles for:

- board identity and hardware revision requirements
- ESP-IDF target and partition schema selection
- flash and PSRAM expectations
- display size in inches and pixels
- audio frontend/output hardware
- hardware/audio-frontend DSP features
- firmware VAD capability and algorithm
- storage, controls, and indicators
- wake/Stop model support
- capability defaults reported by firmware

Do not use board profiles for:

- Wi-Fi SSIDs or passwords
- endpoint ids for a single physical device
- backend hostnames or local development ports
- signing keys, tokens, private keys, or user secrets
- learned calibration values from one installed endpoint

Those values belong in `firmware/config/endpoint.yaml`, runtime provisioning, or
NVS/backend state.

## Files

```text
firmware/boards/
  schema/board-profile.schema.json
  ha_voice_pe/board.yaml
  esp_box_3/board.yaml
  waveshare_s3_touch_lcd_1_85c_box_v2/board.yaml
  waveshare_p4_wifi6_touch_lcd_7b/board.yaml
firmware/tools/validate_board_profiles.py
```

The validator accepts `board.yaml`, `board.yml`, or `board.json` files. YAML
parsing uses PyYAML when available and falls back to the small subset used by
the committed profiles.

## Supported Initial Profiles

For a side-by-side hardware comparison, see
`docs/firmware-board-comparison.md`.

| Profile | Status | SoC | Flash / PSRAM | Display | Audio Path | Partition Schema |
| --- | --- | --- | --- | --- | --- | --- |
| `ha_voice_pe` | active | ESP32-S3 | 16 MiB / 8 MiB | none | XMOS XU316 input, TI AIC3204 output | `s3-16m-v1` |
| `esp_box_3` | active | ESP32-S3 | 16 MiB / 16 MiB | 2.4 inch, 320 x 240 | ES7210 input, ES8311 output | `s3-16m-v1` |
| `waveshare_s3_touch_lcd_1_85c_box_v2` | planned | ESP32-S3 | 16 MiB / 8 MiB | 1.85 inch round, 360 x 360 | ES7210 input, ES8311 output | `s3-16m-v1` |
| `waveshare_p4_wifi6_touch_lcd_7b` | planned | ESP32-P4 + ESP32-C6 | 32 MiB / 32 MiB | 7 inch, 1024 x 600 | ES7210 input, ES8311 output | `p4-32m-v1` |

The Waveshare 1.85 inch profile is intentionally V2-only:

- supported revisions: `v2`, `rev2.0`
- unsupported revisions: `v1`

The V2 requirement matters because the audio hardware is materially different
for the voice endpoint use case.

## Validation Rules

`audio.input.dsp.vad` means the microphone/audio-frontend hardware exposes VAD
or a VAD-like DSP signal. `vad.firmware` means Hexe firmware can run its own VAD
from PCM frames. A board can have firmware VAD even when `audio.input.dsp.vad`
is `false`.

Run:

```bash
python firmware/tools/validate_board_profiles.py
```

The validator checks:

- required top-level sections
- lower-snake-case board profile ids
- profile id matching the parent directory name
- allowed ESP-IDF targets and partition schemas
- SoC and partition-schema compatibility
- display feature flags matching display dimensions
- audio feature flags matching input/output declarations
- firmware VAD availability, status, algorithm, and timing defaults
- wake model contract: Alexa as the initial Hexe alias, Stop as interruption
  model, backend fallback enabled
- storage capability consistency
- capability override consistency
- V2-only enforcement for the Waveshare 1.85C BOX profile
- absence of secret-like keys

## Example

```yaml
schema_version: 1
board_profile: ha_voice_pe
display_name: Home Assistant Voice Preview Edition
vendor: Nabu Casa / Home Assistant
model: Home Assistant Voice Preview Edition
hardware_revision:
  required: false
  supported:
    - production
  unsupported: []
support_status: active
build:
  idf_target: esp32s3
  partition_schema: s3-16m-v1
  app_slot_size: 4MiB
  recovery_app: true
hardware:
  soc: esp32s3
  flash_size: 16MiB
  psram_size: 8MiB
display:
  available: false
  size_inches: null
  width_px: null
  height_px: null
audio:
  input:
    available: true
    frontend: xmos_xu316
    microphones: 2
    dsp:
      vad: true
  output:
    available: true
    codec: ti_aic3204
vad:
  firmware:
    available: true
    status: active
    algorithm: energy_threshold
    input_source: pcm_audio_frames
    configurable: true
    adaptive_noise_floor: true
    frame_ms: 20
    default_energy_threshold: 900
    default_pause_ms: 190
    silence_hold_ms: 1200
wake:
  local_micro_wake_word: true
  primary_model: alexa
  alias: Hexe
  backend_fallback: true
  stop_model: stop
```

## Next Step

Task 267 should consume these profiles from firmware build tooling and move
board-specific source selection behind profile-driven adapters.
