# Phase 2 Operator Release Checklist

This runbook is the Phase 2 path from a blank Voice Node host and prepared
endpoint card to a working endpoint. It intentionally links to the deeper docs
where the details already live, while keeping the release checklist in one
place.

## Target State

Phase 2 is releasable when:

- a fresh host can install HexeVoice with the hosted installer
- setup can finish as a new node or migration without carrying trust secrets
- STT, TTS, wake, firmware artifacts, and endpoint media have known runtime
  locations
- the firmware can be built, flashed once over USB when needed, and pushed by
  OTA after that
- the dashboard can see the endpoint, control audio/session actions, deliver SD
  media, and push firmware OTA
- a real endpoint can complete a wake/button to STT to response to TTS playback
  smoke test

## Host Bring-Up

Start on the target host:

```bash
curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | bash
```

If the host is missing Debian/Ubuntu prerequisites, either allow the installer
to install them:

```bash
curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_INSTALL_SYSTEM_PACKAGES=true bash
```

or print the commands to run manually:

```bash
curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_PRINT_PREREQ_COMMANDS=true bash
```

Expected install outputs:

- checkout at `~/hexe/HexeVoice`
- runtime directory skeleton from `config/runtime-dirs.json`
- temporary setup UI on `http://<host>:8180/setup/host`
- production setup UI on `http://<host>:8084/setup/host`
- default STT/TTS/wake asset readiness checks surfaced in Host Setup
- firmware artifacts in `runtime/firmware` when configured or downloaded

Use the production setup flow:

1. Host and Node Setup
2. Core Connection
3. Migration Import when migrating, otherwise New Node Onboarding
4. Migration Re-auth when migrating, otherwise normal Core onboarding approval
5. Provider Setup
6. Capabilities and Governance
7. Ready Check

At Step 7, run the full smoke test, acknowledge non-blocking warnings, export
the final setup bundle, then complete setup. After completion, verify lifecycle
state:

```bash
./scripts/verify-post-complete-lifecycle.py
./scripts/verify-post-complete-lifecycle.py --restart-stack
```

## Migration Path

For an existing node, export a migration bundle from the old node dashboard or
API. The bundle includes onboarding state, endpoint registry, voice intents,
STT/TTS/wake settings, and safe provider metadata. It does not include trust
tokens or secrets.

Before import on the new host:

```bash
./scripts/migration-preflight.py migration-bundle.json \
  --core-url http://<core-host>:9001 \
  --api-url http://<voice-host>:9004 \
  --ui-url http://<voice-host>:8084
```

Imported migrated nodes must complete Core re-auth before provider setup.

## Backend Provider Configuration

Use Provider Setup unless a manual env override is being tested. The release
default is:

- STT: external faster-whisper, default model `base.en` or selected profile,
  CPU `int8` unless CUDA preflight passes
- TTS: Piper, default voice `en_US-kathleen-low.onnx`
- Wake: openWakeWord/supervised openWakeWord with the `Hexe` wake model

Useful provider checks:

```bash
./scripts/faster-whisper-stt-control.sh doctor
./scripts/faster-whisper-stt-control.sh health
./scripts/piper-tts-control.sh health
./scripts/openwakeword-control.sh health
./scripts/faster-whisper-stt-control.sh cuda-preflight
```

For a CUDA target, run the CUDA preflight before selecting a CUDA STT profile.
In `auto` mode the setup keeps the CPU image/profile when Docker GPU passthrough
or the CUDA faster-whisper image check fails.

## Endpoint Wiring And SPI SD

For ESP-BOX-3 style hardware with the external SPI microSD reader, wire:

- `MISO` to `G9`
- `MOSI` to `G14`
- `SCK` to `G11`
- `CS` to `G12`
- `VCC` to `3V3`
- `GND` to `GND`

Use a FAT32 card, preferably 16 GB or 32 GB. Firmware creates and repairs:

- `/sdcard/hexe/pictures`
- `/sdcard/hexe/sprites`
- `/sdcard/hexe/sounds`

The Home Assistant Voice Preview Edition profile is headless and reports SD,
display, and touchscreen unavailable.

## Image And Sound Assets

Endpoint UI media is SD-card driven. Backgrounds go to `pictures`, sprites and
`ui_manifest.json` go to `sprites`, and cue WAV files go to `sounds`.

Convert full-screen pictures to RGB565:

```bash
python3 firmware/tools/convert_image.py input.png output.rgb565
```

The image converter requires Pillow; use the ESP-IDF Python environment or
install it into the Python environment you use for asset conversion.

Convert sprites with an alpha mask:

```bash
firmware/tools/convert-sprite.sh --size 160x160 avatar.png runtime/endpoint_media/sprites
```

The dashboard Voice Endpoint media panel can upload picture, sprite, and sound
assets, deliver them to a connected endpoint SD card, inspect reported SD
inventory, and send the media-only reformat command.

