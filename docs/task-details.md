# Task Details

## Phase 0
Original task details:
- Create the full HexeVoice onboarding flow for both backend and frontend.
- Cover the complete setup experience from first launch through trusted, provider-configured readiness.
- Include all 10 steps of the setup flow in the implementation plan.
- Core docs are the source of truth for lifecycle, API, trust, and readiness behavior.
- Core node UI standards are the source of truth for layout, shell, card, status, and interaction patterns.

### Canonical 10-step setup flow for Phase 0 from Core docs
1. Node Identity -> `unconfigured`
   - Capture `node_name`, `node_type`, `node_software_version`, `protocol_version`, `node_nonce`, and optional `node_id`.
2. Core Connection -> `bootstrap_connecting`
   - Reach Core's bootstrap MQTT listener.
3. Bootstrap Discovery -> `bootstrap_connected` then `core_discovered`
   - Read retained bootstrap metadata from `hexe/bootstrap/core`.
4. Registration -> `registration_pending`
   - Start `POST /api/system/nodes/onboarding/sessions` using the canonical request contract.
5. Approval -> `pending_approval`
   - Surface `approval_url`, `session_id`, `expires_at`, and pending/terminal session outcomes.
6. Trust Activation -> `trusted`
   - Finalize with `GET /api/system/nodes/onboarding/sessions/{session_id}/finalize?node_nonce=...` and persist the activation payload.
7. Provider Setup -> `capability_setup_pending`
   - Perform node-local provider selection and readiness checks.
8. Capability Declaration -> `capability_declaration_in_progress`
   - Submit capability manifest to `POST /api/system/nodes/capabilities/declaration`.
9. Governance Sync -> `capability_declaration_accepted`
   - Fetch or refresh governance from `GET /api/system/nodes/governance/current` or `POST /api/system/nodes/governance/refresh`.
10. Ready -> `operational`
   - Reach `operational_ready=true` as projected by `GET /api/system/nodes/operational-status/{node_id}`.

### Core docs used as source of truth
- `docs/Core-Documents/docs/nodes/node-onboarding-api-contract.md`
- `docs/Core-Documents/docs/nodes/node-onboarding-phase1-contract.md`
- `docs/Core-Documents/docs/nodes/node-trust-activation-payload-contract.md`
- `docs/Core-Documents/docs/nodes/node-trust-status-contract.md`
- `docs/Core-Documents/docs/nodes/node-phase2-lifecycle-contract.md`
- `docs/Core-Documents/docs/nodes/node-capability-activation-architecture.md`
- `docs/Core-Documents/docs/nodes/node-lifecycle.md`
- `docs/Core-Documents/docs/nodes/node-onboarding-registration-architecture.md`
- `docs/Core-Documents/docs/nodes/onboarding-trust-terminology.md`
- `docs/Core-Documents/docs/json_schema/node_onboarding_start_request.schema.json`
- `docs/Core-Documents/docs/standards/Node/frontend-standard.md`
- `docs/Core-Documents/docs/standards/Node/frontend-visual-and-interaction-standard.md`

## Task 001
Original task details:
- Define HexeVoice onboarding around Core's canonical 10-step node lifecycle.
- Use Core terminology: onboarding session, approval decision, registration record, trust activation, and capability activation.
- Separate node-local lifecycle projection from Core-owned readiness projection where Core APIs do not expose every state directly.
- Define the UI implementation boundary around the node frontend and visual standards so the Phase 0 flow uses the canonical Hexe shell and interaction grammar.

## Task 002
Original task details:
- Persist pre-trust setup, onboarding session metadata, trust activation data, and post-trust resume state safely.
- Keep pre-trust local state distinct from trusted identity and trust tokens.
- Preserve enough data for restart-safe resume through trusted and post-trust setup states.

## Task 003
Original task details:
- Add backend APIs for local setup drafts covering Node Identity and Core Connection inputs.
- Validate canonical onboarding request fields including `node_name`, `node_type`, `node_software_version`, `protocol_version`, and `node_nonce`.
- Support optional Core-facing metadata such as `hostname`, `ui_endpoint`, `api_base_url`, and optional requested `node_id`.

## Task 004
Original task details:
- Implement bootstrap MQTT connectivity and retained bootstrap metadata discovery against `hexe/bootstrap/core`.
- Validate bootstrap advertisement fields such as `onboarding_endpoints.register_session`, `onboarding_mode=api`, and `onboarding_contract=global-node-v1`.
- Surface transport, discovery, and contract diagnostics for the Bootstrap Discovery step.

## Task 005
Original task details:
- Implement Registration via `POST /api/system/nodes/onboarding/sessions`.
- Conform to the canonical request/response contract and schema from Core docs.
- Handle duplicate active session and duplicate identity failures explicitly.

## Task 006
Original task details:
- Implement Approval visibility using `approval_url`, `session_id`, `expires_at`, and session state.
- Support pending, approved, rejected, expired, cancelled, consumed, and invalid outcomes as applicable to node-local UX.
- Surface the operator-mediated approval flow without requiring embedded browser support.

## Task 007
Original task details:
- Implement Trust Activation via finalize and persist all canonical activation payload fields.
- Securely store `node_id`, `paired_core_id`, `node_trust_token`, baseline policy metadata, and operational MQTT credentials.
- Enforce one-time finalize handling semantics locally so stale approved sessions are not reused unsafely.

## Task 008
Original task details:
- On trusted restart, resume safely without repeating onboarding when trust remains valid.
- Use `GET /api/system/nodes/trust-status/{node_id}` to distinguish supported, revoked, and removed states.
- Provide explicit re-onboarding and recovery behavior after revocation, removal, or invalid trust state.

## Task 009
Original task details:
- Implement node-local Provider Setup state as the canonical post-trust blocked state `capability_setup_pending`.
- Track provider readiness, supported providers, enabled providers, and blocking reasons needed before capability declaration.
- Keep provider setup node-local while preserving Core's readiness authority.

## Task 010
Original task details:
- Implement Capability Declaration and Governance Sync backend behavior using the Core Phase 2 contracts.
- Support capability declaration submission, governance fetch/refresh, and operational-status polling.
- Model `capability_status`, `governance_sync_status`, `operational_ready`, and freshness fields from Core.

## Task 011
Original task details:
- Expand node-local status APIs so the frontend can render the canonical 10-step progression.
- Return lifecycle projection, trust state, readiness flags, provider-setup details, and blocking reasons.
- Keep `operational_ready` as the source of truth for final readiness when lifecycle labels and readiness differ.

## Task 012
Original task details:
- Implement the shared Hexe node visual foundation in HexeVoice before step-specific screens.
- Add the standard dark token set, atmospheric shell background, centered page frame, card styling, buttons, pills, callouts, forms, and responsive breakpoints.
- Match the standard shell and layout behavior so later onboarding screens inherit the same look and feel as HexeEmail.

## Task 013
Original task details:
- Replace the starter onboarding panel with a real onboarding shell driven by the Core 10-step lifecycle.
- Add the standard hero card, setup-flow sidebar, numbered step list, stage-card surface, status pills, and semantic callout patterns.
- Keep the UI aligned to canonical lifecycle names even where backend readiness is projected separately.

## Task 014
Original task details:
- Implement frontend steps 1 through 3 for Node Identity, Core Connection, and Bootstrap Discovery.
- Include local draft save/resume, bootstrap connectivity testing, and bootstrap advertisement inspection.
- Gate progression on backend-validated setup state rather than client assumptions.

## Task 015
Original task details:
- Implement frontend steps 4 through 6 for Registration, Approval, and Trust Activation.
- Show registration errors, session metadata, approval URL, finalize outcomes, and activation completion.
- Support retry/recovery flows for duplicate session, rejection, expiry, invalid, and consumed responses.

## Task 016
Original task details:
- Implement frontend steps 7 through 10 for Provider Setup, Capability Declaration, Governance Sync, and Ready.
- Show provider readiness gates, capability declaration progress, governance freshness, and the final operational-ready review.
- Include trusted fast-path resume behavior when accepted capability state and fresh governance already exist.

## Task 017
Original task details:
- Build the post-setup operational overview surfaces so HexeVoice does not stop at the wizard.
- Use the node UI standard patterns for overview cards, facts/state-grid presentation, warning banners, grouped action rows, and operator-readable status summaries.
- Keep setup concerns and post-setup operational concerns visibly separated while still sharing one shell.

## Task 018
Original task details:
- Add targeted backend tests for bootstrap discovery, onboarding session start, approval/finalize handling, trust-status recovery, capability declaration, governance sync, and readiness projection.
- Add frontend validation for themed shell rendering, setup progression, and key responsive/status patterns where practical.
- Update operator documentation to describe both the Core-defined 10-step flow and the required Hexe visual/operator experience.

## Task 019
Original task details:
- Review the current implementation and compare it against the new roadmap baseline assumptions.
- Confirm what is real, partially implemented, placeholder-only, or missing.
- Inspect backend voice-related modules under `src/hexevoice/`.
- Inspect frontend voice/dashboard/setup surfaces under `frontend/src/`.
- Inspect firmware voice/audio/UX modules under `firmware/main/`.
- Produce a concise baseline inventory in a new doc under `docs/`.
- The audit output must clearly label each area as `implemented`, `partial`, `scaffold`, or `missing`.
- The audit must explicitly cover:
  - onboarding / trust / lifecycle
  - dashboard shell
  - ESP32 microphone + VAD loop
  - text assistant endpoint
  - voice pipeline
- Every claim in the audit must point to real files in the repo.

## Task 020
Original task details:
- Convert the baseline audit into a clear architecture note that states what the backend owns today and what the firmware owns today.
- Update `docs/architecture.md`.
- Add a dedicated section for the wake-driven architecture direction from the roadmap.
- Explicitly separate:
  - current implementation
  - intended target direction
- Clarify:
  - backend as orchestration authority
  - firmware as transport/audio/UX endpoint
  - what is not yet implemented
- The document must no longer imply that the full voice pipeline already exists.
- The document must explicitly mark wake/STT/TTS/session orchestration as incomplete where appropriate.

## Task 021
Original task details:
- Establish a reliable Phase 0 record of the backend APIs that already support endpoint and voice-adjacent behavior.
- Review `src/hexevoice/main.py`, `src/hexevoice/api/models.py`, `src/hexevoice/assistant/`, and `src/hexevoice/endpoint/`.
- Document the currently available routes, request contracts, and response contracts in a new or existing doc.
- Identify which routes are production candidates versus temporary scaffolds.
- The API notes must cover:
  - `POST /api/assistant/turn`
  - endpoint heartbeat/status routes
  - any related node-status routes useful to endpoint UX
- The documentation must clearly distinguish:
  - stable starter routes
  - temporary stub routes
  - routes that must be replaced in Phase 1

## Task 022
Original task details:
- Confirm what the ESP32 firmware actually does today versus what the roadmap says Phase 1 needs.
- Review the firmware entrypoint and relevant modules:
  - `firmware/main/app_main.cpp`
  - `firmware/main/board/audio.cpp`
  - `firmware/main/ui/`
  - `firmware/main/voice/`
- Document:
  - what boots successfully
  - what hardware paths are initialized
  - what is only stub/log output
