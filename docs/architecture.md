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

See `docs/feature-spec.md` for the intended HexeVoice runtime behavior and endpoint model.
