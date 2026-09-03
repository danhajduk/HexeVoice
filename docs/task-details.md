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

## Task 206
Original task details:
- Title: Upgrade frontend toolchain to a supported Node and Vite security baseline
- Source finding:
  - `npm audit` reports dev-tooling vulnerabilities through Vite/esbuild/PostCSS/Babel.
  - The available Vite fix is a major upgrade that requires Node `^20.19.0 || >=22.12.0`; current local validation was on Node `18.20.4`.
- Scope:
  - Decide and enforce the new minimum Node runtime for HexeVoice frontend development and install/bootstrap flows.
  - Upgrade Vite and matching React plugin/dev tooling to versions that clear the audit finding.
  - Update install/bootstrap checks, docs, and any system package guidance that currently assumes older Node.
  - Verify production build and dev-server behavior after upgrade.
- Acceptance criteria:
  - `npm run build` passes on the supported Node baseline.
  - `npm audit` has no unresolved actionable Vite/esbuild/PostCSS/Babel findings.
  - Installer and setup docs clearly state the Node version requirement.

## Task 207
Original task details:
- Title: Migrate FastAPI startup and shutdown hooks to lifespan handlers
- Source finding:
  - Full backend suite passes but emits FastAPI deprecation warnings for `@app.on_event("startup")` and `@app.on_event("shutdown")` in backend, STT, and TTS services.
- Scope:
  - Replace deprecated event hooks in `src/hexevoice/main.py`, `src/stt/service.py`, and `src/tts/service.py` with lifespan handlers.
  - Preserve existing startup warmup, cleanup, scheduler, and shutdown behavior.
  - Keep tests isolated so repeated TestClient startup/shutdown still behaves deterministically.
- Acceptance criteria:
  - Targeted service tests and full pytest suite pass.
  - FastAPI event-hook deprecation warnings are eliminated or reduced to third-party-only warnings.

## Task 208
Original task details:
- Title: Add a repo migration preflight command for backend, frontend, and audit checks
- Source finding:
  - Manual repo-health validation required multiple separate commands: Python dependency install, full pytest, frontend build, production audit, and full dev audit review.
- Scope:
  - Add a script or Make target that runs the canonical move-readiness checks from one command.
  - Include backend tests, frontend production build, production `npm audit --omit=dev`, and a clearly reported dev-audit section.
  - Report Python, Node, npm, and key tool versions.
  - Make failures actionable with concise summaries and log pointers.
- Acceptance criteria:
  - A fresh maintainer can run one documented command to validate the repo after moving/cloning.
  - The command exits nonzero on required backend/frontend/production-audit failures.
  - Dev-tooling audit findings are reported distinctly from production-blocking findings.

## Task 209
Original task details:
- Title: Consolidate legacy wake-model runtime paths and remove misspelled path dependency
- Source finding:
  - The repo intentionally carries a legacy `runtime/vioce_models/Hexa.tflite` compatibility path, and `.gitignore` still ignores both `runtime/voice_models/` and `runtime/vioce_models/`.
- Scope:
  - Audit current wake-model sync behavior and any deployed migration dependency on the misspelled path.
  - Add a one-time migration or compatibility warning that moves legacy assets into `runtime/openwakeword/models/`.
  - Remove the misspelled path dependency only after compatibility is preserved.
  - Update docs/tests to make the legacy behavior explicit until it is safe to remove.
- Acceptance criteria:
  - Existing legacy wake model installs still sync correctly.
  - New installs do not create or require `runtime/vioce_models/`.
  - Tests cover legacy-path migration and canonical wake-model path behavior.

## Task 210
Original task details:
- Title: Harden voice-loop validation across target firmware hardware profiles
- Source finding:
  - README now marks full production hardening across target hardware profiles as remaining work.
- Scope:
  - Define the supported firmware hardware profiles and the minimum validation matrix for audio streaming, wake acceptance, TTS playback, display state, OTA/media, mute/volume, and reconnect behavior.
  - Add automated or semi-automated checks where possible without requiring physical hardware for every test.
  - Document any manual field-validation steps that remain hardware-bound.
- Acceptance criteria:
  - Each target profile has a clear validation checklist and automated coverage where feasible.
  - Voice WebSocket, audio upload, STT/TTS response, and firmware playback regressions are caught before release.
  - Unsupported or unvalidated profile states are visible to operators.

## Task 211
Original task details:
- Title: Add long-running provider health, model warmup, and firmware media lifecycle validation
- Source finding:
  - Repo-health review identified long-running provider health, model warmup, and firmware media/artifact lifecycle as remaining production-hardening work.
- Scope:
  - Add soak or lifecycle tests for STT/TTS/wake provider health over restart, warmup, reload, and degraded states.
  - Validate generated TTS artifact cleanup, wake recording cleanup, endpoint media delivery, firmware artifact install, and OTA/media inventory over repeated cycles.
  - Expose concise pass/fail summaries suitable for setup ready checks or operations docs.
- Acceptance criteria:
  - Long-running validation can be run locally or on a staging node without manual log spelunking.
  - Provider/model/media lifecycle failures produce actionable diagnostics.
  - Existing fast test suite remains practical for day-to-day development.

## Task 212
Original task details:
- Title: Reconcile stale Voice Node docs and decide the node feature report disposition
- Source finding:
  - `docs/node-feature-report.md` remains untracked after the repo-health review.
  - `docs/architecture.md` still states STT, TTS, assistant routing, and persistent session history are pending even though source/tests show those paths now exist.
  - `docs/firmware-baseline.md` still lists real TTS playback as missing, while current firmware/tests indicate playback exists but remains profile/device-hardening work.
- Scope:
  - Decide whether `docs/node-feature-report.md` should be committed as an audit artifact, converted into canonical docs, or removed.
  - Update stale architecture and firmware baseline language so implemented, partial, scaffold, and missing states match current source/tests.
  - Cross-link the current roadmap, firmware validation matrix, provider lifecycle validation, and feature report if retained.
- Acceptance criteria:
  - No untracked node feature report remains without an explicit decision.
  - Docs no longer claim implemented backend voice/STT/TTS/session-history surfaces are pending.
  - Firmware docs distinguish implemented TTS playback from remaining physical validation and integrity hardening.

## Task 213
Original task details:
- Title: Add firmware provisioning and endpoint settings UI flow
- Source finding:
  - Firmware Wi-Fi/backend connection still relies on compile-time or local YAML/secrets configuration.
  - Firmware-side settings UI is not implemented.
- Scope:
  - Define the provisioning contract for Wi-Fi, backend host/ports, endpoint id/display name, audio defaults, and recovery/reset behavior.
  - Add a firmware-accessible setup/settings surface appropriate to each supported profile, with a fallback path for headless devices.
  - Persist settings safely and keep existing build-time config as a development fallback.
  - Update setup/operator docs and tests for provisioning state transitions and recovery.
- Acceptance criteria:
  - A fresh endpoint can be configured without editing firmware source or local secrets headers for normal operation.
  - Settings survive reboot and can be reset or re-entered safely.
  - Headless and display-capable profiles have documented setup paths.

## Task 214
Original task details:
- Title: Add automatic endpoint discovery and pairing for firmware endpoints
- Source finding:
  - Automatic endpoint discovery is deferred; endpoint YAML currently points firmware at a fixed backend host/port.
- Scope:
  - Choose a discovery mechanism compatible with the Hexe/Core node standards, such as mDNS, LAN broadcast, Core-mediated enrollment, or a documented hybrid.
  - Implement endpoint discovery/pairing so firmware can locate the HexeVoice node and register a stable endpoint identity.
  - Keep static YAML/manual host configuration as an explicit fallback.
  - Add backend and firmware tests or simulations for discovery success, timeout, duplicate endpoint identity, and stale pairing recovery.
- Acceptance criteria:
  - New firmware endpoints can find and pair with the node without hardcoded backend IPs in the common path.
  - Operators can see discovery/pairing state and actionable failures.
  - Static config remains available for constrained or locked-down networks.

## Task 215
Original task details:
- Title: Harden firmware OTA integrity with checksum enforcement and signed manifest validation
- Source finding:
  - Firmware artifact serving and OTA push exist, but firmware-side SHA-256 enforcement and signed manifest validation remain missing.
- Scope:
  - Define the OTA manifest trust model, signature format, key distribution/rotation expectations, and downgrade/replay policy.
  - Enforce SHA-256 verification on downloaded firmware before applying OTA.
  - Validate signed manifests or signed artifact metadata before accepting backend-pushed updates.
  - Add tests for checksum mismatch, missing signature, invalid signature, unsupported profile artifact, downgrade/replay, and happy path.
- Acceptance criteria:
  - Firmware rejects corrupted, unsigned, invalidly signed, wrong-profile, or replayed OTA artifacts.
  - Backend/operator diagnostics name the exact OTA integrity failure.
  - Valid signed artifacts still update through the existing OTA push flow.

## Task 216
Original task details:
- Title: Validate physical-device reconnect and session-boundary behavior across supported profiles
- Source finding:
  - Device-tested reconnect/session-boundary behavior remains listed as hardening work even though automated WebSocket/session coverage exists.
- Scope:
  - Turn the firmware validation matrix manual reconnect/session checks into a repeatable field-validation procedure or semi-automated rig.
  - Validate backend restart, endpoint power-cycle, Wi-Fi loss/rejoin, active-session disconnect, post-TTS cooldown, wake retry, and duplicate-session prevention for `esp_box_3` and `ha_voice_pe`.
  - Capture results in a structured artifact suitable for release review.
- Acceptance criteria:
  - Each supported profile has recorded pass/fail reconnect and session-boundary results.
  - Failures produce actionable firmware/backend follow-up tasks.
  - Release docs clearly show which profile states are validated, partial, or blocked.

## Task 217
Original task details:
- Title: Replace or explicitly retire scaffold firmware modules for wake word, STT stream, assistant client, telemetry, and power
- Source finding:
  - `firmware/main/voice/wake_word.cpp`, `firmware/main/voice/stt_stream.cpp`, `firmware/main/voice/assistant_client.cpp`, `firmware/main/system/telemetry.cpp`, and `firmware/main/system/power.cpp` remain scaffold-style modules.
- Scope:
  - Audit whether each scaffold is still needed, should move behind an explicit no-op interface, should be implemented, or should be removed.
  - For retained no-op modules, expose clear capability/status semantics so operators are not misled.
  - For implemented modules, add source-level tests or firmware build checks that pin behavior.
  - Update firmware baseline docs to reflect the decision for each module.
- Acceptance criteria:
  - No scaffold module is left ambiguous: each is implemented, removed, or documented as an intentional no-op with capability reporting.
  - Firmware source/tests/docs agree on the state of wake word, STT stream, assistant client, telemetry, and power ownership.

## Task 218
Original task details:
- Title: Run physical reconnect/session-boundary bench validation and replace blocked release artifact results
- Source finding:
  - Task 216 added the repeatable field-validation rig and seed release artifact, but no physical `esp_box_3` or `ha_voice_pe` endpoints were attached during the repo-side run.
- Scope:
  - Run `scripts/firmware-reconnect-session-validation.py` against physical `esp_box_3` and `ha_voice_pe` devices connected to a live backend.
  - Exercise backend restart, endpoint power-cycle, Wi-Fi loss/rejoin, active-session disconnect, post-TTS cooldown, wake retry, and duplicate-session prevention for both profiles.
  - Replace `docs/firmware-reconnect-session-results.json` with operator-recorded `pass` or `fail` results and link follow-up tasks for every failure.
- Acceptance criteria:
  - `docs/firmware-reconnect-session-results.json` has no `blocked` scenarios for supported profiles.
  - Every scenario has operator-recorded observations and pass/fail status for both supported profiles.
  - Any failure has a linked firmware or backend remediation task before release approval.

## Task 219
Original task details:
- Title: Play endpoint timer-expired alarm audio and report acknowledgement state
- Source finding:
  - Timer creation/status requests can be routed through HexeVoice, but timer expiry playback on the endpoint is not implemented yet.
  - The user clarified that when a timer reaches zero, the endpoint should sound a timer-out alarm, and the spoken word `stop` should stop that alarm.
