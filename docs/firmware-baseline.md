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
- Persisted endpoint settings for output volume, mute state, micro VAD pause, endpoint identity, backend host/ports, TLS mode, and optional Wi-Fi credentials: `firmware/main/system/settings.cpp`.
- Wi-Fi station startup and reconnect handling using persisted provisioning settings with local firmware secrets as fallback: `firmware/main/board/wifi.cpp`.
- Button handling for mute/config interactions: `firmware/main/board/buttons.cpp`.
- ESP-BOX-3 touchscreen polling for local volume down/up and mute toggles, reusing persisted endpoint settings and the normal backend heartbeat status.
- Microphone initialization and simple energy-threshold VAD task: `firmware/main/board/audio.cpp`.
- Endpoint-to-node YAML config template: `firmware/config/endpoint.example.yaml`.
- Build-time endpoint config generation from YAML: `firmware/tools/generate_endpoint_config.py` and `firmware/main/CMakeLists.txt`.
- LAN UDP endpoint discovery and pairing with static YAML fallback: `docs/firmware-discovery.md`.
- Backend heartbeat and voice WebSocket client: `firmware/main/voice/backend_client.cpp`.
- Heartbeat capability reporting for touchscreen, SD card, display, audio I/O, provisioning state, command controls, firmware build metadata, and TTS playback lifecycle diagnostics.
- Backend event-to-UX mapping for wake, transcript, response, TTS-ready, completion, cancellation, and error events in `firmware/main/voice/backend_client.cpp`.
- TTS-ready download/playback and stop handling in `firmware/main/voice/tts_player.cpp`, with profile-specific speaker support where available.
- Selectable board profile support in `firmware/main/CMakeLists.txt`. `esp_box_3` remains the default profile, and `ha_voice_pe` adds an experimental Home Assistant Voice Preview Edition profile with I2S microphone input, AIC3204/I2S TTS output, center-button wake/cancel controls, and hardware-mute controls.
- Home Assistant Voice PE LED ring hardware contract: `docs/voice-pe-led-ring.md`.
- Firmware LED ring board API with a no-op non-PE fallback and an RMT-backed `ha_voice_pe` driver for `off`, `set_solid`, and visual-frame rendering.
- Voice PE LED ring voice-state and diagnostic patterns for boot, Wi-Fi/backend connection, disconnected, idle/off, wake/listening, capturing, thinking, replying, completed, muted/privacy, speaker-silent volume, OTA progress, and error states.
- Voice PE rotary dial support: normal rotation adjusts endpoint volume and shows a temporary LED meter; center-held rotation changes the active LED accent color and suppresses the center-button wake/cancel action on release.
- Firmware tracks TTS playback lifecycle as `idle`, `queued`, `started`, `finished`, `failed`, or `stopped`, and reports whether the microphone is currently paused for playback in endpoint heartbeat audio capabilities.
- Firmware has a source-agnostic `stop_playback(reason)` path and accepts backend `playback.stop` commands. Endpoint heartbeats now expose `capabilities.audio.input.playback_interrupt` as `backend_stt_interrupt`, which keeps the microphone open during interruptible playback and lets the backend send `playback.stop` with reason `voice_stop` when stop-only STT matches.
- Firmware still has no local on-device stop-word recognizer. Current profiles report that detail as `local_keyword_reason: missing_on_device_keyword_engine` while keeping the backend-owned interrupt mode available.

## Partial

- VAD updates local app state and display phase, reports `vad.speech_started` with a firmware timestamp when speech begins, queues microphone frames only after the backend voice WebSocket transport is connected, and sends `audio.end` on VAD silence. Raw backend events drive UI phases, and TTS-ready events can download and play WAV output on supported board speaker paths.
- The `ha_voice_pe` profile is headless and reports display, touchscreen, and SD media storage as unavailable. Local TTS playback is available through the onboard AIC3204 speaker path, but SD media playback remains unavailable because this profile has no mounted media storage. The LED ring now covers voice-state, diagnostic, volume, and color-selection affordances.
- TTS playback exists for supported speaker paths, but still needs physical-device validation across profiles for download timing, sample-rate handling, post-playback microphone cooldown, and reconnect/session boundaries.
- Runtime endpoint provisioning is available through backend command envelopes and the operator dashboard; network/backend route changes apply after reboot or reconnect.
- Display states render from native assets, but the UI is still a lightweight state renderer rather than a complete product UI.
- Backend endpoint connection settings are generated from YAML at build time and can be replaced by LAN UDP discovery when enabled.

## Intentional No-Op Ownership Modules

These files remain compiled so firmware has stable ownership/status hooks, but
they are no longer ambiguous scaffolds:

- `firmware/main/voice/wake_word.cpp` reports `backend_streaming`; on-device wake is intentionally unavailable because backend voice streaming owns wake acceptance.
- `firmware/main/voice/stt_stream.cpp` reports `backend_pcm_stream`; firmware captures and sends PCM while backend STT owns decoding.
- `firmware/main/voice/assistant_client.cpp` reports `backend_voice_pipeline`; firmware consumes backend events while assistant turns run on the node.
- `firmware/main/system/telemetry.cpp` reports `heartbeat_capabilities`; endpoint telemetry is carried in heartbeat capabilities rather than a separate firmware telemetry channel.
- `firmware/main/system/power.cpp` reports `board_defaults`; low-power and shutdown commands are intentionally unavailable until a safe per-board power contract exists.
- Heartbeat capabilities expose these decisions under `capabilities.firmware.modules` with `state: "intentional_noop"`, owner, mode, and local availability fields.
- `firmware/main/system/ota.cpp` implements the manual OTA path from backend-pushed, signed `ota.update` events and verifies downloaded bytes before finishing OTA.

## Missing

- Physical-device reconnect and session-boundary validation across supported profiles.

## Current Endpoint Config Contract

For local development, copy:

```bash
cp firmware/config/endpoint.example.yaml firmware/config/endpoint.yaml
```

The local `endpoint.yaml` is gitignored because it contains machine-specific host and port choices.

Firmware version is intentionally not part of endpoint YAML. The firmware reports the ESP-IDF app/project version embedded in the build.

Runtime provisioning can override endpoint identity, display name, backend
host/ports, TLS mode, and Wi-Fi credentials. Reset removes the persisted
provisioning keys and returns to generated endpoint YAML plus local Wi-Fi
secrets. The provisioning flow is documented in
`docs/firmware-provisioning.md`.

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

Automatic endpoint discovery uses UDP offers when `behavior.discovery_enabled`
is true. Static config remains available for constrained networks.

## Next Firmware Work

Firmware implementation should follow the task queue in `docs/New_tasks.txt`:

1. Complete physical reconnect/session-boundary validation.
2. Run physical reconnect/session-boundary bench validation and replace blocked release artifact results.
