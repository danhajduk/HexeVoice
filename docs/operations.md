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

The post-trust setup card remains available after setup completion. Operators can return to it to adjust provider selection, select/redeclare capabilities, and inspect or enable/disable registered Voice Node intents.

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

Systemd user units are intentionally not enabled for auto-start and do not declare a restart policy. Core Supervisor is the lifecycle authority for managed node runtime behavior.

When supervisor integration is enabled, the backend registers and heartbeats through the local Unix socket:

- socket: `/run/hexe/supervisor.sock`
- register route: `POST /api/supervisor/runtimes/register`
- heartbeat route: `POST /api/supervisor/runtimes/heartbeat`

The registration metadata includes service entries for `backend`, `openwakeword`, and `frontend`. When `VOICE_TTS_PROVIDER=piper`, it also advertises a `piper_tts` service with its Docker container name, control-script path, and local synthesis URL. Core Supervisor can inspect and control managed containers through the node service proxy routes:

- `GET /api/services/status`
- `POST /api/services/start` with `{"target":"openwakeword"}`
- `POST /api/services/stop` with `{"target":"openwakeword"}`
- `POST /api/services/restart` with `{"target":"openwakeword"}`
- `POST /api/services/start` with `{"target":"piper_tts"}` when Piper TTS is enabled
- `POST /api/services/stop` with `{"target":"piper_tts"}` when Piper TTS is enabled
- `POST /api/services/restart` with `{"target":"piper_tts"}` when Piper TTS is enabled
- expected public node API: `http://10.0.0.100:9004`

Backend logs are written to `runtime/logs/hexevoice-backend.log`. The active file is archived at local midnight each day and retained for `BACKEND_LOG_BACKUP_DAYS` days, defaulting to 14. Set `BACKEND_LOG_LEVEL=DEBUG` in the backend environment when deeper voice transport, supervisor heartbeat, OTA, or service-control traces are needed.

Systemd templates for this node live at:

- `scripts/systemd/hexevoice-backend.service.in`
- `scripts/systemd/hexevoice-frontend.service.in`