- Scope:
  - Define the timer-expired event contract from the timer-owning node to the endpoint that should sound.
  - Add endpoint playback support for a timer-expired sound that is not coupled to TTS session playback.
  - Track alarm lifecycle state such as queued, playing, stopped, completed, failed, and acknowledged.
  - Publish acknowledgement/dismissal events so the timer-owning node can clear or update timer state.
  - Update backend, firmware, and operator docs for timer-expired playback behavior.
- Acceptance criteria:
  - A timer expiry causes the intended endpoint to play the configured timer alarm sound.
  - The backend can see whether the alarm is queued, playing, stopped, completed, failed, or acknowledged.
  - Timer alarm playback works independently of normal TTS response playback.

## Task 220
Original task details:
- Title: Add local voice stop interruption for active endpoint playback
- Source finding:
  - Firmware currently pauses/disables the microphone during playback, so the endpoint cannot hear `stop` while TTS or future timer alarm audio is playing.
  - Existing playback cancellation is TTS-oriented, but timer alarms and future sounds need the same interruption capability.
- Scope:
  - Add a source-agnostic playback interruption path for TTS, timer alarms, notification sounds, and future audio.
  - Keep a lightweight local listener available during interruptible playback for the word `stop`, or document and implement the safest supported board-specific equivalent.
  - Stop playback locally first for low latency, then report the interruption to the backend.
  - Replace or wrap narrow `stop_tts_playback` semantics with generic playback stop semantics where appropriate.
  - Add firmware/backend tests or source checks for interruptible and non-interruptible playback states.
- Acceptance criteria:
  - Saying `stop` while a timer alarm is playing stops the endpoint audio without waiting for cloud/backend STT.
  - The backend receives a source-agnostic playback stop/interrupted event with reason `voice_stop`.
  - Existing TTS playback cancellation still works.

## Task 221
Original task details:
- Title: Add timer stop and timer cancel voice intents with cross-node routing
- Source finding:
  - Timer create/status intent support exists, but explicit stop/cancel semantics are not available as first-class intents.
  - Timer ownership may live on a different node than the voice endpoint, so intents must route to the timer owner rather than assuming local timer state.
- Scope:
  - Add `timer.stop` for stopping/dismissing an actively ringing or elapsed timer alarm.
  - Add `timer.cancel` for cancelling an active timer before it reaches zero.
  - Define event payloads and MQTT topics for stop/cancel requested, succeeded, failed, and ambiguous cases.
  - Ensure stop/cancel requests include endpoint id, node id, timer id when known, and correlation/session metadata.
  - Add spoken replies for success, failure, no active timer, and multiple timer ambiguity.
- Acceptance criteria:
  - Phrases such as `stop the timer`, `cancel the timer`, and `dismiss the timer` map to the correct intent based on timer/alarm state.
  - The timer-owning node receives the request and HexeVoice announces or displays the result.
  - Ambiguous multi-timer cases do not cancel the wrong timer silently.

## Task 222
Original task details:
- Title: Add timer adjust-time voice intent for adding or removing timer duration
- Source finding:
  - The user requested an adjust-time intent for phrases like `add 5 minutes` and `remove 2 minutes`.
  - The timer may live on a different node, so adjustment must be expressed as a routed timer command.
- Scope:
  - Add a timer adjustment intent, named consistently with the timer domain, for adding or subtracting duration from an existing timer.
  - Parse duration amounts and adjustment direction from natural language.
  - Define event payloads for adjustment requested, succeeded, failed, and ambiguous timer selection.
  - Include timer id, target node, endpoint id, delta seconds, and correlation/session metadata.
  - Add tests for add/remove phrasing, invalid durations, no active timer, and multiple timer ambiguity.
- Acceptance criteria:
  - Phrases such as `add five minutes to the timer` and `remove two minutes from the timer` publish the expected adjustment request.
  - The timer-owning node can respond with the updated remaining time.
  - HexeVoice announces the adjusted timer result or a clear failure/ambiguity message.

## Task 223
Original task details:
- Title: Add multi-node timer ownership, status, and disambiguation handling
- Source finding:
  - Timer state can live on a different node than the voice endpoint.
  - Timer commands need consistent ownership discovery, correlation, and user-facing disambiguation when multiple timers exist.
- Scope:
  - Maintain a backend-side cache of timer ownership and recent timer events by node, endpoint, timer id, label, and due time.
  - Reconcile timer create/status/stop/cancel/adjust responses from other nodes into a consistent local view.
  - Add disambiguation behavior for multiple active timers, such as nearest timer, named timer, room/device-scoped timer, or a follow-up prompt.
  - Expose concise status in the UI or diagnostics so operators can see which node owns each timer.
  - Document recommended event contracts for timer-owning nodes.
- Acceptance criteria:
  - HexeVoice can answer or route timer commands even when the timer is owned by another node.
  - Multiple active timers produce deterministic selection or a clear clarification path.
  - Operator diagnostics show timer owner, timer id, endpoint target, remaining time, and alarm/playback state.

## Task 224
Original task details:
- Title: Consume promoted timer.completed events and sound the target endpoint alarm
- Source finding:
  - Timer completion should be event-based.
  - When Core promotes a `timer.completed` event, HexeVoice should sound the timer completed sound on the endpoint that started or owns the timer interaction.
- Event contract:
  - Listen for promoted timer completion events with:
    - `event_type`: `timer.completed`
    - `promoted_event_type`: `timer.completed`
    - `routing.domain_topic`: `hexe/events/timer/completed`
    - `source.topic`: `hexe/nodes/<timer-node-id>/events/timer/completed`
    - `subject.family`: `timer`
    - `subject.record_id`: timer id
    - `data.timer_id`: timer id
    - `data.endpoint_id`: endpoint to sound
    - `data.device_id`: device id, usually the same as endpoint id
    - `data.title`: timer label/title
    - `data.duration_seconds`, `data.started_at`, `data.due_at`, and `data.completed_at`
    - `source.node_id` or `data.requester_node_id`: timer-owning/requesting node
- Sample event:
  - `schema_version`: `1`
  - `promotion_id`: `30833afd-aa6a-4208-ad51-97bf5e0ad55e`
  - `event_id`: `interaction-timer-completed-timer_codex_20_sec_timer_1787524538`
  - `event_type`: `timer.completed`
  - `promoted_event_type`: `timer.completed`
  - `source.node_id`: `node-6812313e6d1efad6`
  - `source.component`: `hexe.timer`
  - `routing.domain_topic`: `hexe/events/timer/completed`
  - `data.endpoint_id`: `esp-pe-1`
  - `data.device_id`: `esp-pe-1`
  - `data.timer_id`: `timer_codex_20_sec_timer_1787524538`
  - `data.title`: `20 seconds`
  - `data.duration_seconds`: `20`
- Scope:
  - Subscribe to the Core-promoted `hexe/events/timer/completed` topic or equivalent backend event stream.
  - Validate the promoted event shape and ignore non-timer or malformed events with operator-visible diagnostics.
  - Resolve the target endpoint from `data.endpoint_id`, falling back to `data.device_id` only when safe.
  - Push a timer alarm playback command to the target endpoint.
  - Include timer id, title, source node id, due/completed timestamps, and correlation ids in playback state and acknowledgement events.
  - Deduplicate repeated promoted events using `routing.dedupe_key`, `event_id`, or a stable timer completion key.
  - Publish/report failures when the target endpoint is offline, unknown, muted, busy, or rejects playback.
- Acceptance criteria:
  - A promoted `timer.completed` event for `esp-pe-1` causes only `esp-pe-1` to play the timer completed sound.
  - Duplicate promoted timer completion events do not cause duplicate alarm playback.
  - Unknown or offline endpoints produce a clear diagnostic and do not crash the timer subscriber.
  - The timer alarm lifecycle can later be stopped by the generic playback stop path from Task 220.

## Task 225
Original task details:
- Title: Add true on-device stop-word detection during endpoint playback
- Source finding:
  - Task 220 added source-agnostic playback stop plumbing, but the current firmware does not include an on-device keyword recognizer.
  - `firmware/main/voice/wake_word.cpp` explicitly reports backend-owned wake detection and `wake_word_on_device_available() == false`.
  - Both board profiles pause or disable microphone capture during playback to avoid self-triggering on speaker output.
- Scope:
  - Select a firmware-compatible local keyword detection strategy for the single word `stop`, including memory, CPU, license, and model delivery constraints.
  - Keep detection active only during interruptible playback, with echo/false-positive controls appropriate for timer alarm audio.
  - Call the generic `stop_playback("voice_stop")` path locally when the stop word is detected.
  - Report a source-agnostic `playback.stop` event to the backend with reason `voice_stop`.
  - Document board-specific support for `esp_box_3` and `ha_voice_pe`.
- Acceptance criteria:
  - Saying `stop` while an endpoint timer alarm is playing stops playback locally without waiting for backend STT.
  - The feature does not re-enable general wake/STT capture during playback unless explicitly configured.
  - Unsupported profiles report a clear capability/diagnostic instead of pretending local stop-word detection is available.
- Current blocker:
  - Firmware does not include an embedded keyword recognizer such as ESP-SR/MultiNet or a trained local command model for `stop`.
  - Task 225 currently exposes heartbeat diagnostics for this unsupported state and preserves the `playback.stop`/`voice_stop` contract; true local detection requires adding and validating the keyword engine dependency first.

## Task 226
Original task details:
- Title: Interrupt active WAV playback immediately when `stop_playback()` is requested
- Source finding:
  - Backend `playback.stop` with reason `voice_stop` now reaches the endpoint and stops the timer alarm.
  - Current firmware behavior waits until the active WAV loop pass finishes before playback fully stops.
  - This is acceptable for the current implementation, but it should be improved later so stop feels instant.
- Scope:
  - Update `stop_playback()` and the board-specific WAV playback loops to abort the current audio write promptly.
  - Preserve the existing source-agnostic `playback.stop` event and acknowledgement contract.
  - Ensure looped timer alarms, TTS playback, and SD/file playback all settle into the correct stopped lifecycle state.
  - Validate behavior on both `esp_box_3` and `ha_voice_pe` speaker paths.
- Acceptance criteria:
  - Saying or pressing `stop` during a looped timer alarm stops playback without waiting for the current WAV loop to finish.
  - Firmware sends exactly one `playback.stop` event with the stop reason.
  - Playback lifecycle reports `stopped`, microphone state is restored correctly, and no partial write leaves the audio driver wedged.

## Task 227
Original task details:
- Title: Add UDP node beacon advertising HexeVoice API and UI LAN IP endpoints
- Source request:
  - Add option #3 from the local discovery discussion: keep existing request/offer discovery, add UDP beacons, and add mDNS.
  - The advertised host must be the node's LAN IP address, not `hexe.local`.
- Scope:
  - Add a configurable UDP broadcast beacon from the node on the local network.
  - Include schema/version, node identity, node type, API URL, UI URL, API port, UI port, TLS flag, heartbeat path, voice WebSocket path, and advertised LAN IP.
  - Reuse or align with the existing endpoint discovery schema where practical without breaking firmware request/offer discovery.
  - Resolve the advertised address from explicit config first, then from a safe LAN interface probe; do not emit loopback, `0.0.0.0`, or `hexe.local` as the advertised host.
  - Add operator-visible diagnostics for beacon enabled/disabled state, selected IP, port, interval, and last send error.
- Acceptance criteria:
  - A listener on the LAN can discover the node API as `http://<lan-ip>:9004` and UI as `http://<lan-ip>:8084`.
  - Beacon payload never advertises `hexe.local` when LAN IP mode is selected.
  - Existing endpoint UDP discovery request/offer continues to work.
  - Tests cover payload construction, LAN IP selection fallback, disabled mode, and invalid/loopback host rejection.

## Task 228
Original task details:
- Title: Add mDNS service advertisement for HexeVoice API and UI using LAN IP endpoint metadata
- Source request:
  - Add option #3 from the local discovery discussion: support both UDP beaconing and mDNS service advertisement.
  - Keep the advertised endpoint metadata as IP-based rather than `hexe.local`.
