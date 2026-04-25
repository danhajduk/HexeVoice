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
| ESP32 microphone + VAD loop | partial | Firmware initializes BSP audio, opens a 16 kHz mono microphone stream, starts a VAD task, estimates energy, moves local UI state between idle/listening, and queues bounded audio frames toward the backend client in `firmware/main/board/audio.cpp`; app state is in `firmware/main/app_state.h`. |
| Text assistant endpoint | implemented | `POST /api/assistant/turn` is registered in `src/hexevoice/main.py`; request and response contracts are in `src/hexevoice/api/models.py`; deterministic local responses and simple commands are in `src/hexevoice/assistant/service.py`. |
| Endpoint heartbeat/status | partial | `POST /api/endpoint/heartbeat` and `GET /api/endpoint/status/{endpoint_id}` are registered in `src/hexevoice/main.py`; in-memory heartbeat state is implemented in `src/hexevoice/endpoint/service.py`; there is no persistent endpoint registry yet. |
| Voice pipeline | partial | The backend now has a voice protocol contract, in-memory single-endpoint WebSocket session manager, wake detector adapter boundary, and deterministic STT -> assistant -> TTS pipeline boundary under `src/hexevoice/voice/`; real STT/TTS providers and firmware playback are still not implemented. |

## Backend Inventory

Implemented:

- Health and readiness routes: `GET /health/live`, `GET /health/ready`, and `GET /api/health` in `src/hexevoice/main.py`.
- Canonical node setup and readiness routes for onboarding, Core bootstrap discovery, registration, approval polling, trust activation, trust-status refresh, provider setup, capability declaration, governance, and operational status in `src/hexevoice/main.py`.
- Restart-safe onboarding/trust/provider/capability/governance state through `src/hexevoice/persistence/onboarding_state.py`.
- Simple text assistant route through `src/hexevoice/assistant/service.py`.

Partial:

- Endpoint heartbeat/status exists, but `src/hexevoice/endpoint/service.py` stores records only in memory and does not model endpoint registration, connection state, session state, or recent event history.
- Assistant turns generate endpoint-scoped session ids, but they are local counters inside `src/hexevoice/assistant/service.py`, not a real session lifecycle.
- Voice contract models in `src/hexevoice/voice/contracts.py` define the shared event envelope, event vocabulary, endpoint connection states, endpoint UX states, backend session states, audio chunk metadata, and allowed MVP session transitions. The models are validated by `tests/test_voice_contracts.py` and consumed by the WebSocket manager.
- `/api/voice/ws` is registered in `src/hexevoice/main.py` and handled by `src/hexevoice/voice/session_manager.py`. It accepts one endpoint connection, one active session, `session.start`, `audio.chunk`, `audio.end`, `session.cancel`, and `session.ping`, and returns state/completion/cancel/error envelopes. The manager is in-memory only and does not yet process audio.
- `src/hexevoice/voice/wake.py` defines the backend wake authority boundary. The WebSocket manager inspects incoming audio chunks with a `WakeDetector`, emits `wake.accepted` on detection, and uses a deterministic fake detector in tests while runtime defaults to an optional openWakeWord adapter.
- `src/hexevoice/voice/pipeline.py` defines STT and TTS adapter protocols plus deterministic fake adapters. The WebSocket manager can finalize a turn through transcript, assistant response, TTS metadata, and completion events without persisting raw audio.

Missing:

- Required openWakeWord package/model installation and tuning for production wake detection.
- Real STT and TTS provider implementations.
- Persistent voice session history beyond the in-memory MVP manager.
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
- Endpoint config is generated from `firmware/config/endpoint.yaml` or `firmware/config/endpoint.example.yaml` by `firmware/tools/generate_endpoint_config.py`.
- Firmware backend client initialization is wired from `firmware/main/app_main.cpp` through `firmware/main/voice/backend_client.cpp`.

Partial:

- Microphone initialization and simple energy-threshold VAD run in `firmware/main/board/audio.cpp`.
- VAD affects local app state, submits bounded microphone frames to the backend client, and sends `audio.end` on VAD silence.
- Display/UI state exists through `firmware/main/ui/` and app state, but the UI is still a lightweight scaffold.
- The backend client sends heartbeat requests and voice WebSocket session/audio chunk envelopes using the generated endpoint config. Failure behavior is explicit through queue-full and disconnected-session drops.
- Backend event handling now maps wake/session/transcript/response/TTS/error envelopes to endpoint UI phases and cancel/end session messages, but TTS playback is still metadata/scaffold-only.

Scaffold:

- `firmware/main/voice/wake_word.cpp` logs wake-word scaffold readiness.
- `firmware/main/voice/stt_stream.cpp` logs STT stream scaffold readiness.
- `firmware/main/voice/tts_player.cpp` logs TTS player scaffold readiness.
- `firmware/main/voice/assistant_client.cpp` logs backend assistant-client scaffold readiness.

Missing:

- Real TTS audio playback path.

## Phase 0 Gap List

Backend transport:

- Extend the WebSocket-first voice transport endpoint beyond the initial one-endpoint, one-session in-memory MVP.
- Use the shared event envelope in `src/hexevoice/voice/contracts.py` for endpoint-to-backend and backend-to-endpoint messages.

Wake detection:

- Install, configure, and tune openWakeWord for the backend wake detector adapter.
- Treat firmware VAD as an optional early signal, not the authority for wake.

Session lifecycle:

- Add a backend-authored voice session manager with states for wake detected, listening, capturing, transcribing, routing, synthesizing, playing, completed, cancelled, and failed.
- Keep endpoint connection state, endpoint UX state, and backend session state separate.

STT/TTS integration:

- Replace deterministic STT/TTS adapters with real provider implementations.
- Keep the existing text assistant turn as the middle of the voice loop after transcript finalization and before speech synthesis.

Firmware transport:

- Expand the backend client beyond heartbeat/audio send into response handling, reconnect UX, and final utterance boundaries.
- Support cancel/error/session state events from the backend.

Firmware playback:

- Replace TTS metadata handling with real TTS audio receive and playback support.
- Harden backend event handling against reconnects and fragmented WebSocket payloads after device testing.

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
