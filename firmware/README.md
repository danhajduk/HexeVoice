# Hexe Firmware

This directory is the native standalone firmware track for Hexe.

It replaces the ESPHome prototype as the active firmware track while preserving the prototype as a historical behavior reference:

- [`docs/archive/esphome/Expressif box.yaml`](/home/dan/Projects/HexeVoice/docs/archive/esphome/Expressif%20box.yaml)
- [`docs/firmware-baseline.md`](/home/dan/Projects/HexeVoice/docs/firmware-baseline.md)
- [`docs/firmware-migration-plan.md`](/home/dan/Projects/HexeVoice/docs/firmware-migration-plan.md)
- [`docs/firmware-discovery.md`](/home/dan/Projects/HexeVoice/docs/firmware-discovery.md)
- [`docs/firmware-ota.md`](/home/dan/Projects/HexeVoice/docs/firmware-ota.md)
- [`docs/firmware-provisioning.md`](/home/dan/Projects/HexeVoice/docs/firmware-provisioning.md)
- [`docs/firmware-ble-onboarding-integration.md`](/home/dan/Projects/HexeVoice/docs/firmware-ble-onboarding-integration.md)
- [`docs/firmware-production-readiness.md`](/home/dan/Projects/HexeVoice/docs/firmware-production-readiness.md)

## Goals

- standalone Hexe device behavior
- direct ownership of UI, wake word, audio, networking, and OTA
- no required Home Assistant dependency

## Layout

- `apps/endpoint/main/`
  Native endpoint app entrypoint. This is the default ESP-IDF app selected by
  `HEXE_FIRMWARE_APP=endpoint`.
- `apps/recovery/`
  Minimal recovery/provisioning app. The S3 recovery app logs recovery-safe JSON
  diagnostics over serial, starts a temporary local Wi-Fi AP and HTTP status/API
  surface, and avoids the normal endpoint runtime.
- `components/endpoint_runtime/`
  Current endpoint runtime component: app state, board adapters, audio, voice,
  UI, OTA, storage, provisioning, and backend protocol glue. This is the
  compatibility component that will be split into finer shared components as
  recovery work lands.
- `assets/`
  Shared firmware assets reference area
- `boards/`
  YAML board profiles and generated pin/wiring source of truth.
- `partitions/`
  Named partition schemas for supported flash sizes.
- `config/endpoint.example.yaml`
  Local endpoint-to-node connection example. Copy it to `config/endpoint.yaml` for machine-specific backend host and port values.

## Endpoint Node Config

For the first voice-loop implementation, endpoint discovery is intentionally deferred. Configure the HexeVoice node backend explicitly with:

```bash
cp firmware/config/endpoint.example.yaml firmware/config/endpoint.yaml
```

Then edit `firmware/config/endpoint.yaml` so `node.host`, `node.http_port`, and `node.ws_port` point at the machine running the HexeVoice backend. The local `endpoint.yaml` file is gitignored because it is machine-specific.

During the ESP-IDF build, `components/endpoint_runtime/CMakeLists.txt` runs `tools/generate_endpoint_config.py` and generates `endpoint_config.h` from `config/endpoint.yaml` when present, otherwise from `config/endpoint.example.yaml`. Firmware source consumes that generated header instead of hardcoding a node IP address.

The runtime firmware version is not read from endpoint YAML. Heartbeats, voice session starts, and firmware capabilities report the ESP-IDF app/project version embedded in the build. Firmware heartbeats also report a stable `hardware_id` derived from the ESP32-S3 eFuse MAC, such as `esp32s3-b43a4512ab90`; this is separate from the configurable endpoint id/display name.

At runtime, endpoint id, display name, backend host/ports, TLS mode, and Wi-Fi
credentials can be persisted through the operator dashboard/API provisioning
commands. Build-time YAML and Wi-Fi secrets remain the recovery fallback after a
provisioning reset. See
[`docs/firmware-provisioning.md`](../docs/firmware-provisioning.md).

When `behavior.discovery_enabled` is true, firmware first tries LAN UDP
discovery and persists the node's offer before using the static YAML host. See
[`docs/firmware-discovery.md`](../docs/firmware-discovery.md).

## SPI microSD Media Storage

On boot, firmware now tries to mount a FAT-formatted SPI microSD reader at `/sdcard`. Boot continues normally if no card is present. Wire the dock reader as:

- `MISO` -> `G9`
- `MOSI` -> `G14`
- `SCK` -> `G11`
- `CS` -> `G12`
- `VCC` -> `3V3`
- `GND` -> `GND`

Use a 16 GB or 32 GB FAT32 card for the smoothest path. A 64 GB card should use a FAT32 partition, ideally as the first partition. When mounted, firmware creates:

- `/sdcard/hexe/pictures`
- `/sdcard/hexe/sprites`
- `/sdcard/hexe/sounds`

Those paths are the stable drop zones for picture, sprite, scene manifest, and sound loading.

## Next Build Step

Once ESP-IDF is installed locally, the intended workflow is:

```bash
cd firmware
./build.sh
```

By default, `./build.sh` discovers the buildable board profiles from
`firmware/boards/*/board.yaml` and builds them. Those profiles own both adapter
source selection and the dev-board wiring used to generate
`board_profile_pins.h`:

