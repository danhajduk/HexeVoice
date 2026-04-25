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
