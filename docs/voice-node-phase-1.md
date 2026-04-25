# Voice Node Phase 1

## Purpose

This document expands Phase 1 from the roadmap into a focused brainstorming and decision record.

Phase 1 goal:
Replace the current heartbeat + text-turn prototype with a proper endpoint session model.

This is still a working draft. The point is to go deliverable by deliverable and capture:

- what the deliverable means
- the main design options
- the current recommendation
- the open questions we still need to answer

## Current Baseline

Today the project has:

- `POST /api/assistant/turn` for simple text request/response
- endpoint heartbeat/status tracking in memory
- firmware-side audio and simple VAD scaffolding
- no formal endpoint registration contract
- no formal session lifecycle contract
- no real audio transport contract

That makes Phase 1 the contract-definition phase, not the full speech pipeline phase.

## Deliverable 1: Endpoint Registration And Session Lifecycle Contracts

### What This Means

We need two related but separate contracts:

- endpoint registration
- endpoint session lifecycle

Registration answers:

- who is this device
- what can it do
- what configuration applies to it

Session lifecycle answers:

- what interaction is active right now
- what state is it in
- how does it start, progress, and end

### Brainstorm

Registration should likely include:

- `endpoint_id`
- `endpoint_type`
- `display_name`
- `zone_id`
- `priority`
- `firmware_version`
- `input_format`
- `output_capabilities`
- `wake_enabled`
- `stt_enabled`
- `tts_enabled`
- `protocol_version`

Session lifecycle should likely include:

- `session_id`
- `endpoint_id`
- `session_state`
- `started_at`
- `last_updated_at`
- `wake_source`
- `cancel_reason`
- `completion_reason`

### Candidate Lifecycle

Proposed session states:

- `idle`
- `wake_detected`
- `listening`
- `capturing`
- `transcribing`
- `local_command`
- `routing_upstream`
- `waiting_response`
- `synthesizing`
- `playing`
- `completed`
- `cancelled`
- `failed`

This is more expressive than the current device-only states and gives the backend enough shape to drive UI and debugging.

### Recommendation

Use a split model:

- registration is long-lived and persistence-backed
- session state is short-lived and event-driven

That means an endpoint can remain registered across restarts even if active sessions are dropped or rebuilt.

### Decisions To Record

- endpoint registration should be explicit, not implicit from heartbeat traffic
- session lifecycle should be backend-authored
- endpoints should be able to reconnect without creating a brand new identity each time

### Open Questions

- Should endpoint registration require approval, or is node trust enough for endpoint-local devices?
- Should `endpoint_id` be firmware-generated, operator-provided, or node-assigned on first registration?
- Should one endpoint be allowed to have more than one active session at a time? My current recommendation is no for MVP.

## Deliverable 2: Device States Beyond `idle/listening/thinking/speaking/offline`

### What This Means

The current device states are good for basic UX, but not good enough for protocol clarity.

We need to distinguish:

- operator-visible UI state
- backend session state
- transport/connection state

Those are related, but they are not the same thing.

### Brainstorm

A cleaner split would be:

Device connection state:

- `offline`
- `connecting`
- `connected`
- `degraded`

Device UX state:

- `idle`
- `wake_armed`
- `wake_detected`
- `listening`
- `thinking`
- `speaking`
- `muted`
- `error`

Backend session state:

- `none`
- `capturing`
- `transcribing`
- `handling_local`
- `routing_upstream`
- `synthesizing`
- `playing`
- `completed`
- `cancelled`
- `failed`

### Recommendation

Do not force one enum to represent everything.

Instead:

- keep a simple device UX state for the endpoint
- keep a richer backend session state for orchestration
- keep a separate connection health state for transport and diagnostics

This will make the frontend much easier to build later because it can show human-friendly state without losing engineering detail.

### Decisions To Record

- protocol should separate connection state from interaction state
- device UX states can stay compact
- backend session states should be more detailed than device UX states

### Open Questions