## Firmware Build, USB Flash, And OTA

Install/load ESP-IDF first. The build script auto-loads `~/esp-idf/export.sh`
when `IDF_PATH` is not already set.

Build both supported profiles:

```bash
cd firmware
./build.sh
```

Build one profile:

```bash
cd firmware
HEXE_BOARD_PROFILE=esp_box_3 ./build.sh
HEXE_BOARD_PROFILE=ha_voice_pe ./build.sh
```

If the endpoint has not yet received the OTA partition table, do one full USB
flash from the profile export folder:

```bash
cd firmware/export
./flash-esptool.sh /dev/ttyACM0
```

From another machine, pull and flash the export:

```bash
cd firmware
./tools/flash-remote-export.sh pe /dev/ttyACM0
./tools/flash-remote-export.sh box /dev/ttyACM0
```

After the first full flash, OTA can push the app binary through the backend:

```bash
cd firmware
HEXE_BOARD_PROFILE=esp_box_3 ENDPOINT_ID=esp-box-1 ./build.sh push
HEXE_BOARD_PROFILE=ha_voice_pe ENDPOINT_ID=esp-pe-1 ./build.sh push
```

The dashboard Voice Endpoint page can also send OTA when the endpoint is
connected and `runtime/firmware` contains a matching artifact and manifest.

## Dashboard Endpoint Controls

After setup completion, open `http://<voice-host>:8084/#/dashboard/voice-endpoint`.

Expected Phase 2 controls:

- endpoint cards with connection, firmware, storage, audio, and latency details
- endpoint metadata save for display name and zone
- volume and mute commands
- cancel current session
- replay latest eligible TTS response or selected session
- speak text to the endpoint through active TTS provider
- delete retained endpoint wake/TTS artifacts
- upload and deliver SD media assets
- reformat endpoint media directories
- push firmware OTA

## Troubleshooting

Wake:

- confirm the wake provider is enabled in Provider Setup
- run `./scripts/openwakeword-control.sh health`
- confirm `runtime/openwakeword/models/Hexe*` exists or re-run the wake model
  download action
- inspect wake recordings under `runtime/wake_recordings` when recording is
  enabled

STT:

- run `./scripts/faster-whisper-stt-control.sh health`
- check `reload_required` in `/api/services/status` when changing models
- use CPU `int8` first, then CUDA only after `cuda-preflight` passes
- benchmark a representative clip with `scripts/benchmark-stt.py`

TTS:

- run `./scripts/piper-tts-control.sh health`
- confirm the selected `.onnx` and `.onnx.json` files exist under
  `runtime/piper-tts/models`
- verify endpoint sample rate mappings for ESP-BOX versus HA Voice PE
- use the dashboard speak/replay controls to separate TTS synthesis failures
  from endpoint playback failures

SD media:

- confirm firmware heartbeat reports storage and media inventory
- use the dashboard inventory panel to verify files landed in `pictures`,
  `sprites`, or `sounds`
- reformat only the media directories from the dashboard if stale test files
  block validation

Touch:

- ESP-BOX touch controls depend on `ui_manifest.json` sprite `touch` rectangles
- keep touch targets around 50-56 px until the dedicated touchscreen task lands
- HA Voice PE is button/headless only

Firmware/OTA:

- one full USB flash is required after partition-table changes
- use `runtime/firmware/manifest*.json` plus the profile-named `.bin` for OTA
- open the dashboard endpoint card and confirm firmware update metadata before
  pushing OTA

## Release Checklist

Before tagging or publishing a Phase 2 candidate:

- backend tests pass:
  `./.venv/bin/pytest tests/test_api.py tests/test_phase2.py tests/test_setup_ready.py tests/test_node_ui_pilot.py`
- frontend build passes:
  `cd frontend && npm run build`
- setup docs reflect the current installer/setup flow:
  `docs/setup.md`, `docs/operations.md`, and this checklist
- firmware builds both profiles:
  `cd firmware && ./build.sh`
- firmware artifacts verify:
  `./scripts/firmware-artifacts-control.sh verify`
- a device that needs the OTA partition table has been USB-flashed once
- OTA push succeeds for the target profile:
  `cd firmware && HEXE_BOARD_PROFILE=<profile> ENDPOINT_ID=<endpoint-id> ./build.sh push`
- provider health passes:
  `./scripts/faster-whisper-stt-control.sh health`,
  `./scripts/piper-tts-control.sh health`,
  `./scripts/openwakeword-control.sh health`
- post-install smoke passes:
  `./scripts/post-install-smoke-test.py --check-host-alias`
- post-complete lifecycle verifier passes:
  `./scripts/verify-post-complete-lifecycle.py --restart-stack`
- real-device smoke test passes:
  wake or center-button starts a session, STT returns the expected transcript,
  the assistant emits a response, TTS plays on the endpoint, dashboard latency
  updates, and endpoint heartbeat remains connected
- known hardware/profile limitations are recorded before release
