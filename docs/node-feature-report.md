# HexeVoice Node Feature Report

Created: 2026-08-22

## Scope

This report inventories the features present in the HexeVoice node repository as of this review. It is based on the backend, firmware, frontend, tests, and existing docs in this repo.

Canonical follow-up work from this report is tracked in `docs/New_tasks.txt` and
`docs/task-details.md`, starting with Tasks 212-217. Current implementation
state should be reconciled against `docs/architecture.md`,
`docs/voice-node-roadmap.md`, `docs/firmware-baseline.md`,
`docs/firmware-validation-matrix.md`, and `docs/setup.md`.

## Executive Summary

HexeVoice is no longer just a starter node. The repo contains a working Hexe voice-node backend with Core onboarding, trust, provider setup, capability/governance readiness, endpoint registry, voice WebSocket transport, backend-owned wake/STT/assistant/TTS pipeline boundaries, endpoint command routing, TTS artifact serving, local rendered Node UI APIs, and native ESP-IDF firmware tracks for ESP-BOX-3 plus an experimental Home Assistant Voice PE profile.

The strongest implemented areas are the backend API surface, setup/readiness lifecycle, voice session manager, intent registry, provider controls, and endpoint registry/commands. The most important partial areas are physical-device completeness, especially firmware-side provisioning, final hardened TTS playback/download paths, OTA signature validation, and device-tested reconnect/session behavior.

## Implemented Backend Features

### Node Lifecycle And Core Integration

- Health/readiness APIs: `/health/live`, `/health/ready`, and `/api/health`.
- Canonical onboarding lifecycle: node identity, Core connection, bootstrap discovery, registration, approval, trust activation, provider setup, capability declaration, governance sync, and ready.
- Local onboarding persistence under the configured runtime directory.
- Core onboarding session start/poll/finalize flow.
- Trust activation persistence and trust-status refresh handling for trusted resume, revocation, or removal.
- Registration metadata refresh back to Core.
- Core Supervisor runtime registration and heartbeat when `HEXE_SUPERVISOR_ENABLED` is enabled.
- Node migration export/import/preflight/backup/restore APIs.

### Provider, Capability, And Governance Setup

- Provider setup status and persistence for enabled/default providers.
- Provider configuration paths for STT, TTS, and wake providers.
- Setup provider apply actions that call runtime service controls.
- Capability selection and declaration APIs.
- Manifest preview/validation used by setup capability readiness.
- Governance current/refresh/readiness APIs.
- Operational-status projection from persisted governance/readiness state.

### Runtime Service Controls

- Service status and start/stop/install/restart APIs.
- Engine heartbeat intake for `faster_whisper_stt` and `piper_tts`.
- Provider-specific status routes.
- Control-script integration is present for openWakeWord, Faster-Whisper STT, and Piper TTS through settings/runtime service boundaries.

## Implemented Voice Features

### Assistant And Intent Handling

- Text assistant route: `POST /api/assistant/turn`.
- Local echo fallback for smoke tests and degraded operation.
- AI Node routing when configured through `VOICE_ASSISTANT_PROVIDER=ai_node`.
- Registered local voice intent registry with list/register/update/lifecycle/review/dispatch/invoke APIs.
- Seeded/local intent handling is present, including timer-oriented intent behavior and reply-audio generation hooks.
- Assistant responses carry provider/model metadata, fallback status, latency, structured errors, and local-handling metadata.

### Voice Session Transport

- Voice WebSocket route: `GET /api/voice/ws`.
- Versioned voice event envelope contract for endpoint-to-backend and backend-to-endpoint events.
- Multi-endpoint session manager with per-endpoint WebSocket runtime, active session state, command routing, audio buffers, and replay metadata.
- Accepted endpoint event types include session start, audio chunks, audio end, VAD speech-start, cancel, ping, command ack/error, and TTS playback acknowledgement.
- Backend events include wake accepted, session state, transcript final, response text, TTS ready, session completed/cancelled, and session error.
- Voice status API exposes connection, UX, session, transport health, connected endpoint ids, endpoint summaries, diagnostics, last transcript/response/TTS/playback/error data, and operator actions.
- Operator cancel API for active voice sessions.
- Persistent voice session history with list/detail routes and replay command routing.

### Wake, STT, Assistant, And TTS Pipeline

- Backend-owned wake detection boundary with deterministic, in-process openWakeWord, and supervised openWakeWord provider choices.
- Optional wake recording capture for accepted wake sessions with retention cleanup.
- Optional micro-VAD chunk recording with retention cleanup.
- Turn pipeline runs STT finalization, assistant routing, and TTS synthesis metadata on `audio.end`.
- STT adapters include deterministic, OpenAI, local Faster-Whisper, external Faster-Whisper service, silence trimming, model profile selection, and fallback-profile behavior.
- TTS adapters include deterministic, OpenAI, and Piper support.
- Generated TTS artifacts are stored, listed, served, deleted, and cleaned up.
- Piper TTS warmup loop exists when Piper is selected.

## Endpoint And Firmware Features

### Endpoint Registry And Commands

- Endpoint heartbeat route persists endpoint records and capability metadata.
- Endpoint time-sync route.
- Endpoint status routes for latest, per-endpoint, and all endpoints.
- Operator metadata updates for display name and zone.
- Online/stale/offline connection projection based on heartbeat freshness.
- Endpoint commands routed through the active WebSocket: volume, mute, micro-VAD pause, cancel, replay, speak, play sound, storage reformat, LED simulation, media transfer, and OTA update.
- Firmware update availability is projected from runtime firmware artifacts and endpoint version metadata.