- Scope:
  - Advertise HexeVoice services over mDNS, for example `_hexevoice._tcp.local`, with API and UI ports discoverable by operator tools.
  - Include TXT metadata for API URL, UI URL, API port, UI port, node id, node type, TLS flag, and advertised LAN IP.
  - Ensure TXT endpoint URLs use `http://<lan-ip>:9004` and `http://<lan-ip>:8084` unless TLS/ports are explicitly configured otherwise.
  - Decide whether to use a Python mDNS library, Avahi integration, or optional platform adapter; document dependencies and fallback behavior.
  - Keep mDNS optional so locked-down hosts can disable it independently from UDP discovery and UDP beaconing.
- Acceptance criteria:
  - LAN clients can discover the HexeVoice API and UI through mDNS service browsing.
  - Service metadata contains IP-based URLs, not `hexe.local` URLs.
  - Failure to start mDNS does not prevent the backend API from starting; it surfaces a diagnostic.
  - Tests cover service metadata generation and disabled/error states; manual validation documents the expected `avahi-browse` or equivalent output.

## Task 229
Original task details:
- Title: Redesign voice endpoint dashboard for multiple endpoints and production-built UI
- Source request:
  - Redesign `http://10.0.0.100:8084/#/dashboard/voice-endpoint` to support multiple endpoint devices.
  - The UI should be built and served as the production build, not run through the Vite dev server.
- Scope:
  - Update the voice endpoint dashboard so it treats multiple endpoints as a first-class workflow.
  - Show all known endpoints with online/offline state, display name/endpoint id, firmware version/update state, board profile, active session/playback state, volume/mute state, and last heartbeat metadata.
  - Let the operator select an endpoint and view endpoint-specific detail without hiding the rest of the fleet.
  - Ensure endpoint actions target the selected endpoint only, including volume, mute, replay, cancel/stop, OTA, media, and diagnostics where supported.
  - Preserve useful single-endpoint behavior while avoiding UI assumptions that only one voice WebSocket endpoint exists.
  - Build the frontend production bundle and serve/validate the production UI on port `8084`; do not rely on `npm run dev` or Vite dev-only behavior.
- Acceptance criteria:
  - The dashboard route renders more than one endpoint at the same time when multiple endpoints are registered.
  - Selecting one endpoint updates detail panels and actions for that endpoint without losing fleet visibility.
  - Offline endpoints remain visible with stale/update diagnostics and safe disabled actions.
  - Production build succeeds, and the served UI at `http://10.0.0.100:8084/#/dashboard/voice-endpoint` reflects the built assets.
  - Tests or targeted UI validation cover multi-endpoint rendering, endpoint selection, and action scoping.

## Task 230
Original task details:
- Title: Add local playback and endpoint control voice intents
- Source request:
  - User asked to add tasks for suggested local intents and implement them.
  - Existing timer intents already cover `timer.status`, `timer.stop`, `timer.cancel`, and `timer.adjust_time`; do not duplicate them.
- Scope:
  - Add built-in local voice intents for `playback.stop`, `playback.repeat`, `endpoint.volume.set`, `endpoint.volume.adjust`, `endpoint.mute`, `endpoint.unmute`, and `endpoint.identify`.
  - Parse natural language examples such as `stop playback`, `repeat that`, `set volume to 60 percent`, `turn it up`, `lower volume by 10`, `mute yourself`, `unmute`, and `identify this device`.
  - Dispatch matched intents to the selected/current endpoint command path where the backend already supports it.
  - Keep dangerous short utterances follow-up scoped except for the existing interrupt-mode stop behavior.
- Acceptance criteria:
  - Intent registry seeds the new built-ins for fresh and existing registry files.
  - Local intent matching extracts volume slots and endpoint control commands.
  - Voice turn invocation returns a dispatch result for endpoint command handling.
  - Tests cover matching and dispatch for the new controls.

## Task 231
Original task details:
- Title: Add timer snooze voice intent for completed timer alarms
- Source request:
  - User asked for suggested local intents to be added and implemented.
- Scope:
  - Add a `timer.snooze` local intent for phrases such as `snooze five minutes` and `snooze the timer for ten minutes`.
  - Dispatch the snooze as a timer adjustment/create-style MQTT domain event that can be handled by the timer-owning node.
  - Reuse active timer ownership selection when possible.
- Acceptance criteria:
  - Intent registry seeds `timer.snooze`.
  - Matching extracts snooze duration and scope.
  - Dispatch publishes a routed timer snooze request event.
  - Tests cover intent matching and event payload.

## Task 232
Original task details:
- Title: Define the HexeVoice Speaker ID service contract, Core capability names, privacy policy, and event schemas
- Source request:
  - Add Speaker ID as part of the Voice node provider stack, like STT, TTS, and wake services.
  - Candidate engines/models include SpeechBrain ECAPA-TDNN, WeSpeaker, pyannote.audio, and NVIDIA NeMo speaker models.
- Scope:
  - Define Voice-declared Core capabilities such as `voice.speaker.identify`, `voice.speaker.verify`, `voice.speaker.enroll`, and `voice.speaker.profile.manage`.
  - Define the HexeVoice local API request/response contracts for enrollment, verification, closed-set identification, open-set unknown-speaker handling, profile deletion, and health/status.
  - Define MQTT/domain events for speaker enrollment started/completed/failed, speaker identified, speaker verification completed, profile deleted, and low-confidence/unknown outcomes.
  - Specify biometric privacy rules: explicit enrollment consent, local-only profile storage by default, no raw audio retention unless enabled, redacted event payloads, profile export/delete controls, and audit logging.
  - Define Speaker ID as a HexeVoice-managed service/helper, not a separate Core node or Core-resolved external service.
- Acceptance criteria:
  - Docs include exact JSON request/response examples and event schemas.
  - The capability names align with Core capability declaration conventions for Voice.
  - Privacy and deletion requirements are explicit enough to implement without guessing.
  - The contract supports at least enrolled-speaker identification and one-to-one verification.

## Task 233
Original task details:
- Title: Build the HexeVoice Speaker ID runtime adapter layer with benchmark support for SpeechBrain ECAPA-TDNN, WeSpeaker, pyannote.audio, and NVIDIA NeMo
- Source request:
  - Evaluate Speaker ID engines/models including SpeechBrain ECAPA-TDNN, WeSpeaker, pyannote.audio, and NVIDIA NeMo speaker models.
- Scope:
  - Create a HexeVoice-owned provider adapter boundary that normalizes audio input, embedding extraction, similarity scoring, thresholds, model metadata, and hardware requirements.
  - Add adapters or benchmark stubs for SpeechBrain ECAPA-TDNN, WeSpeaker, pyannote.audio speaker embedding/diarization paths, and NVIDIA NeMo speaker models.
  - Support CPU-first operation and optional CUDA acceleration where available.
  - Track model license, download size, memory usage, latency, embedding dimensions, sample-rate requirements, and enrollment quality constraints.
  - Add a benchmark command that runs the same local sample set against enabled providers and emits a JSON comparison artifact.
  - Follow the existing Voice local service patterns used by STT/TTS, including import-safe optional dependencies and local runtime diagnostics.
  - Keep the adapter import-safe and callable both in-process and through the Speaker ID helper service that is reached over the local Unix socket.
- Acceptance criteria:
  - A single adapter API can extract embeddings and score two utterances across provider implementations.
  - Benchmark output compares latency, memory, confidence/separation, and model metadata.
  - Unsupported/missing providers fail with clear diagnostics rather than import-time crashes.
  - Tests cover adapter normalization, threshold handling, and disabled provider behavior.

## Task 234
Original task details:
- Title: Implement the HexeVoice Speaker ID Unix-socket service APIs for enrollment, verification, identification, profile management, and health reporting
- Source request:
  - Add Speaker ID as a Voice-owned managed service like STT/TTS/wake.
- Scope:
  - Create the HexeVoice service runtime with FastAPI endpoints for provider status, enrollment, identify, verify, profile list/detail/delete, and model/provider settings.
  - Run the helper as HTTP/JSON over a Unix domain socket by default, using `runtime/sockets/speaker-id.sock` or `VOICE_SPEAKER_ID_SOCKET_PATH`.
  - Keep `VOICE_SPEAKER_ID_BASE_URL` as an explicit debug/fallback setting only; do not open a Speaker ID TCP listener by default.
  - Add a HexeVoice backend client that uses `httpx` Unix-socket transport for local Speaker ID helper calls.
  - Create the socket directory with owner-only permissions and clean up stale socket files on helper startup.
  - Persist speaker profiles as local embeddings plus metadata, not raw audio by default.
  - Support multi-sample enrollment with quality checks and profile versioning.
  - Return `unknown` when confidence or score margin is below configured thresholds.
  - Register the local Speaker ID service with Core Supervisor when it runs as an external helper process/container.
  - Add Speaker ID capability declaration metadata, provider intelligence, model metadata, and service endpoints to the Voice node declaration.
  - Add supervisor/systemd or runtime scripts matching the existing HexeVoice STT/TTS service patterns.
- Acceptance criteria:
  - The service can enroll a named speaker from one or more WAV samples and identify that speaker from a later sample.
  - Profile deletion removes embeddings and metadata from local storage.
  - HexeVoice status endpoints report selected provider, loaded model, thresholds, profile count, and last error.
  - HexeVoice can call helper health, identify, verify, enroll, and profile APIs over the configured Unix socket without any Speaker ID TCP port.
  - Status and health responses report the active transport mode and socket path.
  - Core sees Speaker ID capabilities on the Voice node capability declaration; no separate Speaker ID node registration is required.

## Task 235
Original task details:
- Title: Integrate the HexeVoice Speaker ID service into per-turn speaker lookup, speaker-aware events, and safe fallback behavior
- Source request:
  - Speaker ID should be usable by the voice system as a local Voice provider service.
- Scope:
  - Add a HexeVoice Speaker ID client/service facade that calls the configured local adapter/helper service through the Unix socket by default.
  - Fan completed VAD/utterance audio out to STT and Speaker ID in parallel.
  - Add an interaction-router join step that combines transcribed text and speaker identity only when the matched intent, assistant route, or tool/action policy requires it.
  - Add `speaker_identity_policy` handling with at least `not_required`, `use_if_ready`, `required`, and `forbidden`.
  - Execute intents/routes that do not require speaker identity without waiting for Speaker ID.
  - Wait for Speaker ID only for identity-gated routes such as personal calendar access; if the speaker is unknown or low confidence, ask who is speaking instead of executing the personal route.
  - Attach speaker result metadata to voice session history, assistant turn context, and reusable voice events without exposing raw biometric embeddings.
  - Keep voice turns functional when Speaker ID is unavailable, slow, unauthorized, or returns unknown/low-confidence.
  - Add configuration for enable/disable, timeout, confidence threshold, per-endpoint scope, and whether speaker identity can affect personalization.
- Acceptance criteria:
  - A voice turn can include `speaker_id`, display name, confidence, model/provider, and unknown/low-confidence reason when available.
  - STT and Speaker ID are launched from the same completed utterance audio without serializing STT behind Speaker ID.
  - `voice.time.query` and other non-personal/local intents execute without waiting for Speaker ID.
  - Identity-gated requests such as personal calendar access wait for Speaker ID and ask a follow-up when the speaker is unknown.
  - Speaker ID provider/helper failure does not block STT, local intents, timers, or assistant routing.
  - Speaker metadata is redacted from public logs/events unless explicitly allowed.
  - Tests cover parallel STT/Speaker ID execution, not-required policy, use-if-ready policy, required-known speaker, required-unknown speaker, service unavailable, timeout, and disabled/unauthorized local policy.

## Task 236
Original task details:
- Title: Add Speaker ID operator UI controls for consent, enrollment, profile review, deletion, diagnostics, and model/provider selection
- Source request:
  - Speaker ID should be operable as a HexeVoice local service.
- Scope:
  - Add dashboard/setup controls for enabling Speaker ID, selecting provider/model, tuning thresholds, and viewing service health.
  - Add an enrollment workflow that records or uploads sample utterances, shows quality checks, asks for explicit consent, and creates a profile.
  - Show profile list/detail with display name, profile id, sample count, provider/model, created/updated timestamps, and delete/export controls.
  - Surface recent identification outcomes with confidence, unknown/low-confidence reason, endpoint id, and privacy-safe metadata.
  - Add recovery actions for reload model, rebuild profile embeddings, re-run benchmark, and clear failed enrollment state.
