# Hexe Firmware Migration Plan

See also: [Firmware OTA Plan](/home/dan/Projects/HexeVoice/docs/firmware-ota.md)

## Goal

Move Hexe from a fast ESPHome prototype on the ESP32-S3 Box into a true standalone Espressif firmware while preserving the behavior we already proved out.

ESPHome got the box working quickly, which was the right choice for prototyping. The next step is to treat the current ESPHome config as the reference behavior and migrate toward a native `ESP-IDF` firmware that is not shaped around Home Assistant concepts.

## Recommendation

Use a two-track approach:

1. Keep the current ESPHome configuration as the working prototype and behavior reference.
2. Build a new native firmware branch in `ESP-IDF` for the real Hexe device experience.

This avoids rewriting blindly. We can compare the native build against a device behavior that already works.

## Why Move Away From ESPHome

ESPHome was the fastest path to a working device, but it becomes limiting if Hexe is meant to be a standalone product:

- wake-word and assistant flows are still framed by Home Assistant-oriented abstractions
- advanced product UX is harder to shape cleanly
- custom provisioning, recovery, and settings flows are more constrained
- richer animation and rendering control is limited
- deeper ownership of audio, networking, latency, and power behavior is easier in native firmware

## Why Keep ESPHome For Now

The current ESPHome setup is still valuable:

- it proves the hardware pinout and audio path
- it proves the UI states and device behavior
- it gives us a fast fallback while native firmware is under development
- it acts as the clearest acceptance reference for the standalone rewrite

## Migration Principle

Treat the current ESPHome YAML as a behavior spec, not the final implementation.

That means we preserve the device contract:

- boot and loading behavior
- mute and button behavior
- wake-word mode behavior
- assistant connection states
- display states and artwork
- timer and error handling

## Phase 1: Freeze The Behavior Contract

Before rewriting, document and preserve:

- hardware pins
- display layout and screen states
- audio sampling and playback settings
- button interactions
- mute semantics
- startup timing
- error states
- OpenWakeWord and on-device wake-word mode expectations

The current `docs/Expressif box.yaml` should remain the working prototype source during this phase.

## Phase 2: Create Native Firmware Skeleton

Start a parallel firmware workspace based on `ESP-IDF`.

Suggested structure:

```text
firmware/
  CMakeLists.txt
  sdkconfig.defaults
  main/
    app_main.cpp
    app_state.h
    app_state.cpp
    board/
      pins.h
      audio.cpp
      audio.h
      display.cpp
      display.h
      buttons.cpp
      buttons.h
      wifi.cpp
      wifi.h
      storage.cpp
      storage.h
    ui/
      screens.cpp
      screens.h
      animator.cpp
      animator.h
      theme.h
    voice/
      wake_word.cpp
      wake_word.h
      stt_stream.cpp
      stt_stream.h
      tts_player.cpp
      tts_player.h
      assistant_client.cpp
      assistant_client.h
    system/
      settings.cpp
      settings.h
      ota.cpp
      ota.h
      telemetry.cpp
      telemetry.h
      power.cpp
      power.h
    assets/
      logo.png
      icons/
```

## Responsibilities By Module

`board/`

- owns pins, peripherals, drivers, and board-specific setup
- initializes display, I2S audio, buttons, storage, and connectivity

`ui/`

- owns screen rendering, animation timing, branded states, and visual transitions
- should become the home of the Hexe loading animation, idle screen, mute screen, and error presentation

`voice/`

- owns wake word, streaming audio, backend assistant requests, and TTS playback
- should connect directly to Hexe backend services rather than Home Assistant flows

`system/`

- owns OTA, local settings, diagnostics, and operational housekeeping

`app_state`

- coordinates device state transitions across UI, voice, connectivity, and system health

## Recommended Port Order

Port features in this order:

1. Boot screen and loading animation
2. Display state machine
3. Button handling and local mute
4. Speaker and microphone bring-up
5. Wi-Fi provisioning
6. Backend connection and health status
7. Wake word
8. STT/TTS streaming
9. Settings, OTA, and recovery flow

This order gets a visible, testable Hexe device on-screen early, before the full voice pipeline is finished.

## What To Rebuild First

The first features worth translating out of the ESPHome YAML are:

- the assistant phase/state machine
- display rendering
- the loading logo animation
- timer and mute presentation
- button behavior
- wake-word mode switching
- audio device configuration

These are the parts that most strongly define the Hexe device personality.

## UI Strategy

Two realistic options:

### Option A: Lightweight Custom Renderer

Use direct drawing routines for the S3 Box display.

Pros:

- lower overhead
- simple and fast
- good for a focused product UI

Cons:

- more manual layout work
- more custom animation code

### Option B: LVGL-Based UI

Use `LVGL` for a richer and more flexible UI layer.

Pros:

- easier layout composition
- easier animation primitives
- easier future settings and menus

Cons:

- more complexity
- more memory and framework overhead

Recommendation:

Start with a lightweight custom renderer if Hexe stays minimal and voice-first. Use `LVGL` if you expect settings pages, onboarding flows, touch-heavy screens, or richer product UI.

## Voice Strategy

Since Hexe is not intended to depend on Home Assistant, the native firmware should talk directly to Hexe services.

Likely responsibilities:

- wake-word detection mode selection
- microphone stream capture
- backend STT/TTS request lifecycle
- playback interruption rules
- mute behavior
- reconnect and retry logic

Wake-word options can remain conceptually the same:

- `OpenWakeWord` for server-side wake-word handling
- `On device` for local wake-word handling

The difference is that native firmware should express those as Hexe product modes, not Home Assistant-specific modes.

## Networking And Provisioning

A native firmware version should eventually own its own:

- Wi-Fi provisioning
- saved credentials
- backend endpoint configuration
- secure token storage
- OTA update path

That will make the box feel like a standalone device rather than a dependent node.

## Suggested Milestones

### Milestone 1: Native Shell

- boot into a branded Hexe loading screen
- render logo animation
- show Wi-Fi and backend status
- support button input

### Milestone 2: Audio Bring-Up

- initialize microphone and speaker
- play local sounds
- verify audio capture and playback stability

### Milestone 3: Backend Voice Round Trip

- connect to Hexe backend
- stream microphone input
- receive and play spoken response

### Milestone 4: Product Readiness

- provisioning
- settings
- OTA
- recovery flow
- diagnostics

## Practical Development Model

Use the current ESPHome implementation as the reference track while native firmware matures:

- ESPHome remains the working prototype
- native firmware becomes the product track
- behavior parity is validated screen-by-screen and flow-by-flow

Do not delete the ESPHome config until the native firmware can:

- boot reliably
- render the same core states
- capture microphone input
- play TTS output
- handle wake-word flow
- recover cleanly from connection loss

## Immediate Next Step

The best next step is to create the native firmware scaffold inside this repo under `firmware/` and begin with:

1. application state definitions
2. display driver bring-up
3. Hexe loading screen and animation
4. button and mute handling

That would give the project a real standalone firmware foundation without giving up the working prototype during the transition.