- Produce firmware baseline notes under `docs/`.
- The notes must explicitly confirm:
  - microphone/audio initialization status
  - current VAD behavior
  - display/UX state handling
  - wake/STT/TTS/client scaffold status

## Tasks 085-092
Original task details:
- Change the endpoint UI to use composited assets instead of only full-screen state images.
- There is a background layer, backed by one or more SD files.
- There is an avatar layer with alpha support for states such as idle, thinking, error, listening, and talking.
- There are general avatar scene types, for example a clock scene composed from background + avatar + clock hands + date.
- There are sprites for buttons, icons, and similar UI elements.
- Keep the existing SD folders as the first storage contract:
  - `/sdcard/hexe/pictures` for backgrounds and full-screen images.
  - `/sdcard/hexe/sprites` for avatars, alpha masks, buttons, icons, manifests, and smaller overlays.
  - `/sdcard/hexe/sounds` for audio assets.
- Prefer a manifest-driven scene model so UI behavior can change from SD assets without reflashing firmware.

Implementation notes:
- Task 085 should define the manifest schema and naming convention before renderer changes.
- Task 086 should preserve the current simple fallback drawing when SD assets are missing.
- Task 087 should choose an alpha representation appropriate for ESP32 memory, likely RGB565 plus an alpha mask for avatar/sprite assets rather than full RGBA framebuffers.
- Task 089 should keep dynamic scene types data-driven enough for clock/date without hardcoding every future avatar type.
- Task 090 should align button/icon sprites with the touchscreen interaction layer from Task 064.

## Task 023
Original task details:
- Make the project docs truthful and Phase-0-safe.
- Review `README.md`, `docs/architecture.md`, and any other relevant docs.
- Remove or soften wording that suggests a real wake-to-reply pipeline already exists when it does not.
- Keep the docs optimistic, but accurate.
- A new contributor must be able to read the docs and correctly understand:
  - what already works
  - what is stubbed
  - what belongs to Phase 1

## Task 024
Original task details:
- Turn the audit into an actionable list of the concrete missing pieces required before or during Phase 1.
- Create a gap-analysis section in a roadmap-adjacent doc.
- Break the missing work into at least these categories:
  - backend transport
  - wake detection
  - session lifecycle
  - STT/TTS integration
  - firmware transport
  - firmware playback
  - dashboard observability
- Produce a clear Phase 0 gap list with short descriptions.
- Each gap must be mapped to either:
  - backend
  - firmware
  - frontend
  - docs/testing

## Task 025
Original task details:
- Record the smallest set of decisions needed to unblock Phase 1 implementation without pretending the whole protocol is finalized.
- Update `docs/voice-node-phase-1.md`.
- Add a short `Phase 1 provisional assumptions` section covering:
  - single-endpoint MVP
  - backend wake authority
  - WebSocket-first transport direction
  - firmware as audio/UX/transport endpoint
  - no raw audio persistence in MVP
- The assumptions section must be concise and clearly labeled provisional.
- The assumptions must be consistent with `docs/voice-node-roadmap.md`.

## Task 026
Original task details:
- Ensure the repo has enough automated validation to protect the current Phase 0 baseline before deeper voice work begins.
- Run the relevant backend tests.
- Run the frontend production build.
- Add or adjust lightweight tests only if the audit reveals uncovered baseline behavior that is already implemented.
- Existing test suite must pass.
- Frontend build must pass.
- Any new tests added must be narrowly scoped to current implemented behavior, not speculative future behavior.

## Task 027
Original task details:
- Close the loop once the baseline audit and cleanup work are complete.
- Add a short Phase 0 completion note to the relevant docs.
- Ensure `docs/voice-node-roadmap.md` and `docs/voice-node-phase-1.md` point to the new baseline artifacts where helpful.
- Produce a clean handoff from Phase 0 into Phase 1 planning/implementation.
- The docs set must be internally consistent.
- A future implementation pass must be able to start from the baseline audit without re-discovering the same context.

## Task 028
Original task details:
- Use `docs/voice-node-phase-0-baseline.md` and `docs/voice-node-phase-1.md` as inputs.
- Define a backend-owned voice event envelope for endpoint-to-backend and backend-to-endpoint messages.
- Define the MVP single-endpoint session lifecycle and state transitions.
- Keep endpoint connection state, endpoint UX state, and backend session state separate.
- Do not implement audio processing in this task unless required for contract validation.
- Add targeted tests or schema validation for the event/session models if code is introduced.

## Task 029
Original task details:
- Add `/api/voice/ws` with an in-memory single-endpoint session manager.
- Support one endpoint and one active session for MVP.
- Accept session/control events and audio chunk metadata using the event envelope from Task 028.
- Return session state, error, and completion events through the same envelope.
- Keep endpoint persistence out of the critical path unless the existing code requires a tiny local store for correctness.
- Add focused backend tests for connection, event validation, session start, audio chunk handling, cancel, and error cases.

## Task 030
Original task details:
- Add the backend openWakeWord audio intake path.
- Use backend openWakeWord as the canonical wake authority.
- Accept audio from the Task 029 WebSocket path or the smallest compatible intake boundary from that implementation.
- Keep firmware VAD as an optional early signal only.
- Provide a testable adapter boundary so development can run with a deterministic fake wake detector.
- Emit wake/session events through the Task 028 event envelope.
- Do not persist raw audio.
- Do not require final STT/TTS provider wiring in this task.

## Task 031
Original task details:
- Implement firmware backend client configuration and connection behavior.
- Use `firmware/config/endpoint.yaml` as the MVP source for the HexeVoice node backend address.
- Keep `firmware/config/endpoint.example.yaml` as the committed template and keep machine-specific `endpoint.yaml` gitignored.
- Load or generate firmware constants from YAML as part of the build/development workflow; do not hardcode the node IP address in source.
- Include `endpoint_id`, HTTP host/port, WebSocket host/port, heartbeat path, voice WebSocket path, and audio format settings in the YAML contract.
- Defer automatic discovery until after the first single-endpoint loop works.
- Send endpoint heartbeat and metadata to the backend.
- Add audio chunk transport from the existing microphone path toward the backend voice WebSocket or agreed MVP transport.
- Keep buffering bounded and failure behavior explicit.
- Do not move wake authority to firmware.

## Task 032
Original task details:
- Add backend STT and TTS provider adapter boundaries for the first real voice loop.
- Wire transcript finalization into the existing assistant turn service.
- Wire assistant response text into TTS synthesis output metadata or audio handles.
- Include deterministic fake adapters for tests/development if real providers are unavailable.
- Preserve the privacy rule that raw audio is not persisted by default.

## Task 033
Original task details:
- Implement firmware TTS receive/playback behavior for backend responses.
- Map backend events to endpoint UX states such as idle, listening, thinking, speaking, muted, and error.
- Support stop/mute button behavior against the backend session contract where practical.
- Keep existing display and app state conventions unless a small local adjustment is required.

## Task 034
Original task details:
- Replace placeholder-only voice endpoint dashboard cards with live backend data.
- Show endpoint connection state, active session state, last transcript, last response, last error, and transport health.
- Wire operator actions for refresh, test assistant turn, stop session, replay response, mute endpoint, and reconnect as supported by backend APIs.
- Keep the dashboard aligned with the existing Hexe node visual shell.

## Task 035
Original task details:
- Integrate the completed backend, firmware, and frontend pieces into the first single-endpoint wake-to-reply loop.
- Validate the happy path:
  - endpoint connects and reports heartbeat
  - endpoint sends microphone audio
  - backend wake authority accepts a wake event
  - backend captures/transcribes/routes the turn
  - assistant response is synthesized or represented by the configured TTS adapter
  - firmware enters speaking state and plays or handles the response payload
  - frontend shows endpoint state, session state, transcript, response, and errors
- Validate cancel/error behavior across backend, firmware, and dashboard where supported.
- Run targeted backend tests, frontend build, and the smallest practical firmware build or compile validation.
- Update `docs/voice-node-phase-0-baseline.md`, `docs/voice-node-phase-1.md`, or a new Phase 1 handoff note to reflect what became real.
- Do not mark the task complete unless the repo has a documented verification result for the integrated loop.

## Task 044
Original task details:
- Existing container found on 04/25/2026:
  - name: `openwakeword`
  - image: `rhasspy/wyoming-openwakeword`
  - port: `10400`
  - restart policy before intervention: `unless-stopped`
  - compose project: `homeassistant`
  - compose file: `/home/dan/Projects/HomeAssistant/docker-compose.yml`
  - custom model mount: `/home/dan/Projects/HomeAssistant/openwakeword/models:/custom`
- Already performed manually:
  - `docker update --restart=no openwakeword`
  - `docker stop openwakeword`
- Acceptance criteria:
  - Document that Docker restart has been disabled for the old container.
  - Add a note that HomeAssistant compose can still recreate/start it if that external stack is launched.
  - Do not edit the HomeAssistant repository from this repo unless explicitly requested.

## Task 045
Original task details:
- Add a HexeVoice-owned openWakeWord container definition.
- Use the existing working image unless a better image is deliberately chosen: `rhasspy/wyoming-openwakeword`.
- Preserve the custom model directory behavior and migrate/copy/reference the trained wake model from the old HomeAssistant path or the current local model path.
- Choose a node-local model/config location that is committed as a template but keeps trained model binaries out of git.
- Ensure restart behavior is controlled by the node/supervisor design, not by a standalone Docker `unless-stopped` policy.
- Add scripts or configuration needed to start, stop, and inspect the service from this repository.

## Task 046
Original task details:
- Register the HexeVoice-managed openWakeWord container/runtime with Core Supervisor.
- Follow the node supervisor contract already used by the backend runtime:
  - Unix socket: `/run/hexe/supervisor.sock`
  - register route: `POST /api/supervisor/runtimes/register`
  - heartbeat route: `POST /api/supervisor/runtimes/heartbeat`
- Determine whether registration should be performed by the HexeVoice backend, a helper sidecar, or supervisor metadata/config.
- The service must be supervisor-owned for lifecycle start/stop/restart behavior.
- Add tests or a dry-run validation for the registration payload if code is introduced.

## Task 047
Original task details:
- Add a backend wake provider mode that uses the supervised openWakeWord service instead of in-process openWakeWord.
- Keep deterministic and in-process openWakeWord providers available for development/fallback unless removal is explicitly requested.
- Add configuration for provider selection and service address/port.
- Translate streamed firmware audio into the protocol expected by the openWakeWord service.
- Emit the existing `wake.accepted` and session state events when the service detects the configured wake word.
- Keep raw audio transient and bounded.
- Expose provider health/status through `/api/voice/status`.

## Task 048
Original task details:
- Validate the supervised openWakeWord wake-to-listening path end to end.
- Expected path:
  - firmware streams microphone audio to HexeVoice
  - HexeVoice feeds wake audio to the supervised openWakeWord service
  - openWakeWord detects the trained wake word
  - backend emits `wake.accepted`
  - firmware switches to Listening only after `wake.accepted`
  - dashboard shows wake provider health and last detection metadata
- Confirm the old HomeAssistant-owned container remains stopped and does not auto-restart.
- Run targeted backend tests and the smallest practical runtime smoke test.
- Update the relevant docs with the final operational flow and any remaining tuning notes.