- Acceptance criteria:
  - Operators can enroll, inspect, and delete a speaker profile without editing files.
  - UI clearly distinguishes verified/identified/unknown speakers and low-confidence outcomes.
  - Deleting a profile removes it from future identification candidates.
  - Production frontend build succeeds and reflects the new UI.

## Task 237
Original task details:
- Title: Validate Speaker ID accuracy, latency, privacy, and multi-endpoint behavior with tests, docs, and benchmark artifacts
- Source request:
  - Speaker ID should be reliable enough for local voice personalization and should not weaken privacy.
- Scope:
  - Add unit, API, and integration tests for enrollment, identify, verify, profile deletion, Voice capability declaration, Voice integration, and event schemas.
  - Add benchmark fixtures and scripts that can be run with local sample audio, with raw audio excluded from git by default.
  - Measure latency and memory on CPU and optional CUDA paths for the candidate providers.
  - Validate multi-endpoint behavior so speaker identity follows the speaker/audio, not the endpoint id.
  - Document setup, model downloads, privacy controls, troubleshooting, and recommended default provider.
- Acceptance criteria:
  - Test artifacts demonstrate correct identification of enrolled speakers, rejection of unknown speakers, and threshold behavior.
  - Benchmark docs compare SpeechBrain ECAPA-TDNN, WeSpeaker, pyannote.audio, and NVIDIA NeMo options when installed.
  - Privacy tests verify delete/export/redaction behavior.
  - Operator docs explain how to enable Speaker ID and how to disable/remove all biometric data.

## Task 238
Original task details:
- Title: Implement Track 1 audio-quality foundation for accepted voice turns
- Source request:
  - Add Track 1 from `docs/temp-roadmap-capability-report.md` as an implementation backlog task.
- Scope:
  - Add a backend audio-quality analyzer for in-memory PCM turn audio.
  - Define a structured audio-quality result with duration, RMS, peak, clipping count/ratio, silence or active-audio ratio, speech-level estimate, optional ambient/SNR placeholders, quality status, and warnings.
  - Integrate the analyzer into the voice turn pipeline without blocking intent execution.
  - Attach redacted quality metadata to voice session history, transcript/session diagnostics, and relevant voice events.
  - Discard raw turn audio immediately after STT, Speaker ID, and audio-quality processing unless explicit debug recording is enabled.
  - When debug recording is enabled, retain raw debug audio for one day only.
  - Add focused tests with synthetic PCM for silence, normal-level audio, clipped audio, very short audio, and low-level audio.
  - Document that first-pass quality analysis is diagnostic only and does not require YAMNet, pyannote, endpoint election, passive calibration, or firmware changes.
- Acceptance criteria:
  - Accepted voice turns produce privacy-safe audio-quality metadata from in-memory PCM.
  - Existing intent execution remains non-blocking when audio-quality analysis reports warnings.
  - Raw voice-turn audio is not retained after processing unless explicit one-day debug retention is enabled.
  - Synthetic PCM tests cover silence, normal-level audio, clipped audio, very short audio, and low-level audio.
  - Documentation reflects that Track 1 is diagnostic-only and excludes heavier environment classifiers, endpoint election, passive calibration, and firmware changes.


## Task 239
Original task details:
- Title: Define versioned voice quality, identity, enrollment, and placement metric schemas
- Source request:
  - Add roadmap improvement for stable shared metric/schema shapes before multiple later tasks invent incompatible fields.
- Scope:
  - Define versioned internal schema shapes for:
    - audio-quality result
    - ambient/SNR result
    - Speaker ID result and confidence tier
    - identity classification/policy decision
    - enrollment readiness result
    - validation phrase scoring result
    - placement test/report metrics
    - voice quality observation log records
  - Include schema version fields in persisted/runtime payloads where the data may be stored, exported, or logged.
  - Keep schemas local/internal unless a Core-facing contract is explicitly needed.
  - Document field names, redaction/privacy expectations, and compatibility policy.
  - Add tests or validation helpers for required fields and redaction-sensitive fields.
- Acceptance criteria:
  - Later Track 1-6 tasks have a documented schema source to follow.
  - Persisted/logged diagnostic records include schema version metadata.
  - Privacy-sensitive fields such as raw audio, embeddings, passcodes, and biometric templates are excluded from schemas intended for logs/status.


## Task 240
Original task details:
- Title: Implement Track 2 pre-roll ambient reference and per-turn SNR metadata
- Source request:
  - Add Track 2 from `docs/temp-roadmap-capability-report.md` as an implementation backlog task after a `[STOP]` gate.
- Scope:
  - Extend accepted voice-turn handling so pre-roll or pre-speech audio can be treated as an ambient reference separate from the spoken utterance.
  - Preserve ambient data in memory by default and avoid raw ambient persistence unless existing debug recording settings explicitly allow it.
  - When debug recording allows raw ambient/pre-roll retention, expire it after one day.
  - Compute ambient RMS, ambient peak, ambient duration, speech RMS, speech peak, speech duration, and estimated SNR in dB when enough data is available.
  - Attach ambient/SNR metadata to the Track 1 audio-quality result and voice session diagnostics.
  - Represent missing or insufficient pre-roll as an explicit unknown state rather than inventing an SNR value.
  - Keep first-pass Track 2 diagnostic-only; do not block intent execution on low SNR yet.
  - Add tests for available ambient pre-roll, missing ambient pre-roll, short ambient pre-roll, low-SNR audio, and normal-SNR audio.
  - Document privacy behavior and the distinction between ambient metrics and raw ambient audio.
- Acceptance criteria:
  - Accepted voice turns can report ambient reference metrics and SNR when pre-roll is available.
  - Voice turns without usable pre-roll report SNR as unavailable with a clear reason.
  - Raw ambient audio is not retained by default.
  - Any debug-retained ambient/pre-roll audio expires after one day.
  - Existing Track 1 quality analysis continues to work without firmware changes.


## Task 241
Original task details:
- Title: Add endpoint ambient metric reporting for pre-roll and noise-floor quality analysis
- Source request:
  - Add Track 2 endpoint-side support for cleaner ambient/SNR analysis.
- Scope:
  - Extend the endpoint-to-backend voice event payloads to carry privacy-safe audio metrics needed for ambient comparison.
  - Add firmware-side reporting for available pre-roll/noise-floor metrics, such as frame level, noise-floor estimate, speech peak level, pre-roll duration, and whether a chunk contains pre-roll or speech.
  - Keep reported metrics numeric and non-biometric; do not send environment classifications or raw ambient summaries beyond what is needed for quality analysis.
  - Preserve compatibility for endpoints that do not report these fields.
  - Update backend parsing and status/session diagnostics to prefer endpoint-provided ambient metrics when present and fall back to backend-derived metrics otherwise.
  - Add firmware/backend tests or contract tests for metric fields, backwards compatibility, and missing-field behavior.
  - Document that TV/music/appliance/background-speech classification remains out of scope for this task.
- Acceptance criteria:
  - Newer firmware can report ambient/pre-roll/noise-floor quality metrics to the backend.
  - Older firmware without the new fields remains compatible.
  - Backend quality diagnostics can use endpoint-provided metrics when available.
  - No raw ambient audio retention or environment classification is introduced by this task.


## Task 242
Original task details:
- Title: Enable the lowest-resource production Speaker ID provider path
- Source request:
  - Add Track 3 Speaker ID production-readiness work after a `[STOP]` gate.
  - For the first production provider, prefer the least resource-heavy option.
- Provider decision:
  - Keep `deterministic_signal` as a test/development adapter only.
  - Use the lowest-resource production-capable catalog option first. Based on the current provider catalog, `speechbrain_ecapa_tdnn` is the preferred first candidate because it has the smallest listed download and memory footprint among the production candidates.
  - Do not enable pyannote or NeMo as the first production path because they are heavier and/or have more model-access complexity.
- Scope:
  - Implement the `speechbrain_ecapa_tdnn` runtime adapter behind the existing Speaker ID adapter protocol.
  - Keep imports lazy so HexeVoice remains usable when SpeechBrain/Torch dependencies are not installed.
  - Add setup/status metadata that clearly reports installed, missing dependency, model unavailable, loaded, and implementation error states.
  - Keep CPU operation supported by default; CUDA can remain optional and should not be required for first-pass use.
  - Add a safe model download/cache policy consistent with existing runtime/provider patterns.
  - Preserve local-only biometric storage and avoid raw-audio retention by default.
  - Enforce raw-audio privacy rules:
    - discard normal voice-turn recordings immediately after derived processing completes
    - retain raw recordings only when explicit debug recording is enabled
    - limit debug raw-audio retention to one day
    - discard biometric enrollment voice audio immediately after embeddings/training data are extracted
  - Add targeted tests for adapter availability, missing dependency behavior, status reporting, and deterministic fallback/test behavior.
  - Document resource expectations, install requirements, and why SpeechBrain is the initial low-resource production candidate.
- Acceptance criteria:
  - Operators can select `speechbrain_ecapa_tdnn` and receive actionable status if dependencies or models are missing.
  - When dependencies and model are available, the adapter can extract embeddings and score candidates through the existing service API.
  - Missing dependencies do not break backend import, service startup, deterministic tests, or non-Speaker-ID voice turns.
  - Documentation identifies deterministic as test-only and SpeechBrain as the initial low-resource production candidate.


## Task 243
Original task details:
- Title: Add resource-aware Speaker ID enrollment readiness and confidence tiers
- Source request:
  - Continue Track 3 with low-resource, production-safe Speaker ID hardening before heavier providers are considered.
- Scope:
  - Add enrollment readiness checks that do not require heavy environment classifiers:
    - minimum sample count
    - minimum total speech duration
    - minimum per-sample duration
    - compatible sample rate
    - non-silent audio
    - clipping warnings
    - low-level warnings
  - Add configurable confidence tier mapping:
    - high: identify and eligible for future learning candidate workflows
    - medium: identify for low-risk personalization only, no learning
    - low/unknown: do not guess; ask or fail closed when identity is required
  - Preserve the existing score-margin check to reduce profile drift.
  - Add `learning_eligible=false` by default until explicit profile-learning workflows exist.
  - Attach tier and readiness metadata to privacy-safe Speaker ID diagnostics without exposing embeddings or raw audio.
  - Add tests for high, medium, low-confidence, low-margin, short enrollment, silent enrollment, and clipped enrollment behavior.
  - Document that automatic profile learning remains out of scope.
  - Enforce biometric training audio disposal:
    - use enrollment voice audio only long enough to extract accepted embeddings/training data
    - wipe/discard raw enrollment audio immediately after extraction
    - never retain raw biometric training voice samples unless a separate explicit debug mode is enabled
    - when debug retention is enabled, raw enrollment/debug audio expires after one day
- Enrollment phrase plan:
  - Use the following 24 phrases as the recommended enrollment phrase pool:
    1. "Hexe, turn on the lights in the living room."
    2. "What's the weather going to be like tomorrow morning?"
    3. "Play some music in the kitchen and set the volume to forty percent."
    4. "Remind me to call the dentist when I get home."
    5. "Who is at the front door, and when did they arrive?"
    6. "The quick brown fox jumps over the lazy dog."
    7. "Seven people bought fresh coffee, bread, cheese, and apples."
    8. "I'd like to know what's on my calendar for Friday afternoon."
    9. "Please turn the bedroom temperature down by two degrees."
    10. "Sometimes I speak quietly, and sometimes I speak much louder."
    11. "Hexe, what time is it?"
    12. "Could you please tell me whether the garage door is still open?"
    13. "Add tomatoes, pasta, olive oil, and basil to my shopping list."
    14. "Turn off the downstairs lights after the movie is finished."
    15. "Tell me how long the drive to the airport will take."
    16. "Please remind Sarah that the package is beside the front steps."
    17. "Set the hallway lights to a soft blue color tonight."
    18. "I need a quiet alarm for six fifteen tomorrow morning."
    19. "The old wooden clock stopped ticking during the storm."
    20. "Check whether any windows are open before bedtime."
    21. "Move my workout reminder from Monday to Wednesday evening."
    22. "Start a twenty five minute focus timer in the office."
    23. "Read the last notification from the security camera."
    24. "A bright yellow scarf was folded inside the small suitcase."
  - Treat the list as a phrase pool rather than a hard all-or-nothing requirement.
  - Require a minimum of 8 accepted phrases and recommend 12-16 accepted phrases when practical.
  - Target roughly 30-60 seconds total accepted speech per speaker profile.
  - Allow enrollment across multiple short sessions or slightly different positions.
  - Present enrollment in batches of 3 phrases.
  - After each batch, suggest that the person slightly change location, posture, direction, or distance from the endpoint before continuing, so the profile gains more natural variability.
  - Keep the location-change prompt advisory, not mandatory, unless enrollment quality remains poor.
  - Store one representative embedding per accepted phrase/sample.
  - Do not execute the spoken command during enrollment.
  - Include the wake word in only a minority of phrases to avoid overfitting profiles to wake-word audio.
  - Before starting enrollment capture, sample roughly 1 second of ambient audio or endpoint ambient metrics to verify that the current room condition is suitable.
  - Use the pre-enrollment ambient sample as a readiness check only by default:
    - warn or pause enrollment when ambient level is too high
    - warn when clipping or microphone noise is detected
    - report SNR readiness once Track 2 ambient/SNR support exists
    - do not retain raw ambient audio unless an explicit debug setting allows it
