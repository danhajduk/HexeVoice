# Firmware Baseline

Created: 04/25/2026

## Current Track

HexeVoice firmware is now a native ESP-IDF firmware track under `firmware/`.

The archived ESPHome prototype is preserved at `docs/archive/esphome/Expressif box.yaml` as a historical behavior reference only.

## Implemented

- Native ESP-IDF endpoint app entrypoint: `firmware/apps/endpoint/main/app_main.cpp`.
- Shared app state: `firmware/components/endpoint_runtime/app_state.h` and `firmware/components/endpoint_runtime/app_state.cpp`.
- ESP-BOX-3 BSP display initialization and framebuffer rendering: `firmware/components/endpoint_runtime/board/display.cpp`.
- Branded RGB565 assets for boot, idle, listening, thinking, and error states: `firmware/assets/`.
- NVS initialization: `firmware/components/endpoint_runtime/board/storage.cpp`.
- Persisted endpoint settings for output volume, mute state, micro VAD pause, endpoint identity, backend host/ports, TLS mode, and optional Wi-Fi credentials: `firmware/components/endpoint_runtime/system/settings.cpp`.
- Wi-Fi station startup and reconnect handling using persisted provisioning settings with local firmware secrets as fallback: `firmware/components/endpoint_runtime/board/wifi.cpp`.
- Button handling for mute/config interactions: `firmware/components/endpoint_runtime/board/buttons.cpp`.
- ESP-BOX-3 touchscreen polling for local volume down/up and mute toggles, reusing persisted endpoint settings and the normal backend heartbeat status.
- Microphone initialization and simple energy-threshold VAD task: `firmware/components/endpoint_runtime/board/audio.cpp`.
- Endpoint-to-node YAML config template: `firmware/config/endpoint.example.yaml`.
- Build-time endpoint config generation from YAML: `firmware/tools/generate_endpoint_config.py` and `firmware/components/endpoint_runtime/CMakeLists.txt`.
- LAN UDP endpoint discovery and pairing with static YAML fallback: `docs/firmware-discovery.md`.
- Backend heartbeat and voice WebSocket client: `firmware/components/endpoint_runtime/voice/backend_client.cpp`.
- Heartbeat capability reporting for touchscreen, SD card, display, audio I/O, provisioning state, command controls, firmware build metadata, and TTS playback lifecycle diagnostics.
- Backend event-to-UX mapping for wake, transcript, response, TTS-ready, completion, cancellation, and error events in `firmware/components/endpoint_runtime/voice/backend_client.cpp`.
- TTS-ready download/playback and stop handling in `firmware/components/endpoint_runtime/voice/tts_player.cpp`, with profile-specific speaker support where available.
- Selectable firmware app support in `firmware/CMakeLists.txt` and selectable board profile support in `firmware/components/endpoint_runtime/CMakeLists.txt`. The `endpoint` app and `esp_box_3` profile remain the defaults, and `ha_voice_pe` adds an experimental Home Assistant Voice Preview Edition profile with I2S microphone input, AIC3204/I2S TTS output, center-button wake/cancel controls, and hardware-mute controls.
- Firmware build selection generates board-specific adapter definitions and source lists from `firmware/boards/<profile>/board.yaml` through `firmware/tools/generate_board_profile_config.py`. Planned profiles can exist as non-buildable until their display, touch, audio, storage, and TTS adapters land.
- Firmware board wiring is profile-driven. `firmware/tools/generate_board_profile_config.py` emits `board_profile_pins.h`, `firmware/components/endpoint_runtime/board/pins.h` includes that generated header, and buildable board adapters consume generated pin, bus, and device-address constants instead of keeping dev-board wiring inline.
- Home Assistant Voice PE LED ring hardware contract: `docs/voice-pe-led-ring.md`.
- Firmware LED ring board API with a no-op non-PE fallback and an RMT-backed `ha_voice_pe` driver for `off`, `set_solid`, and visual-frame rendering.
- Voice PE LED ring voice-state and diagnostic patterns for boot, Wi-Fi/backend connection, disconnected, idle/off, wake/listening, capturing, thinking, replying, completed, muted/privacy, speaker-silent volume, OTA progress, and error states.
- Voice PE rotary dial support: normal rotation adjusts endpoint volume and shows a temporary LED meter; center-held rotation changes the active LED accent color and suppresses the center-button wake/cancel action on release.
- Firmware tracks TTS playback lifecycle as `idle`, `queued`, `started`, `finished`, `failed`, or `stopped`, and reports whether the microphone is currently paused for playback in endpoint heartbeat audio capabilities.
- Firmware has a source-agnostic `stop_playback(reason)` path and accepts backend `playback.stop` commands. Endpoint heartbeats expose `capabilities.audio.input.playback_interrupt` with local Stop keyword availability, active state, and `backend_fallback_mode: "backend_stt_interrupt"` so stop-only backend STT can still send `playback.stop` with reason `voice_stop` when local detection is unavailable or missed.
- Firmware now has an experimental on-device Stop keyword provider configured from Kevin Ahrendt's microWakeWord Stop release (`stop.json`/`stop.tflite`, cutoff 0.50, sliding window 5, tensor arena 21000). The Stop JSON/TFLite assets are checked in under `firmware/components/endpoint_runtime/voice/models/`, the TFLite asset is embedded into the firmware image, and the shared per-frame local keyword path calls `stop_playback("voice_stop")` during playback or `cancel_active_session("voice_stop")` during an active voice session.
- Firmware now has an experimental endpoint microWakeWord provider configured for the official ESPHome `alexa` v2 model, treating the spoken wake word `Alexa` as the initial Hexe-compatible local wake path. The Alexa JSON/TFLite assets and audio preprocessor TFLite are checked in under `firmware/components/endpoint_runtime/voice/models/`, embedded into the firmware image, and exposed through heartbeat capability diagnostics.
- Firmware links Espressif's `esp-tflite-micro` runtime and ESP-NN acceleration through `voice/micro_wake_engine.{h,cpp}`. The adapter initializes the embedded int16-to-int8 audio preprocessor plus the Alexa streaming model, runs 40-channel feature slices every 10 ms, reports privacy-safe feature/inference/detection counters and probability telemetry in heartbeats, and emits endpoint wake candidates through the existing wake-election path when the sliding probability window crosses the configured cutoff.
- Firmware keeps backend openWakeWord as the fallback wake provider. Endpoint-local wake detections enter listening mode and stream post-wake audio immediately after submitting the backend wake candidate, while backend stand-down events can still cancel a losing endpoint during arbitration. If local readiness is false or the endpoint election wait times out, the endpoint continues backend streaming behavior.
- Firmware board-profile schema and examples live under `firmware/boards/`, with validation from `firmware/tools/validate_board_profiles.py`. The initial profile set covers `ha_voice_pe`, `esp_box_3`, V2-only `waveshare_s3_touch_lcd_1_85c_box_v2`, and `waveshare_p4_wifi6_touch_lcd_7b`.

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