## Task 059
Original task details:
- Title: Persist the endpoint registry and heartbeat-derived endpoint profile
- Goal:
  - Turn the current live endpoint heartbeat into a durable endpoint registration record.
  - Persist `endpoint_id`, `zone_id`, `display_name`, `firmware_version`, `last_seen`, connection metadata, and declared endpoint capabilities.
- Implementation notes:
  - Keep node-owned endpoint identity separate from Core node identity.
  - Heartbeat should upsert runtime health without erasing operator-owned labels or zone assignment.
  - Expose endpoint registry read/update APIs for the frontend.
  - Add backend tests for first heartbeat registration, reconnect update, stale endpoint projection, and operator metadata updates.
- Completion criteria:
  - Endpoint records survive backend restart.
  - Frontend can display and edit endpoint display name/zone.
  - Existing voice-loop heartbeat and WebSocket behavior still works.

## Task 060
Original task details:
- Title: Formalize connection, UX, and session state separation
- Goal:
  - Replace remaining implicit state coupling with explicit `connection_state`, `ux_state`, and `session_state` projections.
  - Preserve the expressive backend session lifecycle: `wake_detected -> listening -> capturing -> transcribing -> routing -> responding -> completed`.
- Implementation notes:
  - Keep firmware display phases mapped from UX state, not raw backend session internals.
  - Add state transition helpers so backend and frontend use the same vocabulary.
  - Update `/api/voice/status` and endpoint dashboard rendering to present the three state families clearly.
- Completion criteria:
  - Backend tests cover state transitions for wake, capture, transcription, response, cancel, error, and reconnect.
  - UI no longer needs to infer connection health from session state.

## Task 061
Original task details:
- Title: Version and validate the endpoint event envelope
- Goal:
  - Make every backend-to-endpoint and endpoint-to-backend event use a documented versioned envelope.
  - Include `event_type`, `event_id`, `session_id`, `endpoint_id`, `timestamp`, `schema_version`, and `payload`.
- Implementation notes:
  - Keep backward compatibility with the current firmware until the endpoint update is pushed.
  - Add structured command acknowledgements and endpoint-side command errors.
  - Reject malformed inbound events with operator-visible diagnostics instead of silent drops.
- Completion criteria:
  - Contract docs exist for event envelope and payload types.
  - Backend tests validate accepted and rejected event shapes.
  - Firmware logs unknown or malformed events with enough detail to debug.

## Task 062
Original task details:
- Title: Complete endpoint command APIs and dashboard controls
- Goal:
  - Expand the current volume command into a complete endpoint command surface.
- Scope:
  - Volume set/get.
  - Mute/unmute.
  - Cancel active session.
  - Replay last response.
  - Optional restart/reconnect command if safe.
- Implementation notes:
  - Commands should include request id, timeout, acknowledgement, and terminal status.
  - Frontend should show pending/succeeded/failed command state.
  - Firmware should handle commands idempotently where possible.
- Completion criteria:
  - Operator can control volume/mute/cancel/replay from the dashboard.
  - Firmware applies supported commands and reports unsupported commands explicitly.
  - Backend and frontend tests cover command lifecycle.

## Task 063
Original task details:
- Title: Add firmware persistent settings and capability reporting
- Goal:
  - Persist local endpoint settings in NVS and report real hardware/software capabilities to the backend.
- Scope:
  - Output volume.
  - Mute state.
  - Touchscreen availability.
  - SD card availability.
  - Display resolution/pixel format.
  - Audio input/output capabilities.
  - Firmware version/build metadata.
- Implementation notes:
  - Use conservative defaults when NVS has no saved value.
  - Keep runtime state and persisted settings synchronized after backend commands or local touch UI changes.
- Completion criteria:
  - Volume and mute survive reboot.
  - Backend receives current endpoint capabilities on heartbeat or registration.
  - Frontend displays capabilities and firmware version.

## Task 064
Original task details:
- Title: Build the first touchscreen interaction layer
- Goal:
  - Move from touch initialization to actual on-device controls.
- Scope:
  - Touch read loop or polling task.
  - Coordinate calibration/normalization.
  - Tap regions for volume up/down or a compact volume overlay.
  - Mute toggle.
  - Basic visual feedback for touch actions.
- Implementation notes:
  - Avoid blocking audio capture/playback tasks.
  - Keep touch UI optional when touch init fails.
  - Preserve current LCD status overlays.
- Completion criteria:
  - Touch input can change endpoint volume locally.
  - Local volume changes update the backend-visible status.
  - Firmware build passes and behavior is safe when the touch controller is unavailable.

## Task 065
Original task details:
- Title: Load and display RGB565 pictures from the SPI SD card
- Goal:
  - Use the new SPI SD mount and RGB565 conversion tool to display card-backed images.
- Scope:
  - Define file naming and manifest convention under `/sdcard/hexe/pictures`.
  - Read full-screen `320x240` raw RGB565 files.
  - Validate file size before display.
  - Add fallback behavior if a file is missing, unreadable, or wrong size.
- Implementation notes:
  - Keep the built-in firmware assets as the safe fallback.
  - Do not block the main UI loop on slow SD reads.
  - Use the new converter output as the canonical SD image format.
- Completion criteria:
  - A converted `.rgb565` file copied to the SD card can be displayed on the endpoint.
  - Bad or missing files are logged and do not crash the firmware.

## Task 066
Original task details:
- Title: Load and play sound assets from the SPI SD card
- Goal:
  - Add card-backed local sounds for cues and future UI audio.
- Scope:
  - Define `/sdcard/hexe/sounds` file format expectations.
  - Support at least WAV PCM files matching the current speaker output path.
  - Add validation for sample rate, channels, bit depth, and size.
- Implementation notes:
  - Preserve existing built-in/local cue behavior as fallback.
  - Avoid concurrent playback conflicts with TTS.
- Completion criteria:
  - Firmware can play a valid cue WAV from SD.
  - Invalid files are rejected with clear logs.
  - TTS playback remains stable.

## Task 067
Original task details:
- Title: Persist voice session history and replay metadata
- Goal:
  - Make recent sessions inspectable and replayable beyond the current in-memory latest-status view.
- Scope:
  - Persist session id, endpoint id, timestamps, lifecycle timings, transcript metadata, assistant metadata, TTS stream metadata, error state, and replay eligibility.
  - Add read APIs for recent sessions and session detail.
  - Add dashboard history view or panel.
- Implementation notes:
  - Avoid persisting raw microphone audio unless a separate debug setting is explicitly enabled.
  - TTS replay can reference cached generated audio when available.
- Completion criteria:
  - Recent voice turns survive backend restart.
  - Dashboard can show recent turns and replay the last eligible response.

## Task 068
Original task details:
- Title: Integrate AI Node assistant routing as the primary assistant path
- Goal:
  - Move Phase 2 from local echo fallback to real AI Node routing through the node contract.
- Scope:
  - Finalize request/response payload with AI Node.
  - Send endpoint/session context and rolling conversation context.
  - Surface AI Node latency, model/provider metadata, and structured errors.
  - Keep local echo fallback for smoke tests and degraded mode.
- Completion criteria:
  - A real assistant turn can route through AI Node when configured.
  - Failures degrade predictably and remain visible in logs/UI.
  - Tests cover success, timeout, and fallback.

## Task 069
Original task details:
- Title: Validate real-device audio providers end to end
- Goal:
  - Complete Phase 2 provider validation on the ESP-BOX endpoint with real microphone and speaker behavior.
- Scope:
  - openWakeWord tuning against ESP microphone audio.
  - faster-whisper local STT latency and accuracy pass.
  - Piper TTS latency and audio quality pass.
  - Speaker/microphone contention regression checks.
- Completion criteria:
  - Documented real-device validation results.
  - Tuned default thresholds/config values are committed.
  - Known limitations are captured with follow-up tasks.

## Task 070
Original task details:
- Title: Update Phase 2 operator docs and release checklist
- Goal:
  - Make Phase 2 reproducible by someone other than the current developer session.
- Scope:
  - Endpoint wiring and SPI SD setup.
  - Image and sound asset conversion workflow.
  - Firmware build and OTA push.
  - Backend provider configuration.
  - Dashboard endpoint controls.
  - Troubleshooting for wake/STT/TTS/SD/touch.
- Completion criteria:
  - Docs describe the current Phase 2 setup from blank machine/card to working endpoint.
  - Release checklist includes backend tests, frontend build, firmware build, OTA push, and real-device smoke test.

## Task 077
Original task details:
- Title: Define the endpoint SD media delivery contract
- Goal:
  - Define how the node sends files to the endpoint for persistent SD storage.
- Scope:
  - Add a versioned contract for media type, asset id, filename, destination, byte size, checksum, content type, pixel/audio metadata, overwrite policy, and activation behavior.
- Destinations:
  - Full-screen UI/background pictures go to `/sdcard/hexe/pictures`.
  - Sprites/items go to `/sdcard/hexe/sprites`.
  - Sound assets go to `/sdcard/hexe/sounds`.
- Completion criteria:
  - Contract docs exist.
  - Allowed file extensions and size limits are documented.
  - Unsafe paths/path traversal are explicitly rejected.

## Task 078
Original task details:
- Title: Add backend media upload and endpoint delivery APIs
- Goal:
  - Let the node accept media files and deliver them to a selected endpoint.
- Scope:
  - Add backend APIs to upload/list/delete media assets.
  - Convert pictures to raw RGB565 when needed.
  - Validate sounds.
  - Compute checksums.
  - Queue endpoint media-transfer commands.
- Completion criteria:
  - Backend tests cover upload validation, destination selection, checksum metadata, duplicate/overwrite behavior, and unsupported asset rejection.

## Task 079
Original task details:
- Title: Add firmware media-transfer command handling
- Goal:
  - Let firmware receive media-transfer commands and write files to the SD card.
- Scope:
  - Implement command acknowledgement.
  - Support streamed or chunked download.
  - Write to a temporary file first.
  - Verify checksum.
  - Atomically rename into `/sdcard/hexe/pictures`, `/sdcard/hexe/sprites`, or `/sdcard/hexe/sounds`.
  - Report clear errors.
- Completion criteria:
  - Firmware can receive a file from the backend, persist it on SD, verify size/checksum, and report success/failure without blocking the voice loop.

## Task 080
Original task details:
- Title: Add endpoint SD media inventory reporting
- Goal:
  - Let the node know what media files are currently stored on the endpoint SD card.
- Scope:
  - Firmware scans pictures, sprites, and sounds directories.
  - Report filename, size, checksum when available, modified time if available, and recognized metadata.
  - Backend persists the latest inventory per endpoint.
- Completion criteria:
  - Dashboard/API can show the endpoint-visible SD media inventory and stale/missing asset state.

## Task 081
Original task details:
- Title: Add sprite/item asset support under `/sdcard/hexe/sprites`
- Goal:
  - Define and load smaller UI item assets separately from full-screen UI pictures.
- Scope:
  - Decide first sprite format, likely raw RGB565 plus metadata for width/height/transparent color or LVGL-compatible C/bin format if LVGL is adopted.
  - Add conversion tooling.
  - Add firmware loading/drawing hooks.