- Post-enrollment validation phrase plan:
  - Add a separate holdout phrase pool for scoring the completed profile after enrollment.
  - Do not use holdout validation samples to create or update the profile embeddings during the first implementation.
  - Randomly select a small subset, such as 5-8 phrases, after enrollment to score:
    - Speaker ID recognition
    - STT expected-text accuracy
    - audio quality
    - ambient/SNR quality when Track 2 data is available
  - Enrollment phrases may also be used for STT/audio-quality scoring during capture, but they should not be treated as independent Speaker ID validation because they contributed to the profile.
  - Reject or mark validation samples as unusable when audio quality is too poor, speech is too short, clipping is high, or ambient/SNR is unacceptable.
  - Use the following initial holdout phrase pool:
    1. "Please add blueberries and yogurt to the grocery list."
    2. "Is the upstairs hallway light still on?"
    3. "Set a ten minute timer for the pasta."
    4. "Tell me if the mail has arrived today."
    5. "Move tomorrow's meeting from nine thirty to ten."
    6. "Start the coffee maker at seven fifteen in the morning."
    7. "I left my blue jacket beside the small wooden table."
    8. "Read the next message from Alex out loud."
    9. "How long will it take to drive downtown right now?"
    10. "Dim the porch lights after sunset."
    11. "Cancel the reminder about watering the plants."
    12. "The silver train crossed the bridge before sunrise."
    13. "Please lower the speaker volume in the office."
    14. "Check whether the back gate was opened today."
    15. "Add black pepper, rice, lemons, and tea to the list."
    16. "What appointments do I have after lunch tomorrow?"
    17. "Turn off the fan when the room gets cooler."
    18. "The small red notebook is under the kitchen chair."
    19. "Remind me to charge the camera batteries tonight."
    20. "Play the latest episode in the living room."
    21. "How much time is left on the laundry timer?"
    22. "Please tell me the temperature outside."
    23. "Wake me up at six forty five on Saturday."
    24. "The garden hose is coiled beside the garage door."
- Acceptance criteria:
  - Speaker profiles can report whether enrollment data is sufficient for production use.
  - Speaker ID responses include a confidence tier without exposing biometric templates.
  - Medium confidence does not permit profile learning.
  - Low confidence or low margin does not guess identity for required personal routes.
  - All new checks use lightweight local metrics and do not require pyannote, YAMNet, NeMo, or background classification.
  - Enrollment UI/API guidance exposes the 24-phrase pool, requires at least 8 accepted phrases, recommends 12-16 when practical, and records accepted sample count and total accepted speech duration.
  - Enrollment flow presents phrases in batches of 3 and prompts for slight position/location variation between batches.
  - Enrollment capture performs a pre-enrollment ambient compatibility check and warns or pauses when the room is not suitable for profile creation.
  - Post-enrollment validation can select random holdout phrases, score Speaker ID/STT/audio quality, and avoid using failed-quality samples as profile-learning candidates.


## Task 244
Original task details:
- Title: Add versioned enrollment and validation phrase set management
- Source request:
  - Add roadmap improvement so enrollment and holdout phrase pools can evolve without making old scores ambiguous.
- Scope:
  - Store the enrollment phrase pool and holdout validation phrase pool with explicit phrase-set version IDs.
  - Include phrase-set version in enrollment records, validation reports, placement reports when phrase-based scoring is used, and observation/debug diagnostics where relevant.
  - Support active phrase-set selection through configuration or local defaults.
  - Preserve the current 24 enrollment phrases and 24 holdout validation phrases as the initial phrase-set version.
  - Track which phrases were presented, accepted, skipped, failed quality checks, and used for validation.
  - Add tests for phrase-set version persistence, random holdout selection, skipped phrases, and report attribution.
- Acceptance criteria:
  - Enrollment and validation reports can identify which phrase-set version was used.
  - Future phrase changes do not invalidate or confuse historical validation scores.
  - Initial phrase sets match the current roadmap phrase pools.



## Task 245
Original task details:
- Title: Add speaker profile age-band metadata, restrictions, and review cadence
- Source request:
  - Add Speaker ID profile age-band metadata so policies can restrict child/teen/admin behavior and recommend more frequent profile review for kids and teens whose voices change more often.
- Scope:
  - Add admin/guardian-configured profile metadata; do not infer age from voice.
  - Prefer broad age bands over exact birthday unless exact age is explicitly needed later:
    - `child`: under 13
    - `teen`: 13-17
    - `adult`: 18+
    - `unknown`: not set
  - Add optional profile metadata:
    - `age_band`
    - `age_restriction_class`
    - `guardian_managed`
    - `profile_review_interval_days`
    - `last_voice_profile_review_at`
    - `next_voice_profile_review_at`
    - `admin_eligible`
  - Add default review cadence suggestions:
    - child: every 30-60 days
    - teen: every 60-90 days
    - adult: every 180-365 days
    - unknown: configurable/manual review
  - Add default admin eligibility rules:
    - child: never admin eligible
    - teen: not admin eligible by default
    - adult: admin eligible only when explicitly enabled in UI
    - unknown: not admin eligible by default
  - Let intent/category policy use age band and restriction class for sensitive or age-restricted behavior.
  - Require operator/guardian review before profile-learning updates for child or teen profiles.
  - Expose age-band and review cadence in profile UI without storing exact birth date by default.
  - Add tests for child, teen, adult, and unknown defaults; admin eligibility; review due dates; and guardian-managed profile update restrictions.
  - Document that age band is operator/guardian supplied and not inferred from voice.
- Acceptance criteria:
  - Speaker profiles can store broad age-band metadata without requiring exact birth date.
  - Child and teen profiles receive more frequent review suggestions than adult profiles.
  - Child profiles cannot be admin eligible.
  - Teen and unknown profiles are not admin eligible by default.
  - Admin intent policy can require adult/admin-eligible profile metadata.
  - Child/teen profile learning requires operator/guardian review.


## Task 246
Original task details:
- Title: Add child/teen endpoint audience mode and age-appropriate content restrictions
- Source request:
  - Add an endpoint setting for child/teen use so children cannot ask inappropriate things from endpoints intended for minors.
- Scope:
  - Add endpoint-level audience mode metadata, configured in UI/setup by an admin/guardian:
    - `general`
    - `child_safe`
    - `teen_safe`
    - `adult_unrestricted`
  - Keep endpoint audience mode separate from speaker age band. Apply the stricter policy when either the speaker profile or endpoint mode indicates child/teen restrictions.
  - Add policy behavior for restricted endpoints:
    - block explicit adult, sexual, violent, illegal, self-harm-instructional, or otherwise inappropriate content
    - block admin/debug/privacy/purge/passcode/enrollment actions unless an identified adult/admin explicitly overrides through UI or an approved admin flow
    - restrict personal-sensitive routes according to guardian/admin settings
    - provide age-appropriate refusal text without exposing policy internals
  - Add per-endpoint controls in the operator UI and endpoint registry metadata.
  - Add an override model for adult/admin use at a restricted endpoint:
    - require high-confidence adult/admin Speaker ID
    - require explicit UI/admin setting for which overrides are allowed
    - require spoken passcode only for admin-maintenance actions
    - log only privacy-safe derived metadata for override decisions
  - Add tests for child-safe endpoint, teen-safe endpoint, adult endpoint, unknown speaker on child-safe endpoint, adult override, admin action blocking, and refusal response text.
  - Document that endpoint audience mode is a local household safety policy, not a substitute for broader platform safety controls.
- Acceptance criteria:
  - Endpoints can be configured as child-safe or teen-safe from UI/setup.
  - Child/teen endpoint mode restricts inappropriate content even when the speaker is unknown.
  - The stricter of speaker age band and endpoint audience mode wins.
  - Adult/admin override requires explicit configuration and high-confidence adult/admin Speaker ID.
  - Restricted responses are age-appropriate and do not reveal sensitive policy internals.

## Task 247
Original task details:
- Title: Add explicit speaker-identity policy mapping for registered and built-in intents
- Source request:
  - Add Track 4 intent policy work after a `[STOP]` gate.
- Scope:
  - Make speaker identity requirements explicit for built-in/local intents and registered Voice intents.
  - Preserve existing generic/local behavior where timers, endpoint commands, playback commands, and time queries do not require identity.
  - Support policy values already used by the turn pipeline:
    - `not_required`
    - `use_if_ready`
    - `required`
    - `forbidden`
  - Add a human-readable intent identity classification ladder that maps onto runtime policy:
    - `general`: identity not needed; execute without Speaker ID. Examples: time, simple status, generic household commands.
    - `household_context`: identity optional/use-if-ready; can use room/household context but must not require a person. Examples: shared lights, shared timers, shared device queries.
    - `personal_low_risk`: identity preferred or required depending on route; use for low-risk personalization. Examples: music profile, preferred voice, non-sensitive reminders.
    - `personal_sensitive`: identity required with high confidence. Examples: calendar, email, personal messages, account/profile data.
    - `admin_maintenance`: admin identity required with very high confidence plus spoken passcode. Examples: debug mode, enrollment, placement calibration, privacy purge, passcode rotation.
    - `forbidden_identity`: Speaker ID results must not be used or attached. Speaker ID may have already run speculatively in parallel with STT, but the result must be discarded for this turn. Examples: explicitly anonymous/local-only routes or future privacy-sensitive no-person-context modes.
  - Because Speaker ID runs in parallel with STT before the final intent policy may be known, implement policy as a usage/attachment gate:
    - `forbidden_identity`: discard Speaker ID result and do not attach it to assistant context, intent logs, observation logs, or public events.
    - `general`: do not require identity; do not wait for identity; attach only if policy and privacy settings explicitly allow already-ready metadata.
    - `household_context`: use ready identity only when confidence is acceptable; for low-confidence best matches, ask a lightweight confirmation such as "Is this Dan?" before using person context.
    - `personal_low_risk`: allow follow-up confirmation for low/medium-confidence identity, such as "Is this Dan?", instead of silently guessing.
    - `personal_sensitive`: require high-confidence identity and sufficient margin; if not met, reject/fail closed with a response such as "I could not recognize the speaker. If this happens often, retrain the profile."
    - `admin_maintenance`: require very high-confidence admin identity, strong margin, acceptable audio quality, and correct spoken passcode; otherwise reject/fail closed with the same recognition guidance.
  - Allow registered intent metadata to declare `speaker_identity_policy` where appropriate.
  - Allow registered intent metadata to declare `identity_classification` where appropriate, and derive `speaker_identity_policy` from it when explicit policy is absent.
  - Prefer explicit intent metadata over phrase heuristics when both are available.
  - Require every built-in and registered intent to expose either an explicit `identity_classification` or an explicit `speaker_identity_policy`.
  - Keep text heuristics only as a fallback for obvious personal routes until all relevant registered intents provide explicit policy.
  - Ensure identity-gated routes fail closed when Speaker ID is disabled, unavailable, low confidence, low margin, or unknown.
  - Add tests for built-in generic intents, personal-route metadata, heuristic fallback, forbidden policy, low-confidence household confirmation, low-confidence personal-low-risk confirmation, sensitive rejection, admin rejection, and disabled Speaker ID behavior.
  - Document policy ownership and examples.
