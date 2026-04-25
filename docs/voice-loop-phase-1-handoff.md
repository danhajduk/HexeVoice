# Voice Loop Phase 1 Handoff

Created: 04/25/2026

## Integrated Loop Status

The first single-endpoint wake-to-reply loop is implemented as an MVP contract path:

1. Firmware endpoint configuration is generated from `firmware/config/endpoint.yaml` or the committed example template.
2. Firmware sends heartbeat metadata to `POST /api/endpoint/heartbeat`.
3. Firmware opens `/api/voice/ws`, starts a voice session, and sends bounded PCM chunk envelopes.
4. Backend wake authority inspects `audio.chunk` through a `WakeDetector` adapter and emits `wake.accepted`.
5. Backend finalizes the turn on `audio.end`, runs deterministic STT, routes the transcript through `AssistantTurnService`, and emits deterministic TTS metadata.
6. Firmware maps backend events to endpoint UX phases and handles `tts.ready` through a playback scaffold.
7. Frontend dashboard reads `/api/voice/status` and shows connection state, active session, latest transcript, latest response, TTS metadata, errors, and supported actions.

## What Is Real

- Shared voice event envelope and session state contract.
- `/api/voice/ws` WebSocket route.
- In-memory single-endpoint, single-session backend manager.
- Backend wake detector adapter boundary with deterministic test detector and optional openWakeWord adapter.
- Backend STT -> assistant -> TTS adapter boundary with deterministic adapters.
- Firmware heartbeat, WebSocket session start, audio chunk send, `audio.end`, and cancel message paths.
- Firmware backend event-to-UX mapping.
- Frontend voice endpoint observability dashboard backed by live local APIs.
- Backend integration test for wake-to-reply and cancel behavior: `tests/test_voice_loop_integration.py`.

## Still Deferred

- Production openWakeWord package/model installation and tuning.
- Real STT provider.
- Real TTS provider and audio bytes/stream URLs.
- Firmware TTS audio download/stream playback.
- Persistent endpoint registry and voice session history.
- Backend support for replay, mute, and reconnect operator actions.
- Device-level ESP-IDF build validation in this shell.

## Verification

Completed on 04/25/2026:

- `PYTHONPATH=src .venv/bin/pytest -q tests/test_voice_loop_integration.py` -> 2 passed.
- `PYTHONPATH=src .venv/bin/pytest -q` -> 63 passed, 52 FastAPI deprecation warnings.
- `cd frontend && npm run build` -> passed.
- `cd firmware && idf.py build` -> blocked because `idf.py` is not installed in this shell.

The repo has a documented and tested backend/frontend integrated loop. The firmware code path is implemented, but final proof on device still requires an ESP-IDF environment.
