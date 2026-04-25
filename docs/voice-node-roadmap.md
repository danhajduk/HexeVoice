# Voice Node Roadmap

## Purpose

This document is a working roadmap for turning HexeVoice from a strong onboarding scaffold into a real voice-runtime node.

It is intentionally written as a brainstorming artifact, not a locked implementation spec.

## Current State Snapshot

HexeVoice already has a solid foundation in three areas:

- backend onboarding, trust, provider setup, capability declaration, governance sync, and readiness projection
- frontend setup and dashboard surfaces for the canonical 10-step node lifecycle
- ESP32 firmware bring-up for display, buttons, Wi-Fi, microphone access, and simple VAD state changes

The current gap is that the actual voice runtime is still mostly placeholder code.

### What Exists Today

Backend:

- `POST /api/assistant/turn` supports a minimal text-in/text-out conversation stub for device integration
- endpoint heartbeat/status exists as a lightweight in-memory tracker
- node readiness, onboarding, trust, and capability APIs are already much more mature than the voice pipeline itself

Frontend:

- the dashboard already reserves space for `Speech Pipeline` and `Device Sessions`
- onboarding and operational UI are present, but voice-specific observability is still placeholder content

Firmware:

- audio input is initialized and a simple energy-based VAD loop is running in [`firmware/main/board/audio.cpp`](/home/dan/Projects/HexeVoice/firmware/main/board/audio.cpp:1)
- wake word, STT, TTS, and assistant client modules are only scaffold logs today:
  - [`firmware/main/voice/wake_word.cpp`](/home/dan/Projects/HexeVoice/firmware/main/voice/wake_word.cpp:1)
  - [`firmware/main/voice/stt_stream.cpp`](/home/dan/Projects/HexeVoice/firmware/main/voice/stt_stream.cpp:1)
  - [`firmware/main/voice/tts_player.cpp`](/home/dan/Projects/HexeVoice/firmware/main/voice/tts_player.cpp:1)
  - [`firmware/main/voice/assistant_client.cpp`](/home/dan/Projects/HexeVoice/firmware/main/voice/assistant_client.cpp:1)

### Main Reality Check

HexeVoice is currently better described as:

- a complete node setup and lifecycle shell
- a partial endpoint/device integration scaffold
- an incomplete speech pipeline

That is a good place to be, because the trust and operator surfaces are already ahead of the runtime.

## Product Goal

HexeVoice should become a trusted multi-endpoint voice transport and orchestration node that:

- receives audio from endpoint devices
- detects wake events and active speech
- captures utterances cleanly
- resolves simple local commands without upstream dependency
- forwards conversational turns upstream when needed
- synthesizes replies
- sends audio and state updates back to the originating endpoint
- exposes enough runtime telemetry that operators can understand what the node is doing in real time

## Target Architecture Direction

The cleanest direction appears to be:

1. Keep the Python backend as the authoritative voice session orchestrator.
2. Treat firmware as a native endpoint runtime, not the primary intelligence host.
3. Move from the current `text turn` contract to a real endpoint session contract with streaming or chunked audio transport.
4. Keep simple local commands and local fail-safe behaviors available even when the full upstream path is degraded.

This fits the current codebase better than pushing the full assistant pipeline down into ESP32 firmware.

## Roadmap

## Phase 1: Define The Real Endpoint Contract

Goal:
Replace the current heartbeat + text-turn prototype with a proper endpoint session model.

Deliverables:

- define endpoint registration and session lifecycle contracts
- define device states beyond `idle/listening/thinking/speaking/offline`
- define audio transport shape:
  - push-to-server PCM chunks
  - WebSocket stream
  - HTTP chunk upload with response stream
- define server responses for:
  - wake accepted
  - listening started
  - transcript partial/final
  - local command handled
  - upstream turn pending
  - TTS playback ready
  - stop/cancel
- persist endpoint metadata rather than keeping it only in memory

Suggested backend work:

- expand [`src/hexevoice/api/models.py`](/home/dan/Projects/HexeVoice/src/hexevoice/api/models.py:1) with endpoint session and audio event models
- grow [`src/hexevoice/endpoint/service.py`](/home/dan/Projects/HexeVoice/src/hexevoice/endpoint/service.py:1) into a real endpoint registry/session manager
- add restart-safe persistence for endpoint registry and recent session state

## Phase 2: Build The Server-Side Voice Pipeline

Goal:
Make the backend own the actual wake -> capture -> transcribe -> decide -> respond loop.

Deliverables:

- per-endpoint session state machine
- wake detection boundary and cooldown handling
- utterance capture windows driven by VAD
- STT integration
- deterministic local command interpreter
- upstream assistant routing boundary
- TTS generation boundary
- per-session event log for diagnostics

Suggested module shape:

- `voice/session_manager.py`
- `voice/transport.py`
- `voice/vad.py`
- `voice/stt.py`
- `voice/commands.py`
- `voice/router.py`
- `voice/tts.py`

Suggested MVP order:

1. session state machine
2. recorded-audio-to-STT flow
3. local command path
4. upstream text routing
5. TTS response generation
6. live streaming improvements

## Phase 3: Firmware As A Reliable Native Endpoint

Goal:
Turn the ESP32 firmware into a stable, operator-friendly endpoint runtime.

Deliverables:

- stable Wi-Fi reconnect and backend reconnect behavior
- endpoint identity and provisioning flow
- microphone capture pipeline aligned with backend contract
- speaker playback pipeline for TTS audio
- button-driven fallback controls such as stop, retry, and mute
- on-device state transitions and animations
- bounded buffering, retry, and watchdog behavior