- Acceptance criteria:
  - Built-in generic intents continue to execute without waiting for Speaker ID.
  - Registered intents can explicitly require, forbid, or optionally use Speaker ID.
  - Every built-in and registered intent reports an identity classification or policy.
  - Identity classifications map consistently to runtime Speaker ID policy.
  - Explicit intent policy wins over heuristic text matching.
  - `forbidden_identity` never attaches speculative Speaker ID results to intent logs or assistant context.
  - Household and personal-low-risk classifications can ask for speaker confirmation when identity confidence is low or uncertain.
  - Personal-sensitive and admin-maintenance classifications reject/fail closed unless confidence and margin requirements are met.
  - Required identity failures do not execute personal actions.


## Task 248
Original task details:
- Title: Add safe profile-learning eligibility policy without automatic learning
- Source request:
  - Continue Track 4 by defining profile-learning safety policy while keeping actual automatic learning disabled.
- Scope:
  - Add a `learning_eligible` or equivalent diagnostic decision to Speaker ID turn metadata.
  - Keep automatic profile updates disabled by default and out of scope for this task.
  - Mark a turn as learning-eligible only when all of these are true:
    - speaker confidence tier is high
    - score margin is sufficient
    - audio quality is acceptable
    - profile consent allows derived biometric updates
    - intent/route policy does not forbid learning
    - identity was not obtained from a clarification guess
  - Mark medium-confidence identity as personalization-only and not learning-eligible.
  - Mark low-confidence, low-margin, low-SNR, clipped, too-short, or unknown turns as not learning-eligible with a reason.
  - Attach only privacy-safe eligibility metadata to session diagnostics.
  - Add tests for eligible, medium-confidence, low-margin, low-SNR, clipped, too-short, forbidden-policy, and missing-consent cases.
  - Document that this task creates eligibility metadata only; any future profile update workflow requires a separate explicit task.
- Acceptance criteria:
  - Voice turn metadata can explain whether a high-confidence identified turn could be considered for future profile learning.
  - No embeddings or profiles are updated automatically.
  - Medium and low-confidence turns are not learning-eligible.
  - Audio-quality warnings can disqualify profile-learning eligibility.


## Task 249
Original task details:
- Title: Add global voice privacy mode and feature kill switch
- Source request:
  - Add roadmap improvement for a single operator control that can disable privacy-sensitive voice features.
- Scope:
  - Add a global local privacy mode/off switch for privacy-sensitive voice features.
  - When enabled, disable or block:
    - Speaker ID lookup and profile learning eligibility
    - observation logging
    - debug raw-audio recording
    - passive ambient calibration
    - admin maintenance voice intents
    - profile enrollment captures
  - Keep core non-personal voice operation available where possible, such as general/local commands that do not require identity.
  - Expose state in UI/setup/status and local diagnostics.
  - Require explicit operator action to disable privacy mode.
  - Add tests for each blocked feature and for generic voice turns that should still work.
  - Document guest/privacy use cases and recovery path.
- Acceptance criteria:
  - One operator-controlled privacy mode can stop all identity/logging/debug/calibration/admin voice features.
  - Generic no-identity voice turns can still run when safe.
  - Blocked features fail closed with clear operator-visible reasons.


## Task 250
Original task details:
- Title: Add backend wake-candidate election protocol and simulated multi-endpoint arbitration
- Source request:
  - Add Track 5 endpoint wake-election work after a `[STOP]` gate.
- Scope:
  - Add a backend-first wake election protocol so multiple endpoints hearing the same wake word do not all stream full utterances.
  - Define a candidate event/payload, such as `wake.candidate`, containing privacy-safe metrics:
    - wake confidence
    - speech/frame level
    - ambient level or SNR when available
    - endpoint id
    - session/candidate id
    - timestamp
    - optional endpoint audio profile/version
  - Add a short backend election window, likely 150-300 ms, configurable.
  - Score candidates by wake confidence and audio quality metrics.
  - Select one winning endpoint and reject or stand down the rest.
  - Add backend-to-endpoint events/commands for winner acceptance and loser stand-down, while preserving compatibility for existing endpoints.
  - Keep existing single-endpoint behavior unchanged.
  - Add simulated multi-endpoint tests for:
    - single candidate
    - clear winner
    - near tie
    - late candidate after election closes
    - missing metrics
    - disconnected loser endpoint
  - Document the protocol and first-pass scoring policy.
- Acceptance criteria:
  - Backend can elect one endpoint from multiple wake candidates without requiring physical hardware.
  - Existing endpoints that stream the current way still work.
  - Election diagnostics explain winner, losers, metrics, score, and reason.
  - No raw audio is required for candidate election.


## Task 251
Original task details:
- Title: Implement firmware wake-election candidate and stand-down behavior
- Source request:
  - Continue Track 5 after backend protocol validation by adding endpoint firmware support.
- Scope:
  - Update firmware to report wake candidate metrics before streaming a full utterance when backend election is enabled.
  - Include endpoint-side metrics available at wake time:
    - wake source/confidence where available
    - frame or speech level
    - ambient/noise-floor metrics when available
    - candidate timestamp/session id
  - Wait for backend winner acceptance before streaming full post-wake utterance, subject to a short timeout/fallback policy.
  - Honor backend stand-down command by cancelling capture and returning to idle/wake-armed state.
  - Preserve manual/button wake behavior and existing non-election flow when backend or firmware election is disabled.
  - Add firmware/backend contract tests or validation scripts for candidate, winner, loser, timeout fallback, and stand-down behavior.
  - Document firmware configuration, timeout behavior, and known limitations.
- Acceptance criteria:
  - Election-capable firmware can send candidate metrics and wait briefly for backend selection.
  - Losing endpoints stop streaming and return to idle without playing a response.
  - If election is unavailable or times out, firmware follows a safe fallback that does not permanently block voice turns.
  - Existing firmware behavior remains compatible when election is disabled.


## Task 252
Original task details:
- Title: Add active endpoint placement test workflow and placement report
- Source request:
  - Add Track 6 placement calibration work after a `[STOP]` gate.
  - Include the first implementation path before passive/long-window calibration.
- Scope:
  - Add a manual active placement test mode for a selected endpoint and room/zone.
  - Let the operator start a test, speak known phrases from normal room positions, and optionally identify the expected speaker.
  - Run the full accepted-turn pipeline for active test samples:
    - STT
    - Speaker ID when enabled
    - Track 1 audio quality
    - Track 2 ambient/SNR when available
  - Store privacy-safe placement test metrics by default, not raw audio.
  - Discard placement-test raw audio immediately after STT, Speaker ID, and audio-quality processing unless explicit debug recording is enabled for one-day retention.
  - Compare expected phrase/speaker with observed transcript and Speaker ID result.
  - Generate a placement report with:
    - STT success/accuracy signal
    - Speaker ID confidence/reliability signal
    - SNR and audio-quality warnings
    - clipping/low-level/too-short indicators
    - response consistency across test positions
    - overall placement score and recommendation
  - Add backend APIs and persistence for active placement test sessions/results.
  - Add operator UI or local Node UI surfaces as appropriate for starting tests and viewing reports.
  - Add tests for successful active test, failed transcript match, unknown speaker, low SNR, clipped audio, and report scoring.
  - Document that active placement testing is operator-initiated and may run STT/Speaker ID because the user intentionally starts the test.
- Acceptance criteria:
  - Operator can run a manual placement test for one endpoint without editing files.
  - Placement report combines STT, Speaker ID, and audio-quality metrics into a readable score/recommendation.
  - Raw audio is not retained by default.
  - Any debug-retained placement-test raw audio expires after one day.
  - Active tests are clearly separated from normal voice turns and passive ambient sampling.


## Task 253
Original task details:
- Title: Add passive ambient placement calibration and long-window placement scoring
- Source request:
  - Add the later Track 6 implementation for passive/periodic ambient calibration after the active placement test path exists.
- Scope:
  - Add optional passive placement calibration mode for selected endpoints.
  - Periodically collect privacy-safe ambient metrics, such as every 10 minutes over a 24-48 hour calibration window.
  - Collect metrics only by default:
    - ambient RMS/level
    - peak level
    - clipping incidence
    - speech-like activity presence
    - SNR-related metrics when active test anchors exist
    - optional lightweight environment labels only if a later approved classifier exists
  - Do not run STT or Speaker ID on unattended passive samples.
  - Do not retain raw passive ambient audio by default.
  - If passive calibration debug capture is explicitly enabled, raw ambient debug audio expires after one day.
  - Add scheduling, retention, cancellation, and status APIs for passive calibration windows.
  - Merge passive ambient statistics with active placement test results to produce a long-window placement report:
    - average ambient noise by time of day
    - peak noise periods
    - speech-like/background activity frequency
    - SNR distribution
    - active-test STT success
    - active-test Speaker ID reliability
    - overall placement score
  - Add tests for scheduling, metric-only storage, no unattended STT/Speaker ID calls, cancellation, retention cleanup, and report aggregation.
  - Document privacy behavior, operator controls, and why passive calibration is metric-only.
- Acceptance criteria:
  - Passive calibration can run for a configured endpoint/window and store metrics only.
  - No unattended passive sample is transcribed or sent to Speaker ID.
  - Raw ambient audio is not retained by default.
  - Any debug-retained passive ambient audio expires after one day.
  - Long-window placement reports combine passive ambient trends with active placement test performance.


## Task 254
Original task details:
- Title: Add optional monthly voice quality observation log
- Source request:
  - Add an optional end-of-implementation log that records time, STT, Speaker ID score, ambient/noise detection, and audio quality in its own persistent log.
  - Retain logs for one calendar month, not 30 days. For example, on August 26, delete records/files before July 26.
- Scope:
  - Add an operator-controlled local setting for voice quality observation logging.
  - Keep this log separate from existing voice session history, wake recordings, micro-VAD recordings, and runtime logs.
  - Store records in a dedicated runtime directory, preferably as one JSONL file per local date, for example `runtime/voice_quality_observations/YYYY-MM-DD.jsonl`.
  - Each record should include privacy-safe structured fields:
    - observed_at timestamp
    - endpoint_id
    - session_id
    - STT transcript text and STT provider/model/confidence when logging is enabled
    - Speaker ID public id/display name when policy permits
    - Speaker ID score, confidence, margin, tier, and reason
    - ambient/noise detection result when available, such as loud TV, multiple people, background speech, music-like audio, or unavailable
    - audio-quality result, including duration, RMS/level, clipping, silence/active ratio, ambient level, SNR, quality status, and warnings
    - source feature versions or schema version for future compatibility
  - Do not store raw audio, embeddings, biometric templates, or model-internal features in this log.
  - Keep observation logging derived-data only:
    - do not create a new raw-recording retention path
    - discard raw audio immediately after STT, Speaker ID, ambient/noise, and audio-quality processing
    - if debug recording is enabled elsewhere, raw debug recordings expire after one day
  - Make transcript logging explicit because STT text may contain personal data.
  - Support a redacted mode if transcript retention is later disabled but quality/Speaker ID summaries should still be kept.
  - Add calendar-month retention cleanup:
    - calculate cutoff by subtracting one calendar month from the current local date
    - delete whole daily files older than the cutoff date when file-per-day storage is used
    - never use fixed 30-day retention for this log
  - Add status/diagnostic surface showing logging enabled state, directory, retention policy, latest file, latest cleanup, and record counts where cheap to compute.
  - Add tests for:
    - writing a record
    - disabled logging writes nothing
    - transcript redaction mode
    - no raw audio or embedding fields are persisted
    - calendar-month cutoff behavior, including August 26 deleting before July 26
    - per-day file cleanup
  - Document privacy behavior, operator controls, and retention semantics.
- Acceptance criteria:
  - When enabled, accepted voice turns can write a structured observation record containing time, STT, Speaker ID score, ambient/noise result, and audio quality.
  - The observation log is stored separately from normal session history and debug audio recordings.
  - Logs are retained for one calendar month by date, not for 30 days.
  - On August 26, records/files before July 26 are removed by cleanup.
  - No raw audio, embeddings, or biometric templates are written to the observation log.


