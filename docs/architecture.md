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

See `docs/feature-spec.md` for the intended HexeVoice runtime behavior and endpoint model.