- Completion criteria:
  - A sprite asset can be delivered to `/sdcard/hexe/sprites`, loaded by firmware, and drawn over a full-screen UI image.

## Task 082
Original task details:
- Title: Add SD sound asset delivery and playback integration
- Goal:
  - Let the node deliver sound files to `/sdcard/hexe/sounds` and let firmware use them for local cues.
- Scope:
  - Validate WAV PCM metadata.
  - Deliver files to SD.
  - Inventory sounds.
  - Select cue names for wake/listen/error.
  - Keep TTS playback conflict-safe.
- Completion criteria:
  - A delivered WAV cue can be played by firmware.
  - Invalid audio is rejected with useful errors.
  - Existing TTS playback remains stable.

## Task 083
Original task details:
- Title: Add dashboard media manager for endpoint SD assets
- Goal:
  - Give the operator a UI to send pictures, sprites, and sounds to the endpoint.
- Scope:
  - Add upload controls.
  - Add conversion options.
  - Add destination selection.
  - Add overwrite prompts.
  - Add transfer progress.
  - Add checksum/status display.
  - Add endpoint inventory view.
  - Add delete/replace actions.
- Completion criteria:
  - Operator can upload a full-screen UI picture, sprite/item, or sound from the dashboard and see it arrive in the endpoint SD inventory.

## Task 084
Original task details:
- Title: Add media transfer validation and recovery tests
- Goal:
  - Make media delivery reliable enough for repeated endpoint customization.
- Scope:
  - Test interrupted transfer cleanup.
  - Test checksum mismatch.
  - Test full SD card.
  - Test missing SD card.
  - Test unsupported file type.
  - Test oversized file.
  - Test duplicate filenames.
  - Test endpoint reconnect during transfer.
- Completion criteria:
  - Automated backend tests and firmware/manual validation notes cover the failure modes, with clear operator-facing errors.

## Task 085
Original task details:
- Title: Define the Voice Node registered-intent contract
- Goal:
  - Let clients register voice intents with the Voice Node the same way AI Node registers prompt services.
- AI Node pattern to mirror:
  - Local registry/state store with a normalized JSON contract.
  - Register, update, list, inspect, lifecycle, review, and status snapshot behavior.
  - Id/version lifecycle so definitions can evolve without silently changing old behavior.
- Scope:
  - Define an intent record with `intent_id`, `intent_name`, `service_id`, `owner_service`, `owner_client_id`, `version`, `status`, `privacy_class`, `access_scope`, `definition`, `constraints`, `metadata`, `created_at`, and `updated_at`.
  - Define the intent `definition` shape for utterance examples, slot schema, matching hints, dispatch target/action, response behavior, and safety/permission requirements.
  - Decide allowed lifecycle states, including active, restricted, review_due, probation, retired, and expired if applicable.
- Completion criteria:
  - Contract docs and JSON schema exist.
  - The contract is compatible with later Core service resolution and local Voice Node dispatch.

## Task 086
Original task details:
- Title: Add Voice Node local intent registry storage and APIs
- Goal:
  - Persist and manage registered intents locally in the Voice Node.
- Scope:
  - Add an intent registry/store similar to AI Node `PromptRegistry` and prompt service state store.
  - Add APIs to register, update, list, inspect, retire, transition lifecycle, and review intents.
  - Deny duplicate active intent IDs and permit replacement only after retirement.
  - Return a state snapshot that includes configured/registered counts and last update time.
- Completion criteria:
  - Registered intents survive restart.
  - Invalid intent definitions are rejected with clear errors.
  - Unit/API tests cover registration, update, lifecycle, duplicate handling, and persistence.

## Task 087
Original task details:
- Title: Declare Voice Node intent-registration capabilities and endpoint metadata
- Goal:
  - Let Core resolve that Voice Node supports intent registration.
- Scope:
  - Add capability declarations for at least `voice.intent.register`, `voice.intent.list`, and `voice.intent.dispatch`.
  - Include endpoint metadata for the registration/list/dispatch APIs in the declaration payload using the existing capability declaration schema.
  - Include useful limits/constraints in metadata, such as supported matcher modes, max examples, slot schema support, and lifecycle support.
  - Keep implementation node-side; do not require Core schema changes unless Core already supports the metadata field.
- Completion criteria:
  - Core service resolution can return Voice Node as a provider for intent-registration capability requests.
  - The resolved service metadata is enough for a client to discover how to call the Voice Node intent APIs.

## Task 088
Original task details:
- Title: Route local assistant command handling through registered intents
- Goal:
  - Make Voice Node use registered intents for local command handling instead of hardcoded-only behavior.
- Scope:
  - Use the timer command as the first migrated built-in registered intent.
  - Match recognized text against active registered intents.
  - Validate extracted slots against the registered intent definition before dispatch.
  - Preserve existing timer behavior, MQTT timestamp handling, and response shape.
  - Report useful failures for unregistered, disabled, ambiguous, invalid-slot, and unauthorized intents.
- Completion criteria:
  - Timer commands still work after migration.
  - A newly registered intent can be matched and dispatched in a controlled test.

## Task 089
Original task details:
- Title: Add setup/dashboard controls for Voice Node intents
- Goal:
  - Let operators inspect and control registered intent declarations after setup.
- Scope:
  - Add UI controls to show known built-in and custom intents.
  - Allow selecting, declaring, undeclaring, enabling, disabling, and reviewing intents.
  - Show intent id, version, status, owner, capability declaration state, and last update time.
  - Keep provider setup accessible after initial setup completion.
- Completion criteria:
  - Operator can redeclare or undeclare Voice Node intent capabilities without editing files by hand.
  - UI reflects current registry and declaration state after refresh/restart.

## Task 090
Original task details:
- Title: Add tests and operator docs for Voice Node intent registration
- Goal:
  - Make the intent registration workflow repeatable and safe to operate.
- Scope:
  - Test registry persistence, API validation, capability declaration payloads, service resolution metadata, and dispatch behavior.
  - Document request/response examples for registering, updating, listing, and dispatching intents.
  - Document how the Voice Node intent workflow maps to the AI Node prompt registration pattern.
  - Add troubleshooting notes for unresolved capability, undeclared intent, disabled intent, and invalid definition failures.
- Completion criteria:
  - Targeted tests pass.
  - Docs include enough payload examples for another node to discover and call the service.

## Task 091
Original task details:
- Title: Add declarative required data extraction to registered intents
- Goal:
  - Let every registered intent declare which data must be extracted and normalized without adding intent-specific code.
- Scope:
  - Extend the intent definition contract with a required extraction schema.
  - Support named slots extracted from regex groups, examples, and future resolver outputs.
  - Support required/optional fields, type validation, enums, defaults, aliases, units, and normalized output names.
  - Support derived fields where the platform can compute common values such as `requested_at`, `duration_hhmmss`, and request latency timestamps.
  - Migrate the built-in `timer.create` intent to declare required extracted data such as `duration_seconds`, `duration_text`, and `requested_at`.
  - Return clear errors for missing or invalid required extracted data.
- Completion criteria:
  - Intent registration rejects invalid extraction contracts.
  - Generic intent dispatch validates required extracted data before returning a match or publishing follow-on events.
  - Timer works through the declarative extraction contract with tests for duration parsing and required data validation.

## Task 092
Original task details:
- Title: Emit reusable voice intent recognized events from validated intent matches
- Goal:
  - Publish a generic `voice.intent.recognized` event whenever an active registered intent is matched and required extracted data is valid.
- Scope:
  - Define the reusable event payload schema and docs.
  - Include common fields such as endpoint/session, intent id/name/version, command, provider, recognized text, slots, normalized parameters, confidence, registry metadata, and dispatch intent.
  - Keep domain-specific action events separate from recognition events.
  - Add privacy controls for transcript inclusion and slot redaction.
  - Ensure dry-run dispatch can report the event payload preview without publishing it.
- Completion criteria:
  - Recognized intent events can be consumed by other nodes without knowing the intent-specific code path.
  - Timer recognition emits the generic event and still publishes the existing timer create event when configured.
  - Tests cover recognized, not recognized, invalid extracted data, and disabled intent cases.

## Task 093
Original task details:
- Title: Add optional registered-intent reply audio generation with pullable TTS asset links
- Goal:
  - Allow an intent definition to request that Voice Node synthesize the spoken reply and include a pullable audio URL in the response/event payload.
- Scope:
  - Extend intent definitions with reply behavior such as text template, whether TTS is required, provider/model/voice/language/format hints, TTL, and cache policy.
  - Allow an intent to request a specific TTS provider or model when generating reply audio, while still supporting the node default when no model is specified.
  - Generate TTS only after an intent match passes required data validation.
  - Include audio metadata in assistant responses and reusable intent events when requested: `audio_url`, `content_type`, `stream_id`, `duration_ms`, and expiry.
  - Use the event ID as the stable basename for generated reply audio files, with a matching JSON sidecar file that records the spoken text and readiness state.
  - Include `voice_ready` in the sidecar JSON so endpoints and other nodes can confirm the audio file is ready to pull.
  - Make intent-generated voice files valid for 5 minutes by default, with `expires_at` recorded in the response/event payload and sidecar JSON.
  - Reuse existing local TTS and media URL behavior rather than adding a new storage mechanism unless necessary.
  - Define failure behavior when TTS generation fails: fail the intent, return text-only, or mark audio unavailable based on intent policy.
- Completion criteria:
  - A registered intent can opt into reply audio generation without code changes.
  - The generated audio link can be pulled by an endpoint or another node before expiry.
  - Tests cover text-only, audio-required, audio-best-effort, and TTS failure paths.

## Task 094
Original task details:
- Title: Add background cleanup for expired generated voice artifacts every 5 minutes
- Goal:
  - Remove expired generated voice/audio artifacts without waiting for another synthesize or audio fetch request.
- Scope:
  - Add a backend background cleanup loop named/configured as `every_5_minutes`.
  - Run generated voice artifact cleanup every 5 minutes while the backend is active.
  - Clean up audio files and matching JSON sidecar metadata files together.
  - Preserve existing opportunistic cleanup on synthesize and fetch.
  - Log cleanup failures without crashing the backend.
  - Add tests for expired artifact deletion, non-expired artifact preservation, sidecar/audio pair deletion, and cleanup error tolerance.
- Completion criteria:
  - Expired generated voice artifacts are deleted within one cleanup interval during normal backend runtime.
  - The cleanup loop is observable in logs/status without producing noisy logs.

## Task 095
Original task details:
- Title: Create JSON schemas for registered intent contracts under docs/json-chemas-intents
- Goal:
  - Document and validate the new registered-intent contract, reusable intent events, extraction schema, reply audio metadata, and sidecar JSON payloads.
- Scope:
  - Create `docs/json-chemas-intents/`.
  - Add JSON schemas for intent registration/update payloads, intent definition/extraction contract, `voice.intent.recognized` event payloads, reply audio options, and generated voice sidecar JSON.
  - Include examples for `timer.create`, a generic command intent, and an intent that requests reply audio generation.
  - Align schema fields with the implementation tasks for required extracted data, optional intent-specific data, dispatch metadata, privacy/redaction controls, TTS provider/model selection, event-id-based filenames, and `voice_ready`.
  - Add a README that explains schema purpose, versioning, and how clients should use the schemas before registering intents.
