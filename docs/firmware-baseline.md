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
- Wi-Fi station startup and reconnect handling using local firmware secrets: `firmware/main/board/wifi.cpp`.
- Button handling for mute/config interactions: `firmware/main/board/buttons.cpp`.
- Microphone initialization and simple energy-threshold VAD task: `firmware/main/board/audio.cpp`.
- Endpoint-to-node YAML config template: `firmware/config/endpoint.example.yaml`.

## Partial

- VAD updates local app state and display phase, but it does not stream audio to the HexeVoice backend yet.
- Wi-Fi connects with compile-time local credentials, but provisioning is not implemented.
- Display states render from native assets, but the UI is still a lightweight state renderer rather than a complete product UI.
- Backend endpoint connection settings exist in YAML form, but firmware code does not consume the YAML or connect to the node yet.

## Scaffold

- `firmware/main/voice/wake_word.cpp` logs scaffold readiness only.
- `firmware/main/voice/stt_stream.cpp` logs scaffold readiness only.
- `firmware/main/voice/tts_player.cpp` logs scaffold readiness only.
- `firmware/main/voice/assistant_client.cpp` logs scaffold readiness only.
- `firmware/main/system/settings.cpp`, `firmware/main/system/telemetry.cpp`, `firmware/main/system/ota.cpp`, and `firmware/main/system/power.cpp` are initialization scaffolds.

## Missing

- Firmware backend client.
- Endpoint heartbeat sender.
- Voice WebSocket/audio chunk sender.
- Backend event handling.
- TTS receive/playback path.
- Settings/provisioning UI.
- OTA implementation beyond scaffold initialization.

## Current Endpoint Config Contract

For local development, copy:

```bash
cp firmware/config/endpoint.example.yaml firmware/config/endpoint.yaml
```

The local `endpoint.yaml` is gitignored because it contains machine-specific host and port choices.

Current expected HexeVoice node backend values:

- `node.host`: LAN host running HexeVoice.
- `node.http_port`: `9004`.
- `node.ws_port`: `9004`.
- `node.heartbeat_path`: `/api/endpoint/heartbeat`.
- `node.voice_ws_path`: `/api/voice/ws`.

Automatic endpoint discovery is deferred until after the first single-endpoint voice loop works.

## Next Firmware Work

Firmware implementation should follow the task queue in `docs/New_tasks.txt`:

1. Use the backend voice event/session contract.
2. Connect to `/api/voice/ws`.
3. Send heartbeat and audio chunks to the node.
4. Receive backend events.
5. Play TTS output and drive endpoint UI state from backend events.
