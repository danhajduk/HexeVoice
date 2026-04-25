# Hexe Firmware

This directory is the native standalone firmware track for Hexe.

It replaces the ESPHome prototype as the active firmware track while preserving the prototype as a historical behavior reference:

- [`docs/archive/esphome/Expressif box.yaml`](/home/dan/Projects/HexeVoice/docs/archive/esphome/Expressif%20box.yaml)
- [`docs/firmware-baseline.md`](/home/dan/Projects/HexeVoice/docs/firmware-baseline.md)
- [`docs/firmware-migration-plan.md`](/home/dan/Projects/HexeVoice/docs/firmware-migration-plan.md)
- [`docs/firmware-ota.md`](/home/dan/Projects/HexeVoice/docs/firmware-ota.md)

## Goals

- standalone Hexe device behavior
- direct ownership of UI, wake word, audio, networking, and OTA
- no required Home Assistant dependency

## Layout

- `main/`
  Native app entrypoint and modules
- `assets/`
  Shared firmware assets reference area
- `config/endpoint.example.yaml`
  Local endpoint-to-node connection example. Copy it to `config/endpoint.yaml` for machine-specific backend host and port values.

## Endpoint Node Config

For the first voice-loop implementation, endpoint discovery is intentionally deferred. Configure the HexeVoice node backend explicitly with:

```bash
cp firmware/config/endpoint.example.yaml firmware/config/endpoint.yaml
```

Then edit `firmware/config/endpoint.yaml` so `node.host`, `node.http_port`, and `node.ws_port` point at the machine running the HexeVoice backend. The local `endpoint.yaml` file is gitignored because it is machine-specific.

## Next Build Step

Once ESP-IDF is installed locally, the intended workflow is:

```bash
cd firmware
./build.sh
```

To copy the flashable artifacts into `firmware/export/` for another machine:

```bash
cd firmware
./export-artifacts.sh
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

Scaffold-only today:

- backend assistant client
- wake-word module
- STT stream module
- TTS player module
- OTA, telemetry, power, and settings runtime behavior

Missing today:

- endpoint heartbeat sender
- backend WebSocket connection
- audio chunk upload
- backend event handling
- TTS receive/playback

See [`docs/firmware-baseline.md`](/home/dan/Projects/HexeVoice/docs/firmware-baseline.md) for the detailed current-state record.