- Completion criteria:
  - The schemas are checked into `docs/json-chemas-intents/`.
  - Schema examples validate against the documented contract.
  - The docs are clear enough for another node to register an intent without reading backend code.

## Task 096
Original task details:
- Title: Add Invoke Intent action beside Test Intent in the dashboard UI
- Goal:
  - Let operators trigger the real intent execution path from the Intents dashboard after using the dry-run tester.
- Scope:
  - Add an `Invoke Intent` action next to the existing `Test Intent` dry-run control.
  - Keep `Test Intent` as match/preview only.
  - Make `Invoke Intent` call a backend path that performs real validated intent execution, including domain-event dispatch and optional reply audio generation when configured.
  - Clearly show the invocation result, including matched intent, dispatch status, generated event id, reply text, audio link/sidecar readiness when available, and any failure reason.
  - Add a confirmation or clear visual distinction for intents with side effects.
  - Ensure disabled or invalid intents cannot be invoked.
- Completion criteria:
  - Operators can test an utterance without side effects, then intentionally invoke it from the same dashboard area.
  - Timer invocation from the UI sends the real timer event and reports the publish decision.
  - Tests cover dry-run-only behavior, successful invoke, disabled intent, invalid required data, and failed downstream dispatch.

## Task 117
Original task details:
- Title: Move the external faster-whisper STT service into a dedicated `src/stt/` package boundary
- Scope:
  - Create `src/stt/` as the STT-owned runtime package, separate from the HexeVoice backend package.
  - Move the FastAPI STT service entrypoint out of `src/hexevoice/stt_service.py`.
  - Move or wrap the faster-whisper STT adapter code so STT runtime code does not live only inside `hexevoice.voice.pipeline`.
  - Keep the backend-facing STT adapter contract stable while the implementation moves.
  - Preserve the existing `/health`, `/preload`, and `/transcribe` HTTP API.
  - Keep startup preload behavior for `VOICE_STT_PRELOAD=true`.

## Task 118
Original task details:
- Title: Update STT service launch, tests, and docs after the `src/stt/` package split
- Scope:
  - Update `STT_CMD`, `scripts/stack.env.example`, systemd template expectations, and any control scripts to launch the new module path.
  - Update imports and tests that reference `hexevoice.stt_service`.
  - Add migration notes for existing installed `hexevoice-stt.service` units.
  - Verify Supervisor registration still reports `faster_whisper_stt` with the same service id and control path.

## Task 119
Original task details:
- Title: Move the Piper TTS service into a dedicated `src/tts/` package boundary
- Scope:
  - Create `src/tts/` as the TTS-owned runtime package, separate from the HexeVoice backend package.
  - Move the Piper FastAPI service code out of `services/piper_tts/app.py` into the new package.
  - Keep the Docker service wrapper thin, importing or launching the new `src/tts/` module path.
  - Preserve existing TTS service HTTP APIs, including health, synthesize, model listing, and runtime settings behavior.
  - Keep model warmup behavior, conversion sample-rate handling, sidecar generation, and cleanup contracts unchanged.
  - Keep backend TTS orchestration APIs stable while the service implementation moves.

## Task 120
Original task details:
- Title: Update TTS service launch, tests, and docs after the `src/tts/` package split
- Scope:
  - Update Dockerfile/module launch paths, service imports, and any scripts that reference `services/piper_tts/app.py`.
  - Update tests that load or import the Piper TTS service directly.
  - Add migration notes for existing `hexevoice-piper-tts` Docker runtime expectations.
  - Verify Supervisor registration still reports `piper_tts` with the same service id, container name, and control path.

## Task 121
Original task details:
- Title: Dockerize local STT/TTS engines and move them to Unix-socket transport
- Scope:
  - Dockerize the external faster-whisper STT service so STT runs as a HexeVoice-owned container instead of a user systemd Python process.
  - Keep Piper TTS as a HexeVoice-owned container and update its runtime shape as needed for socket transport.
  - Replace local voice engine TCP host/port communication with Unix domain sockets as the normal and required runtime path.
  - Use a host runtime socket directory, such as `runtime/sockets/`, mounted into Dockerized engine containers.
  - Allow STT and TTS engines to listen on sockets such as `stt.sock` and `tts.sock`.
  - Update backend clients to use `httpx` Unix-socket transport for local STT/TTS calls.
  - Remove normal TCP exposure for local STT/TTS engines.
  - Keep any TCP mode limited to an explicit development/debug override, not the production default.
  - Add socket cleanup on service startup so stale socket files do not block restarts.
  - Add a small shared Python health-ping helper for HexeVoice-owned engine containers that can call the node health/registration endpoint over the mounted Unix socket.
  - Include the health-ping helper in the STT and TTS Docker images or mounted runtime scripts, and make it report engine identity, version/config summary, container hostname, health state, and last error without exposing secrets.
  - Ensure the backend/node side has a Unix-socket route for local engine health pings and records the latest engine heartbeat/status for Runtime UI and Supervisor metadata.
  - Evaluate whether the wake word container should use the same health-ping helper or remain covered by Wyoming/container health checks; document the decision.
  - Document Docker volume, permissions, socket ownership, health-check, health-ping, and Supervisor visibility implications.
- Acceptance criteria:
  - STT and TTS run as local containers by default, with no required TCP ports for backend communication.
  - Backend talks to STT and TTS through mounted Unix sockets.
  - STT and TTS containers can send health pings to the node over the mounted Unix socket.
  - Runtime status shows fresh container health/heartbeat data and clear stale/missing states.
  - TCP remains available only through an explicit development/debug override.

## Task 122
Original task details:
- Title: Make external faster-whisper STT ready immediately after hosted install
- Goal:
  - A fresh `curl ... | bash` install should leave the external faster-whisper STT engine installed, startable, and verifiably healthy without the operator having to discover extra manual STT steps.
- Scope:
  - Extend the hosted installer or a dedicated install helper to perform STT-specific setup after Python dependencies are installed.
  - Ensure `scripts/stack.env` contains a valid default `STT_CMD` using `python -m stt.service`, `VOICE_STT_PROVIDER=external_faster_whisper`, `VOICE_STT_SERVICE_HOST=127.0.0.1`, `VOICE_STT_SERVICE_PORT=10300`, and a CPU-safe default model such as `base.en` with `int8` compute.
  - Add an explicit STT readiness command that installs/renders the user service, starts or restarts it, waits for `/health`, preloads the configured model, and reports actionable errors.
  - Decide whether the installer should always preload/download the configured model or gate that behavior behind an option such as `HEXEVOICE_STT_PRELOAD=true`; default should be practical for a first-use voice node.
  - Add provider setup controls for choosing the default faster-whisper model, additional models to download/preload, device (`cpu` or `cuda`), and compute type.
  - Allow GPU use when the host has compatible NVIDIA drivers/CUDA libraries and the installed CTranslate2/faster-whisper stack supports CUDA; keep CPU `int8` as the safe default.
  - Handle missing host prerequisites gracefully, especially `python3-venv`, systemd user availability, network access for model download, and enough disk space for faster-whisper model/cache files.
  - Keep model/cache downloads out of git and use normal Hugging Face/faster-whisper cache behavior unless a local cache directory is explicitly configured.
  - Update `scripts/faster-whisper-stt-control.sh` if needed so it can run `install`, `start`, `restart`, `preload`, `health`, and `doctor` style checks consistently.
  - Add tests or shell validation for the new installer/control-script behavior without requiring a real model download in CI.
  - Update setup/operations docs with the STT-ready install path and troubleshooting commands.
- Acceptance criteria:
  - After a hosted install on a supported Linux host, the operator can run one documented command and see `hexevoice-stt.service` active with `/health` returning `provider=external_faster_whisper`, configured model details, and `loaded=true` when preload is enabled.
  - Backend service status reports the STT engine as healthy once the STT service is running.
  - Failures name the missing prerequisite or model-download/preload error instead of silently succeeding.

## Task 123
Original task details:
- Title: Make Piper TTS ready immediately after hosted install
- Goal:
  - A fresh hosted install should leave the Piper TTS engine installable, startable, and verifiably healthy without manual Docker/model discovery work.
- Scope:
  - Extend the hosted installer or a dedicated TTS readiness helper to verify Docker/Podman availability, image build/pull behavior, model directory setup, and service start/restart.
  - Add provider setup controls mirroring the STT work: choose default Piper voice/model, choose additional voices to download/preload, and persist those choices through provider setup.
  - Add a model download path for selected Piper voices. Prefer configurable model source metadata rather than hardcoded one-off files; keep model binaries out of git.
  - Decide and document safe default voices for first install, including one lightweight default and optional higher-quality voices.
  - Make the TTS control script support install/build/pull, model download, start/restart, health, preload/warm, and doctor-style diagnostics with actionable errors.
  - Keep existing Piper TTS HTTP APIs, Docker container naming, Supervisor metadata, runtime settings, warm voice behavior, and conversion sample-rate handling stable.
  - Add tests or shell validation that cover control-script/provider-config behavior without downloading real model files in CI.
  - Update setup/operations docs with a TTS-ready install path and troubleshooting commands.
- Acceptance criteria:
  - After a hosted install on a supported host with Docker/Podman available, the operator can run one documented command and see the Piper TTS runtime healthy.
  - Provider setup can select one or more voices to download/preload and mark the default voice.
  - Failures clearly identify missing container runtime, image build/pull failure, model download failure, or health/preload failure.

## Task 124
Original task details:
- Title: Make wake word runtime ready immediately after hosted install
- Goal:
  - A fresh hosted install should leave the wake word runtime installable, startable, and verifiably healthy with at least one configured wake model.
- Scope:
  - Extend the hosted installer or a dedicated wake readiness helper to prepare the configured wake provider, model directory, service/container runtime, and health checks.
  - Add provider setup controls for choosing the default wake word/model, choosing additional wake models to download/copy/preload, and persisting those choices.
  - Include a default `Hexe` wake model, not `Hexa`, under `runtime/openwakeword/models/` as `hexe.*` or download/copy it there during install. Normalize config and docs to use `Hexe` as the default wake word name.
  - Define how trained wake models are sourced: existing migration bundle/runtime copy, local file upload/copy, known repo/release asset, or configured URL. Keep trained model binaries out of git unless explicitly approved.
  - Update `scripts/openwakeword-control.sh` or a companion helper so it can install/sync/download models, start/restart, health-check, preload if supported, and run doctor-style diagnostics.
  - Preserve existing supervised OpenWakeWord/Wyoming behavior and backend wake provider contracts.
  - Add tests or shell validation for setup/control-script behavior without requiring real wake model downloads in CI.
  - Update setup/operations docs with a wake-ready install path and troubleshooting commands.
- Acceptance criteria:
  - After a hosted install and documented wake setup command, the wake runtime is healthy and reports the configured model list.
  - Provider setup can select the default wake model and any additional models to prepare, with `Hexe` available by default.
  - Failures clearly identify missing runtime, missing wake model, model download/copy failure, or wake service health failure.

