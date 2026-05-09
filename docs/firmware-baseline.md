# Firmware Baseline

Created: 04/25/2026

## Current Track

HexeVoice firmware is now a native ESP-IDF firmware track under `firmware/`.

The archived ESPHome prototype is preserved at `docs/archive/esphome/Expressif box.yaml` as a historical behavior reference only.

## Implemented

- Native ESP-IDF app entrypoint: `firmware/main/app_main.cpp`.
- Shared app state: `firmware/main/app_state.h` and `firmware/main/app_state.cpp`.
- ESP-BOX-3 BSP display initialization and framebuffer rendering: `firmware/main/board/display.cpp`.
- Branded RGB565 assets for boot, idle, listening, thinking, and error states: `firmware/main/assets/`.
- NVS initialization: `firmware/main/board/storage.cpp`.
- Persisted endpoint settings for output volume and mute state: `firmware/main/system/settings.cpp`.
- Wi-Fi station startup and reconnect handling using local firmware secrets: `firmware/main/board/wifi.cpp`.
- Button handling for mute/config interactions: `firmware/main/board/buttons.cpp`.
- Microphone initialization and simple energy-threshold VAD task: `firmware/main/board/audio.cpp`.
- Endpoint-to-node YAML config template: `firmware/config/endpoint.example.yaml`.
- Build-time endpoint config generation from YAML: `firmware/tools/generate_endpoint_config.py` and `firmware/main/CMakeLists.txt`.
- Backend heartbeat and voice WebSocket client scaffold: `firmware/main/voice/backend_client.cpp`.
- Heartbeat capability reporting for touchscreen, SD card, display, audio I/O, command controls, and firmware build metadata.
- Backend event-to-UX mapping for wake, transcript, response, TTS-ready, completion, cancellation, and error events in `firmware/main/voice/backend_client.cpp`.
- TTS-ready playback scaffold and stop handling in `firmware/main/voice/tts_player.cpp`.
- Selectable board profile support in `firmware/main/CMakeLists.txt`. `esp_box_3` remains the default profile, and `ha_voice_pe` adds an experimental Home Assistant Voice Preview Edition profile with I2S microphone input, AIC3204/I2S TTS output, center-button wake/cancel controls, and hardware-mute controls.
- Home Assistant Voice PE LED ring hardware contract: `docs/voice-pe-led-ring.md`.
- Firmware LED ring board API with a no-op non-PE fallback and an RMT-backed `ha_voice_pe` driver for `off`, `set_solid`, and visual-frame rendering.

## Partial

- VAD updates local app state and display phase, microphone frames are queued for the backend voice WebSocket, and VAD silence sends `audio.end`. Raw backend events drive UI phases, and TTS-ready events can download and play WAV output on supported board speaker paths.
- The `ha_voice_pe` profile is headless and reports display, touchscreen, and SD media storage as unavailable. Local TTS playback is available through the onboard AIC3204 speaker path, but SD media playback remains unavailable because this profile has no mounted media storage. The LED ring driver is initialized, but voice-phase pattern mapping is still pending firmware work.
- Wi-Fi connects with compile-time local credentials, but provisioning is not implemented.
- Display states render from native assets, but the UI is still a lightweight state renderer rather than a complete product UI.
- Backend endpoint connection settings are generated from YAML at build time. Automatic discovery is still deferred.

## Scaffold

- `firmware/main/voice/wake_word.cpp` logs scaffold readiness only.
- `firmware/main/voice/stt_stream.cpp` logs scaffold readiness only.
- `firmware/main/voice/assistant_client.cpp` logs scaffold readiness only.
- `firmware/main/system/telemetry.cpp` and `firmware/main/system/power.cpp` are initialization scaffolds.
- `firmware/main/system/ota.cpp` implements the first manual OTA path from backend-pushed `ota.update` events.

## Missing

- Real TTS audio download or stream playback path.
- Settings/provisioning UI.
- Firmware-side SHA-256 enforcement and signed manifest validation for OTA.

## Current Endpoint Config Contract

For local development, copy:

```bash
cp firmware/config/endpoint.example.yaml firmware/config/endpoint.yaml
```

The local `endpoint.yaml` is gitignored because it contains machine-specific host and port choices.

Firmware version is intentionally not part of endpoint YAML. The firmware reports the ESP-IDF app/project version embedded in the build.

Current expected HexeVoice node backend values:

- `endpoint.id`: endpoint id sent in heartbeat and voice envelopes.
- `node.host`: LAN host running HexeVoice.
- `node.http_port`: `9004`.
- `node.ws_port`: `9004`.
- `node.heartbeat_path`: `/api/endpoint/heartbeat`.
- `node.voice_ws_path`: `/api/voice/ws`.
- `audio.encoding`: `pcm_s16le`.
- `audio.sample_rate_hz`: `16000`.
- `audio.channels`: `1`.
- `audio.chunk_samples`: microphone chunk size sent to the backend.

Automatic endpoint discovery is deferred until after the first single-endpoint voice loop works.

## Next Firmware Work

Firmware implementation should follow the task queue in `docs/New_tasks.txt`:

1. Use the backend voice event/session contract.
2. Replace TTS-ready scaffold logging with real audio download or stream playback.
3. Harden reconnect/session-boundary behavior after device testing.
