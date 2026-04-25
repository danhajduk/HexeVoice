# Architecture

See also: [Firmware Migration Plan](/home/dan/Projects/HexeVoice/docs/firmware-migration-plan.md)

HexeVoice is structured as a modular Hexe node starter:

- `config/` for typed configuration
- `runtime/` for orchestration
- `onboarding/` for onboarding state and flow
- `trust/` for trust state
- `core/` for Core client boundaries
- `capabilities/` for capability state
- `governance/` for readiness and governance state
- `providers/` for provider-specific behavior
- `persistence/` for stores
- `diagnostics/` for logging helpers
- `security/` for masking and redaction
- `api/` for typed response contracts used by the backend surface

The initial backend exposes the standard starter route groups for health, node status, onboarding, capabilities, governance/readiness, service status, and provider status.

For firmware bring-up, the backend also now exposes a minimal local assistant turn route:

- `POST /api/assistant/turn`

This route is intentionally lightweight. It gives endpoint firmware a stable request/response contract before the full wake, STT, upstream reasoning, and TTS pipeline is implemented. The current behavior supports:

- simple local commands such as `status`, `repeat`, and `stop`
- deterministic fallback replies for arbitrary text turns
- endpoint-scoped session ids so device-side integration can start immediately

The onboarding domain now aligns to the canonical Core 10-step node lifecycle:

1. `node_identity`
2. `core_connection`
3. `bootstrap_discovery`
4. `registration`
5. `approval`
6. `trust_activation`
7. `provider_setup`
8. `capability_declaration`
9. `governance_sync`
10. `ready`

The current runtime projects the first canonical state, `unconfigured`, while later Phase 0 tasks wire persistence, bootstrap discovery, trust activation, provider setup, and readiness progression into those steps.

HexeVoice persists onboarding and trusted-resume state in a node-local onboarding store under `runtime/onboarding_state.json` by default.
That store is the restart-safe boundary for:

- pre-trust local setup inputs
- onboarding session metadata
- trust activation metadata
- current-step and resume state

Later Phase 0 tasks add the APIs and runtime transitions that mutate this store.

Current local setup APIs:

- `GET /api/onboarding/local-setup`
- `PUT /api/onboarding/local-setup/node-identity`
- `PUT /api/onboarding/local-setup/core-connection`

These APIs own the pre-trust draft state for Node Identity and Core Connection and advance the local lifecycle projection into `core_connection` and `bootstrap_discovery` when the required data is present.

Current bootstrap discovery APIs:

- `GET /api/onboarding/bootstrap-discovery`
- `POST /api/onboarding/bootstrap-discovery/test-connection`
- `PUT /api/onboarding/bootstrap-discovery/advertisement`

The bootstrap step currently validates:

- bootstrap listener reachability on the configured bootstrap MQTT port
- topic match for `hexe/bootstrap/core`
- `onboarding_mode=api`
- `onboarding_contract=global-node-v1`
- `onboarding_endpoints.register_session=/api/system/nodes/onboarding/sessions`
- `onboarding_endpoints.registrations=/api/system/nodes/registrations`

When the bootstrap advertisement is accepted, the local lifecycle advances from `bootstrap_discovery` to `registration`.

Current onboarding session start API:

- `POST /api/onboarding/session/start`

This route uses the saved Node Identity, Core Connection, and bootstrap discovery state to call Core's canonical onboarding session start route:

- `POST {core_base_url}/api/system/nodes/onboarding/sessions`

On success, HexeVoice persists:

- `session_id`
- `approval_url`
- `expires_at`
- `finalize_url`

and advances the local lifecycle from `registration` to `approval`.

Current approval visibility APIs:

- `GET /api/onboarding/status`
- `POST /api/onboarding/session/poll`

The onboarding status payload now surfaces operator-facing approval metadata, including:

- `approval_url`
- `session_id`
- `expires_at`
- `finalize_url`
- `session_state`
- `last_polled_at`
- `last_terminal_outcome`

The session poll route calls Core's canonical finalize endpoint:

- `GET {core_base_url}/api/system/nodes/onboarding/sessions/{session_id}/finalize?node_nonce=...`

Current finalize handling supports the canonical outcome set:

- `pending`
- `approved`
- `rejected`
- `expired`
- `consumed`
- `invalid`

When finalize returns `approved`, HexeVoice persists the activation payload as pending protected state and advances the local lifecycle from `approval` to `trust_activation`.

Current trust activation API:

- `POST /api/onboarding/trust-activation/finalize`

This route consumes the pending approved activation payload exactly once, persists the canonical trust activation fields into the protected trust state, clears the staged activation payload from the onboarding session, and advances the local lifecycle from `trust_activation` to `provider_setup`.

Persisted trust activation fields now include:

- `node_id`
- `node_type`
- `paired_core_id`
- `node_trust_token`
- `initial_baseline_policy`
- `baseline_policy_version`
- `activation_profile`
- `operational_mqtt_identity`
- `operational_mqtt_token`
- `operational_mqtt_host`
- `operational_mqtt_port`
- `issued_at`
- `source_session_id`