## Task 125
Original task details:
- Title: Ensure hosted install creates the full runtime directory skeleton
- Goal:
  - A fresh hosted install should always create the runtime directory layout expected by backend, STT, TTS, wake word, firmware, endpoint media, logs, generated UI pages, and migration/runtime artifacts.
- Scope:
  - Add an installer or helper step that explicitly creates the runtime skeleton with `mkdir -p` rather than relying only on tracked `.gitkeep` files.
  - Include at least:
    - `runtime/endpoint_media/`
    - `runtime/endpoint_media/ota/`
    - `runtime/endpoint_media/ui_manifest/`
    - `runtime/firmware/`
    - `runtime/logs/`
    - `runtime/openwakeword/models/`
    - `runtime/piper-tts/models/`
    - `runtime/rendered_node_ui_pages/`
    - `runtime/stt/faster-whisper/`
    - `runtime/voice_tts/`
    - `runtime/wake_recordings/`
  - Ensure directory creation is idempotent and does not overwrite existing runtime state.
  - Consider a single `scripts/prepare-runtime-dirs.sh` helper used by `install.sh`, tests, and docs.
  - Add shell validation or tests that verify the expected directories are created in a temporary install root.
  - Update setup docs to describe which directories are guaranteed empty scaffolding versus populated by model/artifact downloads.
- Acceptance criteria:
  - Running the hosted installer or runtime-dir helper on a clean checkout creates the expected directory tree.
  - Existing files in those directories are preserved.
  - Docs clearly state that model binaries, firmware binaries, and migrated state are separate downloads/imports unless their specific install tasks are run.

## Task 126
Original task details:
- Title: Add firmware artifact download to hosted install
- Goal:
  - The hosted install should be able to fetch the latest compatible endpoint firmware artifacts into `runtime/firmware/` so OTA and endpoint comparison work on a fresh host.
- Scope:
  - Add installer/control-script support for firmware artifact download after app install. The source should be configurable because firmware may live in a separate repository.
  - Support a future separate firmware repository with a configurable repo URL, branch/tag, release asset URL, or GitHub Releases source.
  - Ensure `runtime/firmware/` is tracked/scaffolded and download manifests, binaries, and checksums into it using atomic writes and checksum validation when checksums are available.
  - Preserve board-specific artifacts such as ESP Box and HA Voice PE variants and keep endpoint firmware comparison metadata compatible with existing backend behavior.
  - Avoid committing firmware binaries to this repo as part of the installer work unless explicitly approved.
  - Add docs for configuring `HEXEVOICE_FIRMWARE_REPO_URL`, release/tag selection, offline/manual copy fallback, and verification commands.
  - Add tests or script dry-runs that validate source selection, destination paths, and checksum handling without requiring network downloads in CI.
- Acceptance criteria:
  - A fresh host can run a documented install/firmware command and populate `runtime/firmware/` with the latest selected release artifacts.
  - Backend OTA manifest and endpoint firmware comparison continue to work from the downloaded artifacts.
  - Failures clearly identify missing source configuration, download failure, checksum mismatch, or unsupported board artifact.

## Task 134
Original task details:
- Title: Include STT provider settings in node migration bundles
- Goal:
  - A migration export/import should preserve the STT provider choices needed for a destination host to continue using the same speech-to-text configuration after install.
- Scope:
  - Extend node migration export/import to include STT provider settings when present.
  - Preserve selected provider, faster-whisper model name, additional requested models, preload preference, device choice (`cpu`/`cuda`), GPU enablement, compute type, cache/model directory references, and service/runtime options that are safe to move.
  - Keep downloaded model binaries out of the JSON migration bundle; migrate model selection and download/preload intent, then let install/setup fetch models on the destination host.
  - Validate imported STT settings before writing them so malformed bundles cannot corrupt runtime config.
  - Surface clear import warnings when the destination host must download models, lacks GPU support, or needs the STT service restarted.
  - Add tests covering export, import, missing settings, malformed settings, and GPU/model fields.
  - Update migration docs to explain that STT settings migrate but model files are downloaded or copied separately.
- Acceptance criteria:
  - Exported migration bundles contain an STT settings section when STT provider settings exist.
  - Import restores STT settings and reports any follow-up model download/preload or service restart requirement.
  - Existing migrations without STT settings still import successfully.

## Task 135
Original task details:
- Title: Expand TTS provider settings in node migration bundles
- Goal:
  - A migration export/import should preserve all TTS provider choices, not only the current runtime TTS settings file.
- Scope:
  - Audit the existing `voice_tts_settings` migration behavior and extend it to cover provider setup choices that are not currently included.
  - Preserve selected TTS provider, default Piper voice/model, additional requested voices, preload/warm voices, conversion sample-rate settings, model source metadata, Docker/runtime options, and restart-required state where appropriate.
  - Keep Piper model binaries and generated audio artifacts out of the JSON migration bundle; migrate model choices and download/preload intent instead.
  - Validate imported TTS settings before writing them and return actionable warnings for missing voices, required downloads, container runtime gaps, or service restart needs.
  - Add tests covering current `voice_tts_settings` compatibility, expanded provider settings, missing settings, and malformed settings.
  - Update migration docs to describe exactly which TTS settings migrate and which artifacts must be downloaded/copied separately.
- Acceptance criteria:
  - Exported migration bundles preserve TTS provider and voice/model setup choices.
  - Import restores TTS settings without breaking existing bundles that only contain `voice_tts_settings`.
  - Destination follow-up actions for model downloads, warmup, and restarts are reported clearly.

## Task 136
Original task details:
- Title: Include wake word provider settings in node migration bundles
- Goal:
  - A migration export/import should preserve wake word provider choices and selected wake models for the destination host.
- Scope:
  - Extend node migration export/import to include wake word provider settings when present.
  - Preserve selected provider, enabled state, default wake word/model, additional requested models, model source references, sensitivity/threshold settings, custom model paths, and preload/startup preferences that are safe to move.
  - Keep wake model binaries out of the JSON migration bundle unless a future explicit artifact-copy path is added; migrate model choices and source references instead.
  - Ensure the default wake word/model naming uses `Hexe`, not `Hexa`.
  - Validate imported wake settings before writing them and warn when the destination host needs model download/copy, service restart, or provider setup.
  - Add tests covering export, import, missing settings, malformed settings, default `Hexe` model references, and additional model selections.
  - Update migration docs to explain that wake settings migrate while model binaries are handled by install/download/copy steps.
- Acceptance criteria:
  - Exported migration bundles contain wake word settings when configured.
  - Import restores wake settings and reports required follow-up model/runtime actions.
  - Existing migrations without wake settings still import successfully.

## Task 137
Original task details:
- Title: Add CUDA host preflight, STT benchmark, and CPU fallback validation
- Goal:
  - Before moving HexeVoice to a CUDA-capable host, make it obvious whether the destination can actually run faster-whisper on GPU and how it compares to the CPU fallback.
- Scope:
  - Add a doctor/preflight command that checks NVIDIA driver visibility, `nvidia-smi`, CUDA/cuDNN runtime availability where applicable, Python package compatibility, CTranslate2 CUDA support, and faster-whisper import/load behavior.
  - Verify the configured STT model, device, compute type, preload setting, and cache/model directory without requiring model binaries to be committed.
  - Add a short STT benchmark path using a known small sample or generated fixture so CPU and CUDA profiles can report model load time, transcription time, device, compute type, RAM, and GPU memory when available.
  - Keep CPU `int8` as the required fallback and report a clear warning when CUDA is requested but unsupported.
  - Expose enough status for the provider setup page and runtime service status to show whether CUDA is active, unavailable, or configured but not currently used.
  - Add tests or dry-run validation that can run without a real GPU in CI.
  - Update migration/setup docs with the CUDA preflight command and expected pass/fail outputs.
- Acceptance criteria:
  - On a non-GPU host, the preflight reports CUDA unavailable and confirms CPU fallback without failing the install.
  - On a CUDA-capable host, the preflight can prove faster-whisper/CTranslate2 can load the configured model on GPU.
  - Benchmark output is structured enough to compare CPU and CUDA runs before choosing the production profile.

## Task 138
Original task details:
- Title: Tune endpoint utterance capture and STT silence trimming before CUDA migration
- Goal:
  - Reduce bad or wasteful audio sent to STT before using a larger/faster CUDA-backed model.
- Scope:
  - Combine the future silence-trimming and endpoint-capture tuning work into one pre-migration task.
  - Add or tune STT-side silence trimming so wake tails, pre-roll, and post-speech padding are removed without cutting off the command.
  - Tune endpoint utterance capture duration, micro-VAD pause handling, and backend end-of-speech thresholds against real wake recordings.
  - Use retained wake recordings and micro-VAD debug chunks as fixtures where possible, while keeping those recordings out of git.
  - Report before/after metrics for audio duration sent to STT, transcript quality, latency, and common cutoff/filler cases.
  - Keep defaults conservative and configurable per endpoint/provider.
  - Add tests for trimming boundaries using synthetic audio or lightweight fixtures.
  - Update operations docs with tuning commands and recommended migration defaults.
- Acceptance criteria:
  - Real-device wake recordings produce cleaner STT input without losing the spoken command.
  - Default capture/trimming settings reduce unnecessary STT audio duration compared with the current baseline.
  - Operators can adjust capture and silence settings without code changes.

## Task 139
Original task details:
- Title: Define STT model profiles for fast intent path and accurate fallback
- Goal:
  - Prepare model-selection behavior that can benefit from a CUDA host while still remaining usable on CPU.
- Scope:
  - Define named STT profiles such as `cpu_default`, `cuda_fast_intent`, and `cuda_accurate_fallback`.
  - Map each profile to faster-whisper model, device, compute type, preload/download preference, language, beam size, and fallback behavior.
  - Support a fast intent-first transcription path using a smaller/faster model, with fallback to a higher-accuracy model when confidence, match quality, or intent extraction is weak.
  - Ensure provider setup can persist selected profiles and migration bundles can carry those choices without embedding model binaries.
  - Add status reporting that shows loaded/warm models, active profile, fallback model, and reload-required state.
  - Add tests around profile validation, migration/import compatibility, and fallback decision rules without requiring large model downloads in CI.
  - Document recommended CPU and CUDA profiles for first install and post-migration tuning.
- Acceptance criteria:
  - Operators can choose or migrate named STT profiles instead of hand-editing scattered env vars.
  - The fast intent path can fall back to a more accurate model when needed.
  - Existing single-model STT settings continue to work.

## Task 140
Original task details:
- Title: Auto-detect CUDA-capable STT Docker image during install
- Goal:
  - Hosted install should choose the CUDA faster-whisper STT container only when the destination host proves Docker GPU passthrough works, and otherwise fall back cleanly to the CPU image.
