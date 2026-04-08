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