## Task 255
Original task details:
- Title: Add admin-gated maintenance voice intents for debug, enrollment, and placement calibration
- Source request:
  - Add voice intents for:
    - start debug
    - Voice enrollment
    - start placement analysis, including a 48-hour long analysis and a short phrase-based noise/STT scoring flow
  - These intents should be active only when requested by an admin person configured in the UI and protected by a spoken 4-digit passcode.
- Scope:
  - Add an admin maintenance intent family that is disabled by default.
  - Add UI/setup controls for:
    - enabling admin maintenance voice intents
    - selecting which enrolled speaker profiles are admins
    - configuring or rotating a 4-digit spoken passcode
    - selecting which admin intents are enabled
  - Require all admin maintenance intents to pass:
    - Speaker ID identifies an enrolled admin profile with very high confidence
    - admin maintenance intents are enabled in UI/setup
    - the specific requested intent is enabled
    - the spoken 4-digit passcode is present and correct
    - audio quality is acceptable enough for passcode recognition
  - Use a stricter Speaker ID threshold/tier for spoken PIN acceptance than normal personalization:
    - require very high confidence
    - require a strong best-vs-second-speaker margin
    - reject medium, low, unknown, timed-out, or unavailable Speaker ID results
    - reject passcode acceptance when audio quality is poor enough to reduce identity confidence
  - Treat spoken passcode as a local safety gate, not as strong authentication by itself.
  - Avoid logging the raw spoken passcode in session history, observation logs, runtime logs, MQTT events, or UI diagnostics.
  - Store passcode using a local protected/hashed representation, not plaintext.
  - Add replay/overhear mitigations where practical:
    - require recent live wake/session context
    - reject low-quality, clipped, or suspiciously short passcode utterances
    - rate-limit failed passcode attempts
    - temporarily lock admin voice intents after repeated failures
    - allow UI override/disable
  - Add the requested admin intents:
    - `admin.debug.start`: enable debug mode or debug recording according to existing privacy limits
    - `admin.enrollment.start`: begin Voice/Speaker enrollment flow
    - `admin.placement.start_active`: begin short phrase-based placement/noise/STT scoring flow
    - `admin.placement.start_passive_48h`: begin 48-hour passive placement analysis
  - Add companion stop/cancel/status intents so admin workflows can be controlled safely:
    - `admin.debug.stop`
    - `admin.enrollment.cancel`
    - `admin.placement.status`
    - `admin.placement.stop`
  - Add additional admin intents:
    - `admin.privacy.status`: report whether debug recording, observation logging, and calibration sampling are enabled
    - `admin.privacy.purge_debug_audio`: purge debug raw-audio captures; require UI confirmation or second confirmation before destructive action
    - `admin.voice.quality.status`: summarize recent audio-quality/SNR issues
    - `admin.speaker.enrollment.status`: report enrollment readiness progress
    - `admin.passcode.rotate`: start passcode rotation flow; require UI confirmation before accepting a new passcode
  - Keep destructive or privacy-sensitive actions fail-closed and consider requiring UI confirmation in addition to voice for purge/rotation actions.
  - Classify all admin maintenance intents as `admin_maintenance` under the Track 4 identity classification ladder.
  - Add tests for disabled admin intents, non-admin speaker, missing passcode, wrong passcode, correct passcode, failed-attempt lockout, passcode redaction, and each requested admin intent.
  - Document admin intent setup, privacy behavior, passcode limitations, lockout behavior, and recovery path.
- Acceptance criteria:
  - Admin maintenance voice intents are disabled by default.
  - Enabled admin intents require an identified admin speaker and the correct spoken 4-digit passcode.
  - All admin maintenance intents are classified as `admin_maintenance`.
  - Spoken passcode acceptance requires very high-confidence admin Speaker ID with a strong score margin.
  - Passcodes are never stored or logged in plaintext.
  - Requested intents can start debug, Voice enrollment, short placement scoring, and 48-hour placement analysis.
  - Stop/status companion intents exist for debug, enrollment, placement, privacy, voice quality, and passcode workflows.
  - Non-admin speakers and wrong passcodes fail closed.


## Task 256
Original task details:
- Title: Add operator-reviewed Speaker ID profile improvement workflow
- Source request:
  - Add roadmap improvement for profile learning only after operator review, not automatic updates.
- Scope:
  - Consume `learning_eligible` candidates from earlier tasks without automatically updating embeddings.
  - Provide an operator review queue for candidate profile improvements.
  - Show privacy-safe evidence:
    - speaker public id/display name
    - score/confidence/margin/tier
    - audio-quality/SNR summary
    - phrase/intent context when allowed
    - reason candidate is eligible
  - Never show or store raw audio unless explicit one-day debug retention is enabled.
  - Let operator approve, reject, or discard candidates.
  - On approval, update profile embeddings according to a bounded strategy that avoids profile drift.
  - Add tests for candidate creation, approval, rejection, drift/margin guardrails, privacy redaction, and profile versioning.
  - Document that automatic profile learning remains disabled unless a future explicit policy enables it.
- Acceptance criteria:
  - Eligible profile-learning candidates require operator approval before profile updates.
  - Rejected candidates do not alter profiles.
  - Approved updates increment profile version and preserve privacy constraints.


## Task 257
Original task details:
- Title: Add user-facing voice failure guidance from quality and identity diagnostics
- Source request:
  - Add roadmap improvement so recognition failures explain likely causes instead of only saying the speaker was not recognized.
- Scope:
  - Add a guidance layer that converts Speaker ID, STT, ambient/SNR, and audio-quality diagnostics into short user-facing responses.
  - For personal-sensitive and admin-maintenance failures, prefer safe rejection with helpful cause when known:
    - speaker not recognized
    - confidence too low
    - score margin too close
    - speech too short
    - audio clipped
    - room too noisy / low SNR
    - multiple speakers or background speech likely
    - profile may need retraining
  - Avoid exposing sensitive internal scores unless in admin/operator diagnostics.
  - Keep normal user prompts concise, for example: "I could not recognize the speaker. The room may be too noisy. If this happens often, retrain the profile."
  - Add operator diagnostics with more detailed reason codes.
  - Add tests for each major failure cause and for redaction of sensitive details.
- Acceptance criteria:
  - Failed identity-gated actions return clear, safe, concise guidance.
  - Operator diagnostics expose structured reason codes.
  - User-facing guidance does not expose private scores or biometric details.


## Task 261
Original task details:
- Title: Define portable firmware board-profile config schema for YAML/JSON-driven hardware bring-up
- Source request:
  - Add tasks to make the firmware as portable as possible with different hardware types so a new board can work from a simple YAML/JSON config.
- Scope:
  - Define a firmware board-profile schema that can be authored as YAML or JSON.
  - Cover board identity, ESP-IDF target, flash/partition assumptions, audio input, audio output, I2S pins, codec type/address, buttons, mute controls, LEDs, display, touch, rotary controls, SD/media, power behavior, and optional capabilities.
  - Make unsupported hardware explicit with `disabled`, `not_present`, or `stub` values rather than requiring C++ edits.
  - Separate portable feature configuration from board-specific driver implementation.
  - Include validation rules for required fields, mutually exclusive peripherals, pin conflicts, supported sample rates, and capability dependencies.
  - Add example configs for current supported boards and at least one minimal headless/no-display voice endpoint.
  - Document how backend endpoint capabilities map to firmware board-profile fields.
- Acceptance criteria:
  - A versioned board-profile schema exists for YAML and JSON input.
  - Current ESP-BOX-3 and HA Voice PE profiles can be represented by the schema.
  - A minimal new board can declare mic, speaker, buttons, and no display without firmware source edits.
  - Schema validation catches missing required hardware, invalid pins, and incompatible capability combinations.


## Task 262
Original task details:
- Title: Refactor firmware board support behind config-driven portable hardware adapters
- Source request:
  - Make firmware portable across different hardware types using simple board configs.
- Scope:
  - Introduce a portable board hardware abstraction layer that loads generated board-profile constants instead of hard-coding board behavior directly in feature code.
  - Move board-specific audio input/output, codec, display, touch, button, LED, SD, mute, and rotary setup behind common adapter interfaces.
  - Preserve existing ESP-BOX-3 and HA Voice PE behavior while reducing compile-time conditionals in voice/session code.
  - Support graceful no-op adapters for hardware not present on a board.
  - Ensure endpoint heartbeat/capability reporting is derived from the selected board profile.
  - Keep wake/STT/assistant ownership boundaries intact: backend wake word remains available as backup, and endpoint microWakeWord remains an optional provider.
  - Add firmware tests/static checks that verify unsupported board profiles fail early with actionable errors.
- Acceptance criteria:
  - Voice/session firmware code consumes portable adapter interfaces instead of directly depending on board-specific modules.
  - Existing board profiles build and keep their current capabilities.
  - Missing peripherals degrade to explicit no-op/unsupported states rather than crashes.
  - Capability heartbeat output matches the configured board profile.


## Task 263
Original task details:
- Title: Add new-board bring-up generator, examples, validation tests, and docs for config-only firmware ports
- Source request:
  - Enable adding a new board through a simple YAML/JSON config whenever the hardware uses supported adapter types.
- Scope:
  - Add a generator that converts a board YAML/JSON profile into firmware config headers/source or CMake fragments used by the portable adapters.
  - Add a `build.sh` or CMake flow for selecting generated board profiles without manually editing firmware source.
  - Provide a new-board template and bring-up checklist covering pin mapping, codec validation, microphone capture, speaker playback, buttons, LEDs/display optionality, wake transport, TTS playback, OTA, and endpoint heartbeat.
  - Add CI/local validation for all committed board profiles, including schema validation and compile checks where possible.
  - Document the boundary: config-only ports work for hardware matching supported adapters; new chips/codecs/peripherals still require adding an adapter implementation.
  - Include migration notes for converting existing hand-coded board profiles into generated configs.
- Acceptance criteria:
  - A new supported-adapter board can be added by creating one YAML/JSON profile and running the generator.
  - Generated artifacts are deterministic and validated.
  - Current board profiles build through the generated/config-driven flow.
  - Docs explain when config is enough and when new firmware adapter code is required.


## Task 287
Original task details:
- Title: Reconcile HexeVoice BLE onboarding requirements with the current Core/Supervisor `ble.provision_wifi` contract, schema, lease policy, broker route, security model, and failure modes
- Source request:
  - Use the new Core Bluetooth access request as on-demand BLE transmission of Wi-Fi credentials for endpoint onboarding.
  - For this first task, check Core/Supervisor again because new BT features exist.
- Current Core/Supervisor reference points:
  - Core contract: `/home/dan/hexe/hexe/docs/core/ble-onboarding-contract.md`
  - Core hardware access model: `/home/dan/hexe/hexe/core/backend/app/system/hardware.py`
  - Supervisor broker route: `/api/supervisor/hardware/bluetooth/ble/provision-wifi`
  - Operation: `ble.provision_wifi`
  - Lease scope: `hardware.bluetooth.ble.provision_wifi`
  - Voice payload schema id: `hexe.voice_node.wifi_backend.v1`
  - GATT service UUID: `7f9c0000-5f04-4d8b-9a46-7c0f7a100000`
- Scope:
  - Compare the live Core/Supervisor contract and tests against HexeVoice firmware provisioning needs.
  - Document whether HexeVoice should act only as the target endpoint, as the requesting trusted voice node, or both depending on deployment mode.
  - Map Core voice payload fields to existing firmware NVS keys and runtime settings.
  - Define the HexeVoice-owned implementation boundary: endpoint BLE peripheral, backend/operator request orchestration, recovery-app behavior, and validation.
  - Identify any Core-owned missing pieces as external follow-up notes instead of silently inventing incompatible HexeVoice APIs.
  - Preserve the Core security boundary: Core owns policy/leases, Supervisor owns host BLE broker access, endpoint owns local credential application.
- Acceptance criteria:
  - A HexeVoice BLE onboarding integration plan exists and references the current Core/Supervisor `ble.provision_wifi` contract.
  - The plan explicitly states what is already Core-owned and what must be implemented in HexeVoice.
  - The plan confirms plaintext Wi-Fi credentials are not sent to Core, logged, or persisted outside the approved endpoint provisioning path.
  - Any Core/Supervisor gaps are captured as explicit follow-up requirements.


