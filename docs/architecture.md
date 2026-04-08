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

See `docs/feature-spec.md` for the intended HexeVoice runtime behavior and endpoint model.
