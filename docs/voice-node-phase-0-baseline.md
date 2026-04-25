# Voice Node Phase 0 Baseline

Created: 04/25/2026

## Purpose

This file records the current HexeVoice baseline before the first real wake-to-reply voice loop. It separates implemented behavior from scaffolding so Phase 1 work can start from the actual repository state.

Status labels:

- `implemented`: working code exists in the repo for the described boundary.
- `partial`: useful behavior exists, but the runtime path is incomplete.
- `scaffold`: files or placeholders exist, but the real behavior is not wired.
- `missing`: no meaningful implementation exists yet.

## Baseline Results

| Area | Status | Evidence |
| --- | --- | --- |
| Node onboarding / trust / lifecycle | implemented | Backend routes are registered in `src/hexevoice/main.py`; models live in `src/hexevoice/api/models.py`; state persistence is in `src/hexevoice/persistence/onboarding_state.py`; onboarding, approval, trust activation, trust status, provider setup, capability declaration, governance, and operational status services live under `src/hexevoice/onboarding/`, `src/hexevoice/trust/`, `src/hexevoice/providers/`, `src/hexevoice/capabilities/`, and `src/hexevoice/governance/`. |
| Dashboard shell | implemented | The app shell and routing are in `frontend/src/App.jsx`; dashboard cards live under `frontend/src/features/dashboard/`; shared visual tokens and components are in `frontend/src/theme/`. |
| ESP32 microphone + VAD loop | partial | Firmware initializes BSP audio, opens a 16 kHz mono microphone stream, starts a VAD task, estimates energy, and moves local UI state between idle/listening in `firmware/main/board/audio.cpp`; app state is in `firmware/main/app_state.h`. |
| Text assistant endpoint | implemented | `POST /api/assistant/turn` is registered in `src/hexevoice/main.py`; request and response contracts are in `src/hexevoice/api/models.py`; deterministic local responses and simple commands are in `src/hexevoice/assistant/service.py`. |
| Endpoint heartbeat/status | partial | `POST /api/endpoint/heartbeat` and `GET /api/endpoint/status/{endpoint_id}` are registered in `src/hexevoice/main.py`; in-memory heartbeat state is implemented in `src/hexevoice/endpoint/service.py`; there is no persistent endpoint registry yet. |
| Voice pipeline | missing | There is no backend wake detector, STT integration, TTS integration, audio transport endpoint, session manager, or event protocol module under `src/hexevoice/voice/`; firmware voice files under `firmware/main/voice/` only log scaffold readiness. |

## Backend Inventory

Implemented:

- Health and readiness routes: `GET /health/live`, `GET /health/ready`, and `GET /api/health` in `src/hexevoice/main.py`.
- Canonical node setup and readiness routes for onboarding, Core bootstrap discovery, registration, approval polling, trust activation, trust-status refresh, provider setup, capability declaration, governance, and operational status in `src/hexevoice/main.py`.
- Restart-safe onboarding/trust/provider/capability/governance state through `src/hexevoice/persistence/onboarding_state.py`.
- Simple text assistant route through `src/hexevoice/assistant/service.py`.

Partial:

- Endpoint heartbeat/status exists, but `src/hexevoice/endpoint/service.py` stores records only in memory and does not model endpoint registration, connection state, session state, or recent event history.
- Assistant turns generate endpoint-scoped session ids, but they are local counters inside `src/hexevoice/assistant/service.py`, not a real session lifecycle.

Missing:

- `/api/voice/ws` or any other audio transport route.
- Backend wake detection with openWakeWord.
- STT and TTS providers.
- Shared voice event envelope.
- Voice session manager with explicit states and cancel/error handling.
- Endpoint metadata persistence.

## Frontend Inventory

Implemented:

- Setup/dashboard routing and canonical setup-step rendering in `frontend/src/App.jsx`.
- Onboarding shell and step actions in `frontend/src/features/onboarding/OnboardingPanel.jsx`.
- Dashboard navigation and overview cards under `frontend/src/features/dashboard/`.
- Voice endpoint dashboard section and cards under `frontend/src/features/dashboard/VoiceEndpointDashboardSection.jsx` and `frontend/src/features/dashboard/cards/`.