Firmware focus areas:

- keep VAD on-device when it improves latency and transport cost
- avoid placing heavyweight STT/LLM logic on-device for MVP
- use firmware mainly for capture, playback, wake UX, and transport resilience

Important note:

The current VAD in [`firmware/main/board/audio.cpp`](/home/dan/Projects/HexeVoice/firmware/main/board/audio.cpp:1) is useful as an early signal, but it is not yet a full utterance segmentation strategy. We should treat it as a starting point rather than the final design.

## Phase 4: Multi-Endpoint Scheduling And Arbitration

Goal:
Support more than one active voice endpoint cleanly.

Deliverables:

- endpoint registry with `endpoint_type`, `zone_id`, `display_name`, and `priority`
- collision window handling for near-simultaneous wake detections
- same-zone arbitration rules
- different-zone concurrent session policy
- cooldown handling for losing endpoints
- rate limits and fairness controls

This phase should follow the single-endpoint MVP, but the backend contract should be designed now so we do not paint ourselves into a corner.

## Phase 5: Operator Visibility And Controls

Goal:
Make the voice node debuggable in production.

Deliverables:

- live speech pipeline card replacing the current dashboard placeholder
- per-endpoint health and last-seen views
- recent session timeline with wake, transcript, routing, and playback stages
- explicit failure states for STT, TTS, upstream routing, and endpoint transport
- manual actions:
  - stop current session
  - replay latest response
  - mute endpoint
  - refresh endpoint connection

Suggested frontend targets:

- replace placeholders in [`frontend/src/features/dashboard/VoiceEndpointDashboardSection.jsx`](/home/dan/Projects/HexeVoice/frontend/src/features/dashboard/VoiceEndpointDashboardSection.jsx:1)
- add dedicated cards for active sessions, transport health, and speech pipeline stages

## Phase 6: Hardening, Privacy, And Operations

Goal:
Make the node safe to run continuously.

Deliverables:

- retention policy for transcripts and audio artifacts
- redaction strategy for logs and diagnostics
- crash-safe session cleanup
- bounded queues and backpressure
- service health probes for STT/TTS/upstream dependencies
- degraded-mode behavior when one subsystem is unavailable
- OTA-safe voice-session behavior for firmware

This phase is where the project moves from “works in the lab” to “safe to leave running.”

## Recommended MVP Cut

If we want the fastest path to a believable first Voice node, the MVP should be:

- one native ESP32 endpoint
- backend-owned session orchestration
- on-device VAD plus backend-managed utterance/session flow
- local command handling for `status`, `repeat`, and `stop`
- upstream text request/response path
- backend-generated TTS returned to the endpoint
- operator dashboard for active state, recent transcript, and last error

Avoid adding these too early:

- multi-endpoint arbitration
- speaker identification
- advanced personalization
- heavy on-device inference
- broad provider matrix

## Backlog By Workstream

### Backend

- introduce a dedicated `voice/` package instead of keeping voice behavior inside the minimal assistant stub
- replace in-memory endpoint records with persistence-backed endpoint registry state
- define a session event model for observability and UI consumption
- add integration tests for endpoint lifecycle, command handling, and degraded-path behavior

### Frontend

- replace dashboard placeholders with live voice cards
- show endpoint roster and active session state
- expose actionable error messages instead of generic readiness-only views
- add operator controls for stop, retry, mute, and refresh

### Firmware

- implement real backend transport in `assistant_client`
- add speaker output path for synthesized replies
- align microphone chunking with backend transport contract
- make UI states reflect listening, thinking, speaking, muted, and fault conditions

### Documentation

- create a formal endpoint protocol spec after Phase 1 decisions
- document privacy and retention decisions before production audio logging exists
- document local development workflows for backend + firmware loop testing

## Risks And Decision Points

These are the biggest design choices still open:

1. Should wake-word detection live on-device, on the backend, or support both modes?
2. What transport should carry audio between device and backend?
3. Should transcripts and recent turns be persisted, and for how long?
4. Do we want endpoint sessions to survive backend restarts, or only endpoint registration?
5. Is HexeVoice expected to host STT/TTS directly, or broker to local provider services?

My current recommendation:

- backend hosts orchestration and likely STT/TTS boundaries
- firmware handles capture, playback, local UX, and resilience
- wake can start with on-device gating plus backend session control
- persistence should start with endpoint registry + recent event metadata, not raw audio retention

## Brainstorm Prompts

These are good next questions for us to answer together:

- What is the first real endpoint we care about: ESP Box only, or ESP Box plus Home Assistant Voice from day one?
- Do we want “push to talk” as an interim mode before full wake-word support?
- Should the first transport be HTTP, WebSocket, or MQTT-based?
- Do we want partial transcripts in the operator UI, or only final transcripts?
- What counts as a successful MVP demo?

## Suggested Immediate Next Steps

1. Write the Phase 1 endpoint/session protocol doc.
2. Decide the first audio transport.
3. Implement a persistence-backed endpoint registry.
4. Replace `POST /api/assistant/turn` with a session-oriented API shape.
5. Build a single-endpoint end-to-end happy path before expanding the dashboard and multi-endpoint logic.

## Working Conclusion

The project is already strong where many voice projects struggle first: onboarding, trust, lifecycle projection, and operator shell.

The next stretch should focus less on adding more setup features and more on creating one thin but real voice loop that can be observed, debugged, and repeated reliably.