## Task 288
Original task details:
- Title: Implement the endpoint firmware BLE onboarding peripheral for the Core `ble.provision_wifi` GATT contract
- Source request:
  - Use Core-governed BLE credential transmission for endpoint onboarding.
- Scope:
  - Add a firmware BLE provisioning component, preferably using ESP-IDF NimBLE if size and board support allow.
  - Implement the Hexe BLE onboarding GATT service and characteristics from the Core contract:
    - device identity / board profile
    - pairing nonce / claim code
    - provisioning status
    - encrypted credential write
    - ack/error
  - Advertise only when unprovisioned, explicitly placed in provisioning mode, or running recovery provisioning.
  - Validate contract version, payload schema id, target node identity, pairing nonce or claim-code binding, replay protection, and payload size/chunking.
  - Decrypt and validate the voice Wi-Fi/backend payload before applying it.
  - Save settings through the existing endpoint provisioning path so Wi-Fi SSID/password, backend host/ports, TLS, endpoint name, and display name land in NVS consistently.
  - Restart or reconnect Wi-Fi after successful provisioning and disable BLE advertising after success or timeout.
  - Report BLE provisioning capability/status in endpoint heartbeat without exposing secrets.
  - Keep backend wake word and existing endpoint voice runtime behavior unchanged.
- Acceptance criteria:
  - Supported BLE boards can expose the Hexe onboarding service when eligible.
  - Valid provisioning payloads are written to existing NVS provisioning keys.
  - Invalid, expired, replayed, wrong-node, malformed, or undecryptable payloads fail closed with deterministic ack/error codes.
  - BLE advertising is off after successful provisioning or when the device is already provisioned unless explicitly re-enabled.
  - Firmware size remains within the selected partition slot targets or the size impact is documented before enabling by default.


## Task 289
Original task details:
- Title: Add HexeVoice backend and operator flow support for Core-governed BLE endpoint onboarding
- Source request:
  - Use the Core Bluetooth access request as on-demand BLE transmission of Wi-Fi credentials for endpoint onboarding.
- Scope:
  - Add backend-side orchestration for the operator flow:
    - discover Core hardware access schema
    - request a `ble.provision_wifi` lease with provisioning context
    - handle `denied`, `pending`, and `granted` statuses
    - call the Supervisor `provision-wifi` broker route with the lease token and credential payload
    - release the lease or allow expiry after completion
  - Add operator API/UI support for selecting an endpoint candidate, entering Wi-Fi/backend settings, viewing progress, and continuing normal node onboarding once the endpoint comes online.
  - Ensure Core receives only session/provisioning binding data and never plaintext Wi-Fi credentials.
  - Redact passwords in logs, API responses, events, UI state, and diagnostics.
  - Treat Core/Supervisor broker failures as recoverable onboarding errors with actionable status.
  - Preserve the existing backend-to-connected-endpoint provisioning command as the post-join/reconfiguration path.
- Acceptance criteria:
  - Operator can start a Core-governed BLE onboarding attempt from HexeVoice without direct host Bluetooth access.
  - Pending Core access requests are surfaced clearly when policy is `ask`.
  - Granted leases can drive a Supervisor broker call and show success/failure progress.
  - Wi-Fi passwords are never persisted or returned by HexeVoice backend outside the bounded transmit request.
  - After BLE provisioning succeeds and the endpoint connects, the existing node onboarding/trust flow remains the authority for registration.


## Task 290
Original task details:
- Title: Add recovery-app BLE provisioning support using the same Core contract where available and a local recovery-safe fallback when Core is offline
- Source request:
  - Endpoint onboarding should work through BLE, and recovery/provisioning should remain useful.
- Scope:
  - Reuse the endpoint BLE provisioning component from the recovery app when it fits the 2 MiB recovery partition.
  - In Core-available mode, honor the same `ble.provision_wifi` GATT contract, pairing, schema, and redaction behavior.
  - In Core-offline recovery mode, allow a local explicit provisioning path with short-lived local pairing and the same NVS write path.
  - Keep recovery diagnostics secret-safe and useful without Core.
  - Document entry conditions, button/display indications, timeout behavior, and reset behavior.
- Acceptance criteria:
  - Recovery firmware can provision Wi-Fi/backend settings over BLE on supported boards or explicitly reports BLE unavailable.
  - Recovery mode does not require Core for local rescue provisioning.
  - Core-governed and local recovery modes are visually/status distinguishable.
  - No plaintext credentials appear in recovery status, diagnostics, logs, or crash output.


## Task 291
Original task details:
- Title: Add BLE onboarding end-to-end tests, fake BLE/GATT harnesses, security regression checks, docs, and physical-device validation criteria across supported endpoint boards
- Source request:
  - Make BLE endpoint onboarding reliable enough for retiring current Echo-based setup and future board portability.
- Scope:
  - Add fake BLE/GATT test harnesses for firmware-facing and Supervisor-facing flows where physical Bluetooth is unavailable in CI.
  - Add backend tests for Core lease statuses, broker failures, credential redaction, operator status, and post-Wi-Fi node onboarding handoff.
  - Add firmware tests/static checks for GATT contract constants, payload validation, NVS writes, replay rejection, timeout behavior, and capability heartbeat fields.
  - Add board-profile validation for BLE availability and unsupported-board behavior.
  - Document physical validation steps for HA Voice PE, ESP32-S3-BOX-3, Waveshare S3 1.85C BOX V2, and future P4/C6 board flow.
  - Include failure cases: absent Bluetooth adapter, policy disabled, policy ask pending, lease expiry, wrong adapter, wrong node, wrong pairing nonce, malformed payload, failed Wi-Fi association, and backend unreachable.
- Acceptance criteria:
  - CI/local tests cover the security and state-machine behavior without needing physical Bluetooth hardware.
  - Docs provide a physical-device checklist for validating BLE onboarding on every supported endpoint class.
  - Security regression tests prove credentials are redacted and lease scopes cannot be reused across scan/status/provisioning operations.
  - The BLE onboarding batch has enough validation evidence to safely enable on supported boards.

## Task 292
Original task details:
- User request: Make onboarding work the other way around: the system publishes a BLE advert and the device looks for that advert, connects, and participates in an Add Device pairing session.
- Goal: Define the HexeVoice endpoint-side behavior for Core-published BLE pairing sessions before firmware changes.
- Align with the Core/Supervisor contract task for UUID reuse. Prefer reusing the existing Hexe onboarding service UUID if the contract clearly distinguishes endpoint-advert and host-advert roles.
- Define the endpoint states:
  - unprovisioned/recovery enters pairing scan mode
  - endpoint scans for a Hexe pairing-session advert
  - endpoint validates contract version, session hint, role flag, and expiry
  - endpoint connects to the Supervisor host GATT service
  - endpoint writes identity including board profile
  - endpoint receives encrypted provisioning payload through the approved path
  - endpoint applies settings and exits setup/recovery advertising/scanning
- Define what endpoint data is required for onboarding identity: node hardware id, target node id or generated candidate id, board profile, firmware version, application type, provisioning mode, endpoint public key, supported payload schemas, and status.
- Define the endpoint's stable device id as mandatory pairing identity. The endpoint must send it during BLE pairing and later present the same device id with the provisioning session id when it comes online over Wi-Fi.
- Define the operator approval handoff: user approves the BLE-reported device id, endpoint receives encrypted Wi-Fi/backend credentials, endpoint connects to Wi-Fi, then endpoint starts HexeVoice onboarding with the same provisioning session id and device id so HexeVoice can approve that exact device.
- Define retry/backoff, timeout, cancellation, and coexistence with the current device-advertises fallback flow.
- Acceptance: The endpoint-side design names the BLE central/client responsibilities and all required identity fields.
- Acceptance: Board profile is mandatory in the endpoint identity exchange.
- Acceptance: Device id is mandatory in the endpoint identity exchange and in the later Wi-Fi onboarding request.
- Acceptance: No plaintext Wi-Fi credentials, Core tokens, or trust secrets are advertised or logged.

## Task 293
Original task details:
- Depends on: Task 292 and the matching Core/Supervisor contract task.
- Goal: Implement endpoint firmware BLE central scanning for Hexe pairing-session adverts on supported boards.
- Add or extend the minimal/recovery firmware BLE component so HA Voice PE can scan for the Hexe pairing service UUID while in setup/recovery mode.
- Filter candidates by Hexe UUID, contract version, and host-advert role/session flags rather than by device name alone.
- Keep scan windows long enough for provisioning UX, with bounded retry/backoff and clear status reporting.
- Preserve the existing endpoint BLE peripheral advert path as fallback/debug unless the contract explicitly retires it.
- Ensure scanning stops after success, timeout, operator cancellation, or when normal provisioning state is reached.
- Add diagnostics that show state and high-level errors without exposing secrets.
- Acceptance: HA Voice PE minimal firmware can detect a Core/Supervisor pairing advert in setup/recovery mode.
- Acceptance: Unsupported boards fail closed with clear capability/status rather than crashing or silently hanging.
- Verification: Add firmware tests/static checks for UUID constants, role filtering, scan state transitions, timeout, and unsupported-board behavior.

## Task 294
Original task details:
- Depends on: Tasks 292 and 293 plus Core/Supervisor host GATT support.
- Goal: Implement endpoint-to-Supervisor GATT pairing identity exchange and provisioning handoff.
- Add endpoint BLE client behavior to connect to the Supervisor host GATT service for the selected pairing session.
- Read the pairing offer/session metadata and validate contract version, expiry, target/session binding, and supported payload schema.
- Write endpoint identity to the host GATT service, including mandatory device id, board profile, and firmware version.
- Continue to use the approved encrypted credential path for Wi-Fi/backend settings; do not accept plaintext credential writes except where an explicit local recovery contract says so.
- Apply Wi-Fi/backend settings through the existing NVS provisioning path and reconnect/reboot as required by the current firmware architecture.
- Persist enough provisioning handoff state for first Wi-Fi boot so the endpoint can call HexeVoice onboarding with the same provisioning session id and device id after network connection.
- Report status/ack/error states for success, invalid session, expired session, unsupported schema, decrypt failure, payload validation failure, Wi-Fi failure, backend unreachable, timeout, and already provisioned.
- Acceptance: Endpoint can complete the identity exchange and receive/apply provisioning data for a valid Core pairing session.
- Acceptance: Endpoint presents the same device id and provisioning session id during Wi-Fi onboarding that it used during BLE pairing.
- Acceptance: Wrong session, expired session, unsupported schema, malformed payload, or credential decryption failure fails closed.
- Verification: Add fake GATT/client tests where possible plus physical HA Voice PE validation.

## Task 295
Original task details:
- Depends on: Core Tasks 997-1000 and HexeVoice Tasks 292-294.
- Goal: Integrate HexeVoice onboarding UI/backend with Core pairing sessions and fallback scanning.
- Update the Voice Endpoint onboarding dialog so the normal path starts a Core pairing session and waits for the endpoint to self-discover/connect.
- Auto-fill endpoint fields from the endpoint identity returned by Core, especially board profile, target node id, firmware version, and display name.
- Show the BLE-reported device id as the identity the user is approving, then require the online endpoint onboarding request to present the same provisioning session id and device id before approval.
- Keep the current Supervisor UUID scan path available as an advanced fallback/debug action.
- Show user-friendly states: waiting for device, device found, reading identity, ready to provision, provisioning, waiting for endpoint online, completed, timed out, and failed.
- Hide internal fields such as pairing nonce, endpoint public key, lease token, adapter, and session id by default.
- Ensure Wi-Fi password handling remains redacted in logs, API responses, diagnostics, and UI state after submission.
- Acceptance: Operator can onboard a HA Voice PE from the popup without manually copying BLE onboarding fields.
- Acceptance: HexeVoice approves the device only when the Wi-Fi onboarding request matches the BLE-approved device id and provisioning session id.
- Acceptance: If no endpoint responds, the UI gives retry/cancel/fallback options.
- Acceptance: Existing backend-to-online-endpoint provisioning remains available for already-connected endpoints.
- Verification: Add focused backend/UI tests and run frontend build.
- Verification: Run physical scan/onboarding validation after firmware and Core changes are installed.
