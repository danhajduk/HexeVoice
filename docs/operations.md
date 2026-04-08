# Operations

## Operator Surfaces

HexeVoice now uses one shared operator shell with two distinct concerns:

- onboarding setup card:
  the canonical 10-step setup flow driven by local onboarding status and Core-backed readiness actions
- operational overview cards:
  post-setup summaries for readiness, provider configuration, governance, and diagnostics

These concerns intentionally share one shell while remaining visually separate.

## Setup Progression

During setup, operators should use the onboarding card in this order:

1. save Node Identity
2. save Core Connection
3. test and validate Bootstrap Discovery
4. start Registration
5. open the Approval URL and poll finalize state
6. finalize Trust Activation
7. save Provider Setup
8. declare Capabilities
9. fetch or refresh Governance
10. poll operational status until `operational_ready=true`

## Recovery Signals

The frontend and local API surface the following important recovery conditions:

- approval session rejected, expired, invalid, or consumed
- trust revoked or node removed by Core
- provider setup blockers before capability declaration
- governance stale or outdated conditions after trust

Operators should treat Core `operational_ready` as the source of truth for final readiness, even if intermediate lifecycle labels still show a compatibility-era state.

Use:

- `scripts/bootstrap.sh` to install user services
- `scripts/run-from-env.sh backend` to launch the backend from `scripts/stack.env`
- `scripts/run-from-env.sh frontend` to launch the frontend from `scripts/stack.env`
- `scripts/stack-control.sh` for service control
- `scripts/restart-stack.sh` to restart both services

Logs should be written under `runtime/logs/`.

Systemd templates for this node live at:

- `scripts/systemd/hexevoice-backend.service.in`
- `scripts/systemd/hexevoice-frontend.service.in`