Current trust resume and trust-loss API:

- `POST /api/onboarding/trust-status/refresh`

This route uses the canonical Core trust-status contract:

- `GET {core_base_url}/api/system/nodes/trust-status/{node_id}`

with the last issued `X-Node-Trust-Token` to distinguish:

- supported trusted resume
- explicit Core-side revocation
- explicit Core-side node removal

When Core still reports the node as supported and trusted, HexeVoice preserves trusted resume and keeps the node in the post-trust lifecycle. When Core reports `revoked` or `removed`, HexeVoice clears stale onboarding-session state, invalidates local trusted operation by dropping the operational MQTT token, records the explicit trust-loss metadata, and moves the local lifecycle back to `registration` for re-onboarding.

Current provider setup API boundary:

- `GET /api/providers/setup`
- `PUT /api/providers/setup`
- `GET /api/providers/{provider_id}/status`

Provider setup is now modeled as the node-local `capability_setup_pending` gate after trust activation. HexeVoice persists:

- `supported_providers`
- `enabled_providers`
- `default_provider`
- `declaration_allowed`
- provider setup `blocking_reasons`

Provider setup remains distinct from trust state. Trust must already be valid, and at least one enabled provider must be selected before the local lifecycle advances from `provider_setup` to `capability_declaration`.

Current Phase 2 backend surfaces:

- `POST /api/capabilities/declaration`
- `GET /api/governance/current`
- `POST /api/governance/refresh`
- `GET /api/node/operational-status`

These routes now implement the Core Phase 2 progression:

- capability declaration submits the node manifest using the trusted node token and persists accepted capability profile metadata
- governance fetch and refresh persist the active governance version, bundle, refresh interval, and freshness tracking metadata
- operational-status polling persists the canonical Core readiness projection fields including `capability_status`, `governance_status`, `operational_ready`, governance freshness state, and timestamps

HexeVoice now treats Core's `operational_ready` projection as the source of truth for the final transition from `governance_sync` to `ready`.

Current node-local status projection:

- `GET /api/node/status`
- `GET /api/onboarding/status`

These local status payloads now include the richer operator-facing readiness data the frontend will need:

- canonical current step and lifecycle projection
- trust state plus explicit trust-support metadata
- `capability_status` and `governance_sync_status`
- `operational_ready` and governance freshness summary
- `capability_setup` payload with:
  - readiness flags
  - provider selection state
  - task capability selection state
  - blocking reasons
  - declaration allowance

This keeps Core's `operational-status` route as the canonical readiness source while making the local node API the canonical source for setup gating detail.

Frontend visual foundation now uses the shared Hexe node shell under `frontend/src/theme/`:

- canonical `--sx-*` dark design tokens
- atmospheric dark page background
- centered 90vw application frame
- two-column shell layout with collapsible rail
- shared card, pill, callout, and facts-grid styling

The frontend also now has its missing Vite `index.html` entrypoint, so the app can be built locally with `npm install` followed by `npm run build` from `frontend/`.

The onboarding frontend shell now consumes `GET /api/onboarding/status` alongside `GET /api/node/status` and renders:

- current-stage hero context
- setup-flow sidebar from the canonical 10-step list
- stage-card presentation for the active onboarding step
- operator callouts for approval, trust activation, post-trust setup, and blocked readiness

This gives the frontend a real stage-aware setup surface before step-specific forms and actions are added in later tasks.

Frontend steps 1 through 3 now call the live node APIs for:

- local node identity save/resume
- Core base URL save/resume
- bootstrap connection testing
- bootstrap advertisement validation and inspection

The onboarding shell keeps local draft state in the browser, refreshes node/onboarding status after successful writes, and uses the persisted backend responses as the source of truth for progression into `registration`.

Frontend steps 4 through 6 now expose the Phase 1 control actions in the onboarding shell:

- registration session start
- approval/finalize polling
- trust activation finalize

The current UI also surfaces session metadata and terminal finalize outcomes so operators can recover from duplicate, rejected, expired, invalid, or consumed approval sessions without losing context.

Frontend steps 7 through 10 now expose the remaining setup actions directly in the onboarding shell:

- provider selection save
- capability declaration
- governance current/refresh
- operational-status polling

This means the full canonical 10-step flow is now present in the onboarding UI, with each step backed by the corresponding local or Core-facing API route.

The post-setup operator overview now renders as a separate surface beside the onboarding card and uses the standard visual grammar:

- facts grids for provider, readiness, and identity summaries
- state-grid rows for operational and diagnostic detail
- grouped action rows for refresh/runtime placeholders
- warning-card treatment for stale governance or setup blockers

This keeps setup and operational concerns visibly separated while still sharing one shell.

See `docs/feature-spec.md` for the intended HexeVoice runtime behavior and endpoint model.