- Scope:
  - Add installer/control-script detection for Docker or Podman availability before selecting an STT image.
  - Detect NVIDIA GPU and driver availability with host checks such as `nvidia-smi` when present.
  - Detect Docker GPU passthrough with a small CUDA smoke container, such as `docker run --rm --gpus all nvidia/cuda:<tag> nvidia-smi`, using a configurable CUDA smoke image/tag.
  - Add an STT image capability check that verifies the selected faster-whisper image can import CTranslate2/faster-whisper and report CUDA availability from inside the container.
  - If CUDA checks pass, set the STT runtime to the CUDA image/profile, `VOICE_STT_FASTER_WHISPER_DEVICE=cuda`, and a CUDA-safe compute type such as `float16`.
  - If any CUDA check fails, keep the CPU image/profile, `VOICE_STT_FASTER_WHISPER_DEVICE=cpu`, and `VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE=int8` without failing the full install.
  - Record the detection result, chosen image/profile, failure reason, and fallback status in install output and runtime/provider status.
  - Integrate with Task 137's CUDA preflight/benchmark path so the install-time detection and operator-facing doctor command share checks where practical.
  - Add CI-safe tests/dry-runs that mock GPU-present, GPU-missing, Docker-missing, and Docker-GPU-broken cases without requiring a real GPU.
  - Update setup docs with override environment variables for forcing CPU, forcing CUDA, or skipping CUDA detection.
- Acceptance criteria:
  - Fresh install on a non-GPU host succeeds with the CPU STT image/profile.
  - Fresh install on a properly configured CUDA host selects the CUDA STT image/profile and reports the proof checks.
  - A host with a GPU but broken Docker GPU passthrough falls back to CPU with a clear warning.

## Task 141
Original task details:
- Title: Evaluate alternative neural TTS engines, including GPU-capable options
- Goal:
  - Decide whether HexeVoice should add a second local TTS engine beyond Piper, especially one that benefits from CUDA on larger hosts.
- Scope:
  - Compare candidate local neural TTS engines for quality, latency, language/voice coverage, licensing, model size, Docker support, CPU performance, and GPU/CUDA support.
  - Include Piper CPU as the baseline.
  - Prefer engines that can run behind the existing TTS provider contract or a small adapter without changing firmware playback expectations.
  - Check whether outputs can be normalized to the existing WAV/sample-rate/artifact sidecar flow.
  - Identify install complexity, VRAM/RAM/disk requirements, and model download behavior.
  - Produce a recommendation: keep Piper only, add an optional experimental provider, or replace the default later.
- Acceptance criteria:
  - A short evaluation doc compares at least two alternatives against Piper.
  - Recommendation includes whether GPU TTS is worth implementing for HexeVoice.
  - Any follow-up implementation task is scoped separately.

## Task 142
Original task details:
- Title: Add migration preflight and dry-run validation
- Goal:
  - Before importing a migration bundle on a new host, operators should get a clear pass/fail report for host readiness and bundle compatibility.
- Scope:
  - Add a migration preflight command or API path that can run before destructive writes.
  - Check Docker/Compose or Podman availability, disk space, Python/npm availability when relevant, required runtime directories, expected ports or socket paths, Core URL reachability, model download/network access, firmware source configuration, and migration bundle schema/version validity.
  - Validate that STT, TTS, wake, firmware, endpoint media, and migration state requirements are either satisfied or have clear follow-up actions.
  - Support a dry-run import mode that validates destination overrides and reports which files/settings would be written without writing them.
  - Return structured results suitable for CLI output and setup-page display.
  - Add tests for missing Docker, low disk, invalid bundle, unreachable Core, missing model source, and successful dry-run.
  - Update docs with the recommended preflight command before migration.
- Acceptance criteria:
  - Operators can run one preflight command and know whether the host is ready for migration.
  - Dry-run import reports planned writes and warnings without modifying runtime state.
  - Failures name the exact missing prerequisite or invalid bundle field.

## Task 143
Original task details:
- Title: Add post-install smoke test command
- Goal:
  - After hosted install and/or migration import, operators should have one command that proves the node is actually usable.
- Scope:
  - Add a smoke test command or API-driven helper that checks backend health, frontend reachability, Core registration/trust state, STT health/preload status, TTS health/model availability, wake word runtime/model availability, firmware artifacts, runtime directory presence, and service-control visibility.
  - Include optional checks for Docker container state, Unix socket availability, CUDA image selection, and migration import status when those features are enabled.
  - Keep checks read-only except for safe health/preload calls explicitly marked as such.
  - Output a concise pass/fail summary plus detailed remediation hints.
  - Add tests or dry-run fixtures that cover pass, partial failure, and unavailable optional components.
  - Document the command as the final step after install or migration.
- Acceptance criteria:
  - A fresh install can run the smoke test and receive a clear readiness summary.
  - The command identifies whether failures are backend, Core trust, STT, TTS, wake, firmware, or artifact related.
  - The smoke test can run safely multiple times.

## Task 144
Original task details:
- Title: Add migration backup and rollback workflow
- Goal:
  - Operators should be able to preserve current node state before migration and recover if the destination setup fails.
- Scope:
  - Add a backup command that exports the migration bundle and creates a timestamped backup of important local runtime state.
  - Include onboarding/trust state, endpoint registry, voice intents, STT/TTS/wake settings, runtime TTS settings, selected service env files, and any lightweight manifests/settings needed to retry migration.
  - Exclude large model binaries, generated audio, logs, and session history by default, with explicit options for including selected local artifacts when needed.
  - Add a rollback/restore command or documented workflow that can restore a backup into the same host or retry import on another host.
  - Treat backups containing trust secrets as sensitive and label them clearly.
  - Add tests for backup manifest creation, redacted vs secret-inclusive export, restore validation, and missing-file tolerance.
  - Update docs with backup, rollback, and retry steps.
- Acceptance criteria:
  - A timestamped migration backup can be created before changing hosts.
  - Backup contents are listed in a manifest and clearly mark whether trust secrets are included.
  - Restore/rollback validates before writing and reports what was restored.

## Task 145
Original task details:
- Title: Define runtime state cleanup and git-tracking policy
- Goal:
  - The repository should not stay dirty after normal runtime operation or hosted install.
- Scope:
  - Classify runtime paths as tracked defaults, generated caches, mutable local state, migration data, large artifacts, logs, or ignored secrets.
  - Decide policy for `runtime/endpoint_registry.json`, `runtime/voice_intents.json`, `runtime/voice_session_history.json`, `runtime/voice_tts_settings.json`, `runtime/rendered_node_ui_pages/*.json`, endpoint media, firmware artifacts, wake models, Piper models, and STT model/cache files.
  - Update `.gitignore` and tracked file layout as needed, using `git rm --cached` only for files that should stop being tracked.
  - Provide seed/default files or installer-created skeleton directories where needed so fresh installs still work.
  - Add a cleanup/status helper that distinguishes source changes from mutable runtime state.
  - Update docs to explain which runtime data migrates through the API, which downloads during install, and which should never be committed.
- Acceptance criteria:
  - Running HexeVoice no longer dirties tracked generated/runtime files during normal operation.
  - Default install assets remain available without committing local mutable state.
  - Docs clearly identify what belongs in git, migration bundles, downloads, and backups.

## Task 146
Original task details:
- Title: Remove trust-secret migration export/import and require Core re-auth
- Goal:
  - Migration should never export or import node trust tokens/secrets; migrated nodes should always receive fresh trust material through Core re-auth.
- Scope:
  - Remove or disable trust-secret-inclusive migration export paths in API and CLI.
  - Reject migration imports that contain trust tokens/secrets.
  - Update migration backup/restore behavior so backups do not include trust tokens/secrets.
  - Update first-setup and operational migration language to say migrated nodes must re-authorize with Core.
  - Add tests for redacted export, import rejection when trust tokens/secrets are present, backup redaction, and re-auth-required messaging.
  - Update docs with the no-secrets migration policy and Core re-auth requirement.
- Acceptance criteria:
  - There is no supported migration path that exports or imports trust tokens/secrets.
  - Bundles containing trust tokens/secrets are rejected before import writes state.
  - Migration UI/CLI clearly says Core re-auth is required after migration.

## Task 147
Original task details:
- Title: Add optional HexeVoice hostname alias during setup
- Goal:
  - A fresh or migrated host should be reachable through a stable `HexeVoice` alias in addition to its current machine hostname, LAN IP, mDNS name, or VPN name.
- Scope:
  - Add setup/install configuration for an optional host alias such as `HexeVoice` and `HexeVoice.local`.
  - Detect the current hostname and existing aliases before making changes.
  - Prefer a reversible, least-surprising mechanism appropriate for Linux hosts, such as documenting `/etc/hosts` changes or configuring local hostname/mDNS aliases when supported.
  - Require explicit operator confirmation or an install environment flag before changing system host files.
  - Avoid breaking the real host name, Tailscale/VPN DNS, DHCP, mDNS, or existing Core URLs.
  - Surface the alias in setup docs and migration/post-install smoke checks.
  - Add tests or dry-run validation for the alias planning logic without requiring root access.
- Acceptance criteria:
  - Setup can optionally add a `HexeVoice` alias for the current host.
  - Existing hostnames and VPN-published names continue to work.
  - The change is documented, reversible, and safe to skip.

## Task 148
Original task details:
- Title: Add setup bootstrap runner for temporary LAN setup UI/API
- Goal:
  - Hosted install should bring up a temporary setup UI/API before production services are ready.
- Scope:
  - Add `scripts/setup-runner.sh`.
  - Run temporary backend on `9100` and temporary frontend/UI on `8180`.
  - Use LAN URL routing, with temporary setup URL `http://<lan-host>:8180/setup/host`.
  - Keep the temp runner alive while production services start.
  - Redirect to `http://<lan-host>:8084/setup/host` after production setup URL is healthy.
  - Stop the temp runner after a configurable delay, default `120` seconds.
  - Support handoff to an existing Supervisor, newly installed Core Supervisor, or unsupervised systemd services.
  - Integrate with Core Supervisor installer modes:
    - `install-supervisor.sh --standalone`
    - `install-supervisor.sh --join-core --core-url <core-url> --enrollment-token <token> --supervisor-id <id>`
  - Support Core one-time enrollment token creation/collection through `POST /api/system/supervisors/enrollment-tokens`.
  - Prefer one-time Core enrollment tokens over admin tokens for joined Supervisor install.
- Acceptance criteria:
  - Fresh install can show the temporary setup page on the LAN.
  - Temp setup redirects to production setup when healthy.
  - Temp runner exits after the configured grace period.
  - Supervisor handoff can target existing, standalone, joined, or unsupervised lifecycle mode.

## Task 149
Original task details:
- Title: Add setup bootstrap status API and installer progress tracking
- Goal:
  - The setup UI should show Step 1 install/download/progress state while bootstrap work is running.
- Scope:
  - Add `GET /api/setup/bootstrap/status`.
  - Persist or expose current bootstrap action, completed actions, pending downloads, failures, retryable failures, and final redirect URL.
  - Wire install/setup scripts to update the status source.
  - Add tests for status payload shape and failure reporting.
- Acceptance criteria:
  - UI can poll one endpoint to display Step 1 progress.
  - Failed firmware/model downloads are visible and retryable from setup.

## Task 150
Original task details:
- Title: Extend hosted install Step 1 for default firmware/model downloads and browser launch
- Goal:
  - Hosted install should prepare default artifacts while the setup UI is visible.
- Scope:
  - Download firmware artifacts during Step 1.
  - Download default STT model `base`.
  - Download default Piper TTS voice/model `en_US-kathleen-low.onnx`.
  - Ensure default wake model `Hexe` is present.
  - Attempt to open the LAN setup URL in a browser; print it clearly if opening fails.
  - Continue to setup UI with retry status when downloads fail.