### Endpoint Media And Firmware Artifacts

- Endpoint media upload/list/get/delete/file-serving APIs.
- Media inventory view from endpoint heartbeat capabilities.
- Media delivery command dispatch to endpoints with URL, size, hash, destination, overwrite/rewrite, and activation metadata.
- Firmware artifact file serving and manifest route.
- Firmware OTA push command route that sends URL, version, hash, and size to an endpoint.

### Native Firmware

- Native ESP-IDF app entrypoint and shared app state.
- ESP-BOX-3 display initialization, framebuffer rendering, and branded RGB565 UI assets.
- NVS initialization and persisted endpoint settings for volume and mute.
- Wi-Fi station startup and reconnect handling using compile-time/local secrets.
- Button handling for mute/config interactions.
- ESP-BOX-3 touch controls for volume and mute.
- Microphone initialization and energy-threshold VAD task.
- YAML endpoint config template and build-time config generation.
- Backend heartbeat and voice WebSocket client scaffold.
- Backend event-to-UX mapping for wake, transcript, response, TTS-ready, completion, cancellation, and error events.
- TTS playback lifecycle tracking and reporting.
- Selectable board profile support: `esp_box_3` default and experimental `ha_voice_pe`.
- Home Assistant Voice PE support includes I2S microphone input, AIC3204/I2S TTS output, center-button wake/cancel controls, hardware mute controls, RMT LED ring driver, voice-state LED patterns, rotary volume, and LED accent color selection.
- Manual OTA path from backend-pushed `ota.update` events.

## Operator UI Features

- React/Vite operator UI for setup and operational surfaces.
- Canonical setup flow screens for host/core setup, reauth, providers, capabilities, ready, and onboarding.
- Dashboard sections for overview, runtime, providers, voice endpoints, voice intents, TTS provider/runtime, diagnostics, and operational warnings.
- Local rendered Node UI APIs with page snapshots for overview, runtime, voice endpoints, voice intents, and voice TTS.
- Endpoint dashboard actions for volume, mute/unmute, cancel, replay, and assistant-turn testing.
- Provider setup and runtime service controls are surfaced through both frontend and local Node UI payloads.

## Supporting Operations

- Installer, uninstaller, bootstrap, dev stack, restart stack, runtime status, provider control, model/control, and firmware artifact scripts.
- Docker compose files for Faster-Whisper STT and Piper TTS, including CUDA-oriented Faster-Whisper compose/Dockerfile variants.
- Systemd service templates for backend, STT, and frontend.
- Post-install smoke test, migration preflight, STT benchmark, runtime directory preparation, and firmware control scripts.
- Broad tests cover onboarding, trust, provider setup, phase 2 readiness, supervisor runtime, endpoint registry, voice contracts, voice WebSocket, voice loop integration, wake, pipeline, intent handling, STT/TTS services, firmware config/envelope, and operational scripts.

## Partial Or Scaffolded Areas

- Firmware VAD can update local state, send speech-start/audio chunks/audio-end, and react to backend events, but it is still documented as partial pending device hardening.
- Firmware provisioning UI is not implemented; Wi-Fi uses compile-time/local credentials.
- Automatic endpoint discovery is deferred.
- Firmware-side settings UI is not implemented.
- Firmware-side SHA-256 enforcement and signed manifest validation for OTA are missing.
- Some firmware modules remain scaffolds, including firmware-side wake word, STT stream, assistant client, telemetry, and power modules.
- Device-tested reconnect/session-boundary behavior remains listed as next firmware work.
- Existing docs disagree in places: `README.md` still says `/api/voice/ws`, STT/TTS adapters, firmware TTS playback, and live endpoint telemetry are not implemented, while source and newer docs show those backend surfaces now exist. Treat `docs/voice-node-roadmap.md`, `docs/firmware-baseline.md`, and source as more current for voice runtime status.

## Public Capability Shape

The intended external capability family is voice/task oriented. Current and planned capability candidates include:

- `task.wake_stream`
- `task.transcribe`
- `task.synthesize`
- `task.command_interpret`
- `task.endpoint_session`
- `task.conversation_session`

Source comments and docs suggest internal implementation details such as VAD, arbitration, and provider routing should not be exposed as standalone public capabilities unless intentionally promoted later.

## Evidence Reviewed

- Backend app/routes: `src/hexevoice/main.py`
- Settings/provider choices: `src/hexevoice/config/settings.py`
- Endpoint registry: `src/hexevoice/endpoint/service.py`
- Voice session manager: `src/hexevoice/voice/session_manager.py`
- Voice pipeline: `src/hexevoice/voice/pipeline.py`
- Wake providers: `src/hexevoice/voice/wake.py`
- Assistant/intent services: `src/hexevoice/assistant/`
- Firmware entrypoint and board/voice modules: `firmware/main/`
- Frontend app and dashboard/setup features: `frontend/src/`
- Current docs: `docs/architecture.md`, `docs/feature-spec.md`, `docs/firmware-baseline.md`, `docs/voice-node-roadmap.md`
- Tests: `tests/test_voice_websocket.py`, `tests/test_voice_loop_integration.py`, `tests/test_voice_pipeline.py`, `tests/test_voice_wake.py`, `tests/test_endpoint_registry.py`, `tests/test_phase2.py`, `tests/test_provider_setup.py`, and related setup/service tests.
