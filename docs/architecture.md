# Architecture

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

See `docs/feature-spec.md` for the intended HexeVoice runtime behavior and endpoint model.