- Should `muted` be a UX state, a capability flag, or both?
- Should `wake_armed` exist explicitly, or is `idle` enough for MVP?
- Should `degraded` be generic, or should we expose exact degraded reasons from the start?

## Deliverable 3: Audio Transport Shape

### What This Means

We need to decide how audio moves between firmware and backend.

The roadmap listed three broad options:

- push-to-server PCM chunks
- WebSocket stream
- HTTP chunk upload with response stream

### Brainstorm

Option A: Simple HTTP upload per utterance

Pros:

- easiest to reason about
- easiest to debug with existing backend tooling
- good for push-to-talk or VAD-bounded utterances

Cons:

- not ideal for low-latency streaming
- awkward for partial transcripts or mid-stream cancel

Option B: WebSocket bidirectional stream

Pros:

- best fit for event-driven session updates
- supports audio chunks, partial transcript events, cancel, and playback coordination
- scales better into future multi-endpoint interactivity

Cons:

- more protocol work up front
- more complexity in firmware

Option C: MQTT audio/event transport

Pros:

- conceptually aligned with broader Hexe messaging patterns
- useful for event fanout

Cons:

- likely a poor first choice for real-time audio chunk transport
- higher complexity and more operational moving parts for MVP

### Recommendation

My current recommendation is:

- use WebSocket as the primary Phase 1 target contract
- define an HTTP fallback for simple recorded-utterance upload in development

Why:

- the project clearly wants session-oriented, event-rich behavior
- WebSocket fits transcript partials, cancel, playback-ready, and future duplex control better than plain HTTP
- MQTT feels better as a control or telemetry path than as the first real audio path

### Minimum Audio Payload Shape

If we choose WebSocket, the message families should likely be:

- `session.start`
- `audio.chunk`
- `audio.end`
- `session.cancel`
- `session.ping`
- `session.state`
- `transcript.partial`
- `transcript.final`
- `command.result`
- `response.text`
- `tts.ready`
- `session.complete`
- `session.error`

Audio chunks should likely carry:

- `session_id`
- `chunk_index`
- `encoding`
- `sample_rate_hz`
- `channels`
- `is_final`
- binary or base64 payload

### Decisions To Record

- Phase 1 should define transport, not just discuss it abstractly
- transport must support cancel and state updates, not only raw audio upload
- transport should be designed around one endpoint first, but not block multi-endpoint later

### Open Questions

- Do we want binary WebSocket frames, JSON-wrapped base64 chunks, or a hybrid protocol?
- Should the first implementation send complete utterances over WebSocket even if the long-term design is chunk streaming?
- Do we want an HTTP dev/test path written into the spec from day one?

## Deliverable 4: Server Responses

### What This Means

The backend needs a formal event vocabulary for what it tells endpoints.

The roadmap already identified the most important responses:

- wake accepted
- listening started
- transcript partial/final
- local command handled
- upstream turn pending
- TTS playback ready
- stop/cancel

### Brainstorm

These should probably become named response events rather than ad hoc response bodies.

Candidate response events:

- `wake.accepted`
- `capture.started`
- `capture.stopped`
- `transcript.partial`
- `transcript.final`
- `command.handled`
- `upstream.pending`
- `upstream.response`
- `tts.ready`
- `playback.start`
- `playback.stop`
- `session.cancelled`
- `session.completed`
- `session.error`

Each event should have:

- `event_type`
- `session_id`
- `endpoint_id`
- `server_time`
- event-specific payload

### Recommendation

Use event-first responses, not endpoint-specific ad hoc reply objects.

That means:

- one protocol
- many event types
- consistent envelope

This gives us a cleaner path for firmware, frontend diagnostics, testing, and replay tooling.

### Example Envelope

```json
{
  "event_type": "transcript.final",
  "session_id": "session-123",
  "endpoint_id": "kitchen-box",
  "server_time": "2026-04-25T20:00:00Z",
  "payload": {
    "text": "turn on the kitchen lights",
    "confidence": 0.91
  }
}
```