- `firmware/components/endpoint_runtime/voice/wake_word.cpp` reports `endpoint_micro_wake_word_experimental` when the native preprocessor and Alexa runtime are ready, otherwise `backend_streaming_with_micro_wake_word_manifest`. The configured primary model is the official ESPHome `alexa` v2 model, exposed as alias `Hexe`, and backend openWakeWord remains the fallback.
- `firmware/components/endpoint_runtime/voice/stt_stream.cpp` reports `backend_pcm_stream`; firmware captures and sends PCM while backend STT owns decoding.
- `firmware/components/endpoint_runtime/voice/assistant_client.cpp` reports `backend_voice_pipeline`; firmware consumes backend events while assistant turns run on the node.
- `firmware/components/endpoint_runtime/system/telemetry.cpp` reports `heartbeat_capabilities`; endpoint telemetry is carried in heartbeat capabilities rather than a separate firmware telemetry channel.
- `firmware/components/endpoint_runtime/system/power.cpp` reports `board_defaults`; low-power and shutdown commands are intentionally unavailable until a safe per-board power contract exists.
- Heartbeat capabilities expose these decisions under `capabilities.firmware.modules` with `state: "intentional_noop"`, owner, mode, and local availability fields.
- Heartbeat capabilities also expose the compiled firmware contract:
  `application_type`, `board_profile`, `soc`/`idf_target`, `flash_size`,
  `psram_size`, `partition_schema`, `app_slot_size`, and firmware/model/asset/
  calibration API version fields. The dashboard uses those values, falling back
  to the backend release manifest when an endpoint has not reported them yet.
- `firmware/components/endpoint_runtime/system/ota.cpp` implements the manual OTA path from backend-pushed, signed `ota.update` events and verifies downloaded bytes before finishing OTA.

## Missing

- Physical-device reconnect and session-boundary validation across supported profiles.

## Current Endpoint Config Contract

Board profiles describe compile-time hardware capabilities and are documented in
`docs/firmware-board-profile-schema.md`. The local endpoint config remains the
per-device connection and identity layer.

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