Partial:

- The voice endpoint dashboard exists as an operator surface, but the microphone, STT, TTS, wake-word, audio-path, session-history, and transport-health cards are placeholder-only.
- Dashboard actions such as refresh endpoint and test assistant turn are visually present, but real endpoint controls are not wired.

Missing:

- Live endpoint registration view.
- Voice WebSocket/session telemetry view.
- Last transcript, last response, last error, and active session timeline.
- Operator controls for stop, replay, mute, and reconnect.

## Firmware Inventory

Implemented:

- Native ESP-IDF entrypoint and boot loop in `firmware/main/app_main.cpp`.
- Display, buttons, Wi-Fi, storage, power, telemetry, OTA, and audio initialization hooks are called from `firmware/main/app_main.cpp`.
- Basic button state handling is present in `firmware/main/board/buttons.cpp`.
- Wi-Fi station initialization and reconnect handling are present in `firmware/main/board/wifi.cpp`.

Partial:

- Microphone initialization and simple energy-threshold VAD run in `firmware/main/board/audio.cpp`.
- VAD affects local app state, but it does not stream audio, open a backend session, or interact with wake/STT/TTS.
- Display/UI state exists through `firmware/main/ui/` and app state, but the UI is still a lightweight scaffold.

Scaffold:

- `firmware/main/voice/wake_word.cpp` logs wake-word scaffold readiness.
- `firmware/main/voice/stt_stream.cpp` logs STT stream scaffold readiness.
- `firmware/main/voice/tts_player.cpp` logs TTS player scaffold readiness.
- `firmware/main/voice/assistant_client.cpp` logs backend assistant-client scaffold readiness.

Missing:

- Backend connection client.
- Endpoint heartbeat sender.
- Audio upload or WebSocket transport.
- TTS playback path.
- Wake accepted/listening/thinking/speaking event handling from backend.

## Phase 0 Gap List

Backend transport:

- Add a WebSocket-first voice transport endpoint, initially for one endpoint and one active session.
- Define a shared event envelope for endpoint-to-backend and backend-to-endpoint messages.

Wake detection:

- Add an openWakeWord-backed backend wake detector.
- Treat firmware VAD as an optional signal, not the authority for wake.

Session lifecycle:

- Add a backend-authored voice session manager with states for wake detected, listening, capturing, transcribing, routing, synthesizing, playing, completed, cancelled, and failed.
- Keep endpoint connection state, endpoint UX state, and backend session state separate.

STT/TTS integration:

- Add provider boundaries for STT and TTS.
- Wire the existing text assistant turn as the middle of the voice loop after transcript finalization and before speech synthesis.

Firmware transport:

- Add backend connection configuration and a client that can send endpoint metadata, heartbeat, and audio chunks.
- Support cancel/error/session state events from the backend.

Firmware playback:

- Add TTS audio receive and playback support.
- Map backend events to display and button UX states.

Frontend observability:

- Replace voice endpoint placeholders with live endpoint state, active session, last transcript, last response, last error, and transport health.
- Wire operator actions for stop, replay, mute, reconnect, and test assistant turn.

Docs/testing:

- Keep `README.md`, `docs/architecture.md`, and `docs/voice-node-roadmap.md` explicit that the full voice pipeline is not implemented yet.
- Add targeted tests as each current scaffold becomes real behavior.

## Phase 1 Starting Point

The repo is ready for Phase 1 planning from this baseline if Phase 1 is treated as a first real voice-loop implementation, not a cleanup of an already-working speech pipeline. The safest implementation order is:

1. Backend voice event/session contract.
2. Backend WebSocket endpoint and in-memory single-session manager.
3. Firmware backend client and audio chunk sender.
4. Backend wake/STT/assistant/TTS adapters.
5. Firmware playback and event-driven UI.
6. Frontend endpoint/session observability.