### Decisions To Record

- all backend-to-endpoint responses should use a shared envelope
- server responses should be designed to double as observability events where possible
- final transcript and local command result should be first-class protocol events

### Open Questions

- Should `tts.ready` include a URL, a stream handle, or immediate chunked audio delivery?
- Should `wake.accepted` exist if wake detection happens fully on-device?
- Should `upstream.response` and `tts.ready` be separate events or a combined reply object?

## Deliverable 5: Persist Endpoint Metadata Instead Of Keeping It Only In Memory

### What This Means

The current endpoint service keeps records in memory only.

That is enough for a stub, but not enough for:

- restart-safe behavior
- operator visibility
- endpoint identity continuity
- debugging recent failures

### Brainstorm

There are two different persistence scopes here:

Long-lived endpoint registry data:

- endpoint identity
- endpoint type
- display metadata
- capability flags
- last known firmware version
- preferred configuration

Short-lived recent activity data:

- last seen time
- current connection state
- most recent session id
- most recent error
- recent event summary

I do not think raw audio should be part of Phase 1 persistence.

### Recommendation

Persist:

- endpoint registry
- last known health snapshot
- recent session/event summary

Do not persist in Phase 1:

- raw PCM audio
- full transcript history beyond a small recent summary window
- large playback artifacts

This keeps persistence lightweight while still making the node operator-friendly.

### Suggested Storage Boundary

Likely new store responsibilities:

- `EndpointRegistryStore`
- `EndpointSessionSnapshotStore` or equivalent addition to the runtime persistence layer

Potential stored fields:

- `endpoint_id`
- `endpoint_type`
- `display_name`
- `zone_id`
- `priority`
- `registered_at`
- `last_seen_at`
- `last_connection_state`
- `last_session_id`
- `last_error`
- `firmware_version`
- `input_format`
- `output_capabilities`

### Decisions To Record

- persistence should focus on metadata and recent state, not media storage
- endpoint registration must survive backend restarts
- active sessions may be resumable later, but Phase 1 only needs recent snapshot continuity

### Open Questions

- Should endpoint persistence live inside `runtime/onboarding_state.json` at first, or move into its own store file immediately?
- How much recent event history should we keep per endpoint for MVP?
- Should transcript text be persisted at all in Phase 1, or only status summaries?

## Cross-Deliverable Recommendation

If we want Phase 1 to stay manageable, the most important constraints should be:

1. One endpoint at a time for the first real contract.
2. WebSocket-first event protocol.
3. Explicit endpoint registration plus explicit session lifecycle.
4. Shared event envelope for all server responses.
5. Persistence for endpoint metadata and recent state only.

## Accepted MVP Contract Decisions

Task 028 establishes the first backend-owned voice contract in `src/hexevoice/voice/contracts.py`.

Accepted for the MVP:

- `VoiceEventEnvelope` is the shared JSON envelope for endpoint-to-backend, backend-to-endpoint, and internal observability events.
- Endpoint connection state, endpoint UX state, and backend session state are separate fields on `VoiceSessionSnapshot`.
- The first implementation targets one active endpoint/session at a time, with explicit transition validation in `VOICE_SESSION_ALLOWED_TRANSITIONS`.
- `audio.chunk` carries transport metadata and an optional base64 payload field only; wake detection, STT, TTS, and raw audio processing remain outside Task 028.
- `/api/voice/ws` must consume this contract in the next implementation task instead of introducing a parallel ad hoc message shape.

Task 029 implements that next transport step in `src/hexevoice/voice/session_manager.py` and registers `/api/voice/ws` from `src/hexevoice/main.py`.

Accepted for the first WebSocket implementation:

- The route is an MVP single-endpoint, single-active-session WebSocket manager.
- The endpoint may send `session.start`, `audio.chunk`, `audio.end`, `session.cancel`, and `session.ping`.
- The backend responds with `session.state`, `session.completed`, `session.cancelled`, and `session.error` envelopes.
- Session state is held in memory only; endpoint/session persistence and recent event history remain deferred.
- `audio.end` completes the contract-only session after chunk intake, but it does not yet run wake detection, STT, assistant routing, or TTS.

