# Operations

## Operator Surfaces

HexeVoice now uses one shared operator shell with two distinct concerns:

- onboarding setup card:
  the canonical 10-step setup flow driven by local onboarding status and Core-backed readiness actions
- operational overview cards:
  post-setup summaries for readiness, provider configuration, registered voice intents, governance, and diagnostics

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

The post-trust setup card remains available after setup completion. Operators can return to it to adjust provider selection, select/redeclare capabilities, and enable/disable registered Voice Node intents. The operational dashboard also exposes an Intents section for inspecting the current registry as compact intent cards, opening a full contract detail popup, and dry-running utterances against registered intent dispatch. The Providers section shows Piper models as compact cards with detail popups; operators can mark a model warm from the popup and then save the TTS settings.

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
- `scripts/run-from-env.sh stt` to launch the external faster-whisper STT service from `scripts/stack.env`
- `scripts/run-from-env.sh frontend` to launch the frontend from `scripts/stack.env`
- `scripts/stack-control.sh` for service control
- `scripts/restart-stack.sh` to restart the configured stack services

The external faster-whisper STT runtime code lives in the standalone
`src/stt/` package. `src/hexevoice/stt_service.py` remains as a compatibility
entrypoint, while service launch commands use `python -m stt.service`.

Systemd user units are intentionally not enabled for auto-start and do not declare a restart policy. Core Supervisor is the lifecycle authority for managed node runtime behavior.

When supervisor integration is enabled, the backend registers and heartbeats through the local Unix socket:

- socket: `/run/hexe/supervisor.sock`
- register route: `POST /api/supervisor/runtimes/register`
- heartbeat route: `POST /api/supervisor/runtimes/heartbeat`

The registration metadata includes service entries for `backend`, `openwakeword`, and `frontend`. When `VOICE_STT_PROVIDER=external_faster_whisper`, it also advertises a `faster_whisper_stt` user service with its control-script path and local STT URL. When `VOICE_TTS_PROVIDER=piper`, it also advertises a `piper_tts` service with its Docker container name, control-script path, and local synthesis URL. Core Supervisor can inspect and control managed services through the node service proxy routes:

- `GET /api/services/status`
- `POST /api/services/start` with `{"target":"openwakeword"}`
- `POST /api/services/stop` with `{"target":"openwakeword"}`
- `POST /api/services/restart` with `{"target":"openwakeword"}`
- `POST /api/services/install` with `{"target":"stt"}` or `{"target":"faster_whisper_stt"}` when external faster-whisper STT is enabled
- `POST /api/services/start` with `{"target":"piper_tts"}` when Piper TTS is enabled
- `POST /api/services/stop` with `{"target":"piper_tts"}` when Piper TTS is enabled
- `POST /api/services/restart` with `{"target":"piper_tts"}` when Piper TTS is enabled
- `POST /api/services/restart` with `{"target":"stt"}` or `{"target":"faster_whisper_stt"}` when external faster-whisper STT is enabled

The external faster-whisper STT service is installed through Supervisor rather
than by ad hoc stack restarts. Its Supervisor registration includes the user
systemd service name, the `scripts/systemd/hexevoice-stt.service.in` unit
template, the `scripts/stack.env` environment file, and `install_action:
install`. Supervisor can invoke the node service proxy install action to render
the user unit and run `systemctl --user daemon-reload` before starting or
restarting STT.

After pulling the `src/stt/` package split, existing STT units only need the
updated `scripts/stack.env` command and a restart because the unit evaluates
`STT_CMD` at runtime. Running
`./scripts/faster-whisper-stt-control.sh install` is still safe when operators
want Supervisor or the local control script to re-render the user unit.

The local `scripts/stack-control.sh` helper will skip the external STT unit when
it is not installed yet, instead of installing it itself. This keeps Supervisor
as the install owner while still letting operators restart the already-installed
backend and frontend user services.

When Piper TTS runtime settings are saved, the provider page marks them as
restart-required. A successful `tts` or `piper_tts` restart through the service
proxy clears that flag and records `restart_applied_at` in the runtime settings.

`GET /api/services/status` also exposes runtime page metadata for the operator
UI: Backend, STT, and TTS component health, per-component CPU/memory usage where
the runtime can observe it, supervisor registration status, and whether a
component has a supported restart target. In-process STT reports backend-process
resource usage. External faster-whisper STT reports its managed user-service
process resource usage when enabled, and Piper TTS reports Docker container
usage when enabled.

The Voice Endpoint runtime page shows endpoint status as summary cards. Selecting
an endpoint opens a blurred-background detail popup with the full registry,
voice-state, latency, session, and raw debug payload for that endpoint.
Endpoint status also includes firmware comparison metadata. The backend infers
the endpoint board profile, checks the matching `runtime/firmware/manifest-*.json`
and binary artifact, and the endpoint detail popup exposes `Send OTA` when the
reported endpoint firmware version differs from the latest exported artifact.
- expected public node API: `http://10.0.0.100:9004`

Backend logs are written to `runtime/logs/hexevoice-backend.log`. The active file is archived at local midnight each day and retained for `BACKEND_LOG_BACKUP_DAYS` days, defaulting to 14. Set `BACKEND_LOG_LEVEL=DEBUG` in the backend environment when deeper voice transport, supervisor heartbeat, OTA, or service-control traces are needed.

Systemd templates for this node live at:

- `scripts/systemd/hexevoice-backend.service.in`
- `scripts/systemd/hexevoice-stt.service.in`
- `scripts/systemd/hexevoice-frontend.service.in`

The backend unit sets `LimitNOFILE=65536` so long-running voice WebSocket, endpoint heartbeat, and supervised wake-word socket activity does not inherit the low shell default of 1024 file descriptors.

Supervisor API calls default to an 8 second timeout because Core Supervisor may sample Docker resource usage while registering the node runtime.
