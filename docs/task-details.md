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
- `../Hexe/docs/nodes/node-onboarding-api-contract.md`
- `../Hexe/docs/nodes/node-onboarding-phase1-contract.md`
- `../Hexe/docs/nodes/node-trust-activation-payload-contract.md`
- `../Hexe/docs/nodes/node-trust-status-contract.md`
- `../Hexe/docs/nodes/node-phase2-lifecycle-contract.md`
- `../Hexe/docs/nodes/node-capability-activation-architecture.md`
- `../Hexe/docs/nodes/node-lifecycle.md`
- `../Hexe/docs/nodes/node-onboarding-registration-architecture.md`
- `../Hexe/docs/nodes/onboarding-trust-terminology.md`
- `../Hexe/docs/json_schema/node_onboarding_start_request.schema.json`
- `../Hexe/docs/standards/Node/frontend-standard.md`
- `../Hexe/docs/standards/Node/frontend-visual-and-interaction-standard.md`

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
  - Reuse existing local TTS and media URL behavior rather than adding a new storage mechanism unless necessary.
  - Define failure behavior when TTS generation fails: fail the intent, return text-only, or mark audio unavailable based on intent policy.
- Completion criteria:
  - A registered intent can opt into reply audio generation without code changes.
  - The generated audio link can be pulled by an endpoint or another node before expiry.
  - Tests cover text-only, audio-required, audio-best-effort, and TTS failure paths.