- ESP-BOX-3: build directory `firmware/build`, flash export `firmware/export`, OTA binary `runtime/firmware/hexe_firmware_esp_box_3.bin`, and legacy OTA binary `runtime/firmware/hexe_firmware.bin`.
- Home Assistant Voice Preview Edition: build directory `firmware/build-ha-voice-pe`, flash export `firmware/export-ha-voice-pe`, and OTA binary `runtime/firmware/hexe_firmware_ha_voice_pe.bin`.

The profile validation matrix lives in
[`docs/firmware-validation-matrix.md`](../docs/firmware-validation-matrix.md).
It lists required automated and manual checks for audio streaming, wake
acceptance, TTS playback, display or LED-ring state, OTA/media, mute/volume, and
reconnect behavior.
Production firmware signing, key rotation, secure boot, flash encryption,
manufacturing, recovery, field-service, enclosure, audio opening, mute, and
service-access gates are defined in
[`docs/firmware-production-readiness.md`](../docs/firmware-production-readiness.md).

New board profiles should start with
[`firmware-new-board-bringup.md`](../docs/firmware-new-board-bringup.md) and
`tools/create_board_profile.py`. The scaffold keeps wiring partial and firmware
adapters non-buildable until the dev-board connection table is complete.

The shared `firmware/export` folder also receives the profile-named app binaries for both builds. Planned board profiles can be validated before their adapters are buildable. To build just one active profile:

```bash
cd firmware
HEXE_BOARD_PROFILE=esp_box_3 ./build.sh
HEXE_BOARD_PROFILE=ha_voice_pe ./build.sh
```

The root project also accepts `HEXE_FIRMWARE_APP`. `endpoint` is the default.
`recovery` builds the minimal S3 recovery skeleton with
`HEXE_FIRMWARE_APP=recovery HEXE_BOARD_PROFILE=<profile> ./build.sh build`.

This writes flashable artifacts to `firmware/export-ha-voice-pe`. The `ha_voice_pe` profile targets the Home Assistant Voice Preview Edition ESP32-S3 pin map for microphone input, speaker output, and the center/mute controls. It reports endpoint id `esp-pe-1` by default, and brings up the onboard Voice Kit/XMOS device over I2C before enabling the secondary I2S microphone stream. Short-pressing the center button starts a voice session with `wake_source=button`, equivalent to an accepted wake word; pressing it during an active turn cancels that turn. It is intentionally headless: display, touchscreen, and SD media storage report unavailable. TTS playback uses the onboard AIC3204 codec and the 48 kHz secondary I2S speaker path.

To flash the Home Assistant Voice device from another PC, pull that profile-specific export folder:

```bash
NODE_HOST=dan@hexe.local \
REMOTE_EXPORT=/home/dan/hexe/HexeVoice/firmware/export-ha-voice-pe \
./flash.sh /dev/ttyACM0
```

Or download the current exports from the HexeVoice host:

```bash
./tools/download-remote-export.sh all
```

Then use the maintained helper script with an explicit profile selector when you
want to download and flash in one step:

```bash
# Home Assistant Voice Preview Edition
./tools/flash-remote-export.sh pe /dev/ttyACM0

# ESP-BOX-3
./tools/flash-remote-export.sh box /dev/ttyACM0
```

To build and immediately push the new app binary by OTA:

```bash
cd firmware
./build.sh push
```

`push` posts to `http://127.0.0.1:9004/api/firmware/ota/push` by default and reads
the endpoint id from `config/endpoint.yaml`. Override with `OTA_API_BASE` or
`ENDPOINT_ID` when needed. To push the Home Assistant Voice PE firmware, select
that profile and provide the target endpoint id:

```bash
cd firmware
HEXE_BOARD_PROFILE=ha_voice_pe ENDPOINT_ID=ha-voice-1 ./build.sh push
```

To copy the flashable artifacts into `firmware/export/` for another machine:

```bash
cd firmware
./export-artifacts.sh
```

Each flash export includes `provisioning.env.example`. Copy it to
`provisioning.env`, set the node address and Wi-Fi values, then run
`flash-esptool.sh`. The helper flashes that text file as an NVS image at
`0x9000`, so the endpoint can boot with the intended Wi-Fi, backend host, and
identity even after a full erase.

## Wi-Fi Log Monitor

The firmware can mirror ESP logs over UDP while keeping USB serial output enabled.
Enable `debug_log` in `firmware/config/endpoint.yaml`, then listen on the configured host:

```bash
./scripts/monitor-firmware-udp.sh 9010
```

## Current Firmware Status

Implemented today:

- native ESP-IDF app entrypoint
- display initialization and RGB565 screen rendering
- local app state
- button handling
- Wi-Fi station connection
- microphone initialization
- simple energy-threshold VAD that updates local state
- backend client configuration generated from endpoint YAML
- endpoint heartbeat sender
- voice WebSocket client with bounded audio frame queue
- backend event handling for wake/session/transcript/response/TTS/error envelopes
- scaffolded TTS playback state handling
- silent wake-to-listening transition so cue audio does not feed back into VAD/STT

Scaffold-only today:

- backend assistant client
- wake-word module
- STT stream module
- OTA, telemetry, power, and settings runtime behavior

Missing today:

- real TTS audio download/stream playback

See [`docs/firmware-baseline.md`](/home/dan/Projects/HexeVoice/docs/firmware-baseline.md) for the detailed current-state record.