Task 030 adds the backend wake authority boundary in `src/hexevoice/voice/wake.py`.

Accepted for the first wake intake implementation:

- The WebSocket manager feeds each `audio.chunk` into a `WakeDetector` adapter and emits `wake.accepted` through the shared envelope when backend wake detection fires.
- `DeterministicWakeDetector` is the test/development boundary for predictable wake behavior.
- `OpenWakeWordWakeDetector` is the runtime adapter name and attempts to load the optional `openwakeword` package; if unavailable, it fails closed and reports no wake.
- Raw audio chunks are inspected transiently and are not persisted.
- Firmware VAD remains an optional early signal only; backend wake detection is the canonical wake authority.
- STT, assistant routing, and TTS remain deferred to later tasks.

Task 036 makes the wake provider configurable from backend settings.

Accepted for the configurable provider:

- `VOICE_WAKE_PROVIDER=openwakeword` is the default runtime path and constructs `OpenWakeWordWakeDetector`.
- `VOICE_WAKE_PROVIDER=deterministic` keeps predictable local/test behavior without loading model packages.
- `VOICE_WAKE_THRESHOLD`, `VOICE_WAKE_MODELS`, `VOICE_WAKE_AUTO_DOWNLOAD_MODELS`, `VOICE_WAKE_ENABLE_SPEEX_NOISE_SUPPRESSION`, and `VOICE_WAKE_VAD_THRESHOLD` configure the openWakeWord adapter.
- The backend still fails closed if the openWakeWord runtime or selected model cannot be loaded.

Task 037 adds backend wake buffering and provider health.

Accepted for the buffered wake provider:

- The openWakeWord adapter keeps a bounded per-session PCM buffer so short ESP audio chunks can be accumulated into the 80 ms prediction frames expected by openWakeWord.
- `VOICE_WAKE_BUFFER_MS` controls the retained rolling audio window and `VOICE_WAKE_PREDICTION_FRAME_MS` controls the minimum frame sent to the wake model.
- `/api/voice/status` includes `wake_provider` metadata with provider name, load state, load error, threshold, model configuration, buffer configuration, active buffer count, and last detection summary.

Task 038 aligns firmware capture with backend wake authority.

Accepted for firmware wake alignment:

- Firmware VAD opens and closes the upstream audio turn but no longer moves the LCD into the listening state by itself.
- Firmware enters the listening UX only after the backend emits `wake.accepted` or a listening session snapshot.
- Firmware advertises `wake_source=openwakeword` in `session.start` because the backend is the canonical wake authority.

Task 032 adds the first backend STT -> assistant -> TTS turn boundary in `src/hexevoice/voice/pipeline.py`.

Accepted for the first provider-adapter implementation:

- STT and TTS are protocols with deterministic adapters for tests/development.
- `audio.end` after backend wake detection can emit `transcript.final`, `response.text`, `tts.ready`, and `session.completed`.
- The existing `AssistantTurnService` remains the text-turn middle of the voice loop.
- TTS output is metadata only for now (`stream_id`, content type, optional URL); firmware playback is handled later.
- Raw audio is not persisted by the pipeline.

## Proposed Phase 1 Outcome

Phase 1 has produced the first MVP wake-to-reply contract path. The current handoff is recorded in `docs/voice-loop-phase-1-handoff.md`.

Phase 1 is successful if, by the end of the remaining provider/device pass, we have:

- a written endpoint protocol spec
- a defined state model for endpoints and sessions
- a chosen transport
- a chosen response event vocabulary
- a persistence plan for endpoint registry and recent state

That would give Phase 2 a stable contract to implement against.

## Next Pass

On the next pass, we should turn this brainstorming doc into a decision doc by marking each section as:

- accepted
- rejected
- deferred

and then sketch the actual request/response models that belong in the backend code.