- Acceptance criteria:
  - Fresh install starts setup UI before model downloads finish.
  - Default artifact download status is visible in setup.

## Task 151
Original task details:
- Title: Implement Host and Node Setup page with readiness, setup mode, and lifecycle mode
- Goal:
  - Replace the narrow Node Identity first step with `/setup/host`.
- Scope:
  - Add `GET /api/setup/host-readiness`.
  - Add targeted readiness actions under `/api/setup/host-readiness/actions/<action>`.
  - Show backend/frontend, LAN URL, runtime dirs, firmware/model status, Docker/CUDA, systemd, Supervisor, host alias, and disk space.
  - Add New Voice Node vs Migrate Existing Voice Node mode selection.
  - Add lifecycle mode display/selection for:
    - existing Supervisor
    - install joined Supervisor with Core enrollment token
    - install standalone Supervisor
    - unsupervised systemd node
  - Joined Supervisor install should use Core one-time enrollment tokens when available and should never persist the one-time token.
  - When joined Supervisor is selected, show an `Open Core enrollment token` button.
  - The button should open the Core enrollment-token page/flow for the selected Core URL.
  - First implementation may require the operator to paste the returned one-time token into HexeVoice setup.
  - Leave room for a future Core callback/return URL flow.
  - Add safe actions for standalone and joined Supervisor install.
  - Save setup mode only when the operator presses Continue.
- Acceptance criteria:
  - `/setup/host` renders from production UI/API.
  - Readiness blockers/warnings match `docs/setup_re-desing.txt`.

## Task 152
Original task details:
- Title: Implement Core Connection and Migration Source setup routes
- Goal:
  - Route Step 3 based on the selected setup mode.
- Scope:
  - New node path: `/setup/core`, Core URL validation, metadata fetch, registration support validation, and save.
  - Migration path: `/setup/migration`, migration bundle upload or local backup selection, preflight/dry-run, destination rewrites, token/secret scan, and redacted import.
  - Reject migration bundles containing trust tokens/secrets.
  - Return migration flow to Core Connection or Re-auth Node as appropriate.
- Acceptance criteria:
  - New node path can save a valid Core connection.
  - Migration path can preflight/import redacted state and rejects token/secret bundles.

## Task 153
Original task details:
- Title: Implement migrated-node re-auth setup step using Core re-auth API
- Goal:
  - Migrated nodes should receive fresh trust material through Core re-auth.
- Scope:
  - Add `/setup/trust/reauth`.
  - Start Core re-auth with `POST /api/system/nodes/reauth/sessions`.
  - Generate and retain a fresh `node_nonce` for finalization.
  - Show Core approval URL `/reauth/nodes/approve?rid=...&state=...`.
  - Finalize with `GET /api/system/nodes/reauth/sessions/{session_id}/finalize?node_nonce=...`.
  - Handle `pending`, `approved`, `rejected`, `expired`, `consumed`, and `invalid`.
  - Save approved activation payload as fresh local trust state.
- Acceptance criteria:
  - Migrated setup can re-authorize and save new trust credentials without imported trust tokens/secrets.

## Task 154
Original task details:
- Title: Implement provider/runtime setup status, config, apply, and polling flow
- Goal:
  - Step 5 should configure STT/TTS/wake/firmware and prove local engine health before continuing.
- Scope:
  - Add `/setup/providers`.
  - Add `GET /api/setup/providers/status`.
  - Add `POST /api/setup/providers/config`.
  - Add `POST /api/setup/providers/apply`.
  - Add targeted actions for downloads, sync, restarts, and health checks.
  - Track provider state as configured/downloading/downloaded/applying/restarting/healthy/warning/failed/skipped.
  - Poll the same status endpoint during apply.
- Acceptance criteria:
  - Continue is blocked until required enabled providers are healthy or explicitly skipped/accepted.

## Task 155
Original task details:
- Title: Implement capability declaration and governance setup step
- Goal:
  - Step 6 should tell Core what this trusted node can do and verify governance.
- Scope:
  - Add `/setup/capabilities`.
  - Add `GET /api/setup/capabilities/status`.
  - Add declare/sync actions.
  - Build declaration from trusted state and provider/runtime health.
  - Fetch and verify governance.
  - Poll status during declaration/governance sync.
- Acceptance criteria:
  - Setup cannot continue until Core has current capabilities and governance is current.

## Task 156
Original task details:
- Title: Implement final smoke-test ready step and setup-mode root redirect
- Goal:
  - Step 7 should be the final gate before leaving setup.
- Scope:
  - Add `/setup/ready`.
  - Add `GET /api/setup/ready/status`.
  - Add `POST /api/setup/ready/run-smoke-test`.
  - Add `POST /api/setup/ready/complete`.
  - Run final smoke checks for backend/frontend, trust, governance, providers, firmware, runtime dirs, sockets, LAN URLs, host alias, and Core node visibility.
  - Save setup-complete state after required checks pass.
  - Redirect `http://<lan-host>:8084/` into the current `/setup/*` page while setup mode is active.
  - After setup completion, keep `8084/` on the local dashboard/fallback surface for now.
- Acceptance criteria:
  - Setup completes only after required smoke checks pass.
  - Root URL redirects into setup while setup is incomplete and stops redirecting after completion.

## Task 125-133
Original task details:
- Title: Start the HexeVoice migration to Core-rendered node UI
- Source docs:
  - `docs/Core-Documents/docs/nodes/future-dev/core-rendered-node-ui-migration.md`
  - `docs/Core-Documents/docs/nodes/ui-mogration/README.md`
  - `docs/Core-Documents/docs/nodes/ui-mogration/node-requirements.md`
- Scope and boundaries:
  - Implement node-side contracts in HexeVoice only; do not change Core behavior from this queue.
  - Keep the existing node-hosted operational dashboard available during the pilot.
  - Treat the local UI as `full` until Core-rendered parity is verified.
  - Expose declarative manifests, data endpoints, detail endpoints, and action endpoints; do not expose React components, arbitrary HTML, scripts, secrets, or giant full-page data payloads.
  - Shape responses for Core card kinds instead of current frontend internals.
  - Preserve node-side authorization, validation, trust boundaries, and operator-safe error payloads for every new endpoint.

Active normalized queue entries:
- Task 125: Inventory HexeVoice dashboard surfaces against the Core-rendered node UI manifest and card contracts.
- Task 126: Add the HexeVoice `/api/node/ui-manifest` pilot for Core-rendered overview, runtime, endpoint, TTS, and intents pages.
- Task 127: Add Core-rendered overview data endpoints for Voice node identity, health strip, warnings, and live facts.
- Task 128: Add Core-rendered runtime and provider status data endpoints for backend, STT, TTS, wake, and Piper services.
- Task 129: Add Core-rendered voice endpoint summary and action-panel data endpoints without removing the local dashboard.
- Task 130: Add Core-rendered registered-intent record list, detail, test, and invoke surfaces.
- Task 131: Add Core-rendered TTS model, artifact, wake-recording, and media inventory surfaces.
- Task 132: Add contract tests and migration docs for the HexeVoice Core-rendered UI pilot.
- Task 133: Add a Voice node local UI mode setting for `full`, `setup_only`, and future `disabled` operation.

Preserved task details:
- Task 125 inventories existing dashboard sections and cards under `frontend/src/features/dashboard/`, setup cards under `frontend/src/features/setup/`, and API sources under `src/hexevoice/main.py`, `src/hexevoice/runtime/service.py`, `src/hexevoice/endpoint/`, `src/hexevoice/assistant/`, and `src/hexevoice/tts/`. Completion requires a short mapping from current surfaces to Core card kinds, identifying which existing endpoints can be reused and which need lightweight `/api/node/ui/...` summaries.
- Task 126 adds `GET /api/node/ui-manifest` with `schema_version`, Voice node identity, `node_type=voice`, display name, page definitions, surface ids, card kinds, Core-routable `data_endpoint` values, refresh policies, detail endpoint templates, and manifest action metadata. The first pilot should include overview, runtime, endpoints, TTS, and intents pages while leaving `/nodes/:nodeId/UI` fallback behavior untouched in Core.
- Task 127 adds lightweight summary endpoints such as `/api/node/ui/overview/node`, `/api/node/ui/overview/health`, `/api/node/ui/overview/warnings`, and `/api/node/ui/overview/facts`. Responses should map existing onboarding, trust, governance, readiness, provider setup, and operational-status data into `node_overview`, `health_strip`, `warning_banner`, and `facts_card` shapes.
- Task 128 adds runtime/provider endpoints such as `/api/node/ui/runtime/services` and `/api/node/ui/providers/status`. Responses should summarize backend, external STT, TTS engine, wake runtime, Piper runtime, provider configuration, model state, restart support, resource usage, and last errors using `runtime_service` and `provider_status` shapes.
- Task 129 adds endpoint-focused data endpoints such as `/api/node/ui/voice/endpoints`, `/api/node/ui/voice/endpoint-actions`, and optional endpoint detail endpoints. The data should project endpoint connection, transport, firmware, mute, volume, session, replay, OTA, media, and storage state into shared `record_list`, `facts_card`, and `action_panel` responses without requiring Core to fetch every endpoint detail up front.
- Task 130 adds intent-focused Core-rendered surfaces: a `record_list` endpoint for registered intents, a detail endpoint for selected intent contracts, and action metadata/data for dry-run test and real invoke flows. Existing `/api/voice/intents`, `/api/voice/intents/{intent_id}`, `/api/voice/intents/dispatch`, and `/api/voice/intents/invoke` behavior should stay authoritative.
- Task 131 adds TTS and artifact surfaces for model inventory, warm/cold model status, conversion sample-rate settings, generated TTS artifacts, wake recordings, endpoint media inventory, and related safe actions. Use `resource_grid`, `artifact_browser`, `settings_form`, `provider_status`, and `record_list` shapes where Core already supports or plans those card kinds.
- Task 132 adds tests that validate the manifest and card payloads against the Core-rendered UI handoff contract available in `docs/Core-Documents/docs/nodes/ui-mogration/`. Tests should cover manifest shape, endpoint routing, no forbidden executable content, lightweight data payloads, safe action metadata, detail loading, and preservation of existing local dashboard/API behavior. Docs should explain how to enable the pilot from Core and what remains local-only.
- Task 133 adds a Voice node local UI mode configuration with initial default `full`. `setup_only` must preserve first boot, Core pairing, trust registration, recovery diagnostics, and handoff messaging while hiding normal operational dashboard surfaces only after Core-rendered parity is verified. `disabled` should remain documented as future-only until recovery coverage is strong enough.

Definition of done:
- Core can fetch a HexeVoice manifest through its rendered-node UI path and render useful Voice operational pages without node-hosted React cards.
- HexeVoice still serves its existing local dashboard for setup, recovery, diagnostics, and migration fallback.
- Overview, runtime, endpoint, TTS, and intent surfaces expose lightweight Core card payloads with explicit refresh policy.
- Operator actions remain backed by existing node-side authorization and validation.
- Tests prove new Core-rendered endpoints do not break current Voice dashboard, onboarding, provider setup, endpoint, intent, TTS, or service-control APIs.
