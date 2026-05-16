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

The setup card also exposes **Refresh Core metadata** after trust activation. Use it when Core has a stale Voice Node `api_base_url` or UI endpoint; the backend requires `CORE_ADMIN_TOKEN` or Core's legacy `SYNTHIA_ADMIN_TOKEN` and patches Core's node registration metadata refresh route.

## Node Migration

The operational dashboard includes a Migration section for moving a Voice Node
identity to a different machine. Export creates a JSON bundle containing local
onboarding/trust state, endpoint registry metadata, registered voice intents
when present, and runtime TTS settings when present. Trust tokens are included
only when the operator leaves **Include trust secrets** enabled; without those
tokens, the imported node must be reactivated with Core.

Import validates the bundle, writes the supported local runtime state files, and
can rewrite destination-specific `api_base_url`, `ui_endpoint`, `core_base_url`,
and hostname values for the new machine. Large local artifacts are intentionally
not bundled. Copy Piper models, firmware artifacts, endpoint media, service env
files, logs, and retained audio/history separately when those are needed.

Fresh installs can import a migration bundle from the first setup page before
starting onboarding. Enter the destination Core base URL, choose the migration
JSON bundle, confirm the destination API/UI URLs, and import. The later
operational Migration page remains available for exporting bundles and for
post-setup imports.

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
- `scripts/faster-whisper-stt-control.sh ready` to render the STT user unit,
  restart it, wait for `/health`, and preload/download the configured model

`scripts/stack-control.sh` restarts services one at a time and prints each
service as it is handled. Each `systemctl --user` operation is bounded by
`STACK_CONTROL_TIMEOUT_S`, which defaults to 45 seconds. Override it when a
machine needs a longer model unload/load window, for example
`STACK_CONTROL_TIMEOUT_S=90 scripts/restart-stack.sh`.

Hosted install runs `scripts/prepare-runtime-dirs.sh` to create the runtime
directory skeleton idempotently. Run it directly after manual checkouts or after
changing `RUNTIME_DIR`:

```bash
./scripts/prepare-runtime-dirs.sh
```

The helper creates empty scaffolding for endpoint media, firmware artifacts,
logs, migration backups, micro-VAD debug chunks, openWakeWord models, Piper
models, rendered node UI pages, local sockets, STT cache/model files, generated
TTS artifacts, and wake recordings. It does not download model binaries,
firmware binaries, migrated state, logs, or generated audio; those are populated
by their specific install, download, import, or runtime paths.

Firmware OTA artifacts can be populated during hosted install with
`HEXEVOICE_SETUP_FIRMWARE=true`, or later with the firmware artifact control
script. The source is intentionally configurable so firmware can move to a
separate repository or release feed:

```bash
HEXEVOICE_SETUP_FIRMWARE=true \
HEXEVOICE_FIRMWARE_SOURCE_DIR=/path/to/exported/runtime/firmware \
./install.sh

HEXEVOICE_FIRMWARE_ARTIFACT_BASE_URL=https://downloads.example.com/hexe/firmware/latest \
./scripts/firmware-artifacts-control.sh download

HEXEVOICE_FIRMWARE_REPO_URL=https://github.com/example/HexeFirmware.git \
HEXEVOICE_FIRMWARE_REF=main \
HEXEVOICE_FIRMWARE_REPO_ARTIFACT_DIR=runtime/firmware \
./scripts/firmware-artifacts-control.sh download

HEXEVOICE_FIRMWARE_GITHUB_REPOSITORY=example/HexeFirmware \
HEXEVOICE_FIRMWARE_SOURCE=github-release \
HEXEVOICE_FIRMWARE_RELEASE_TAG=latest \
./scripts/firmware-artifacts-control.sh download
```

The downloader writes artifacts atomically into `runtime/firmware`, validates
`SHA256SUMS` when present, and checks for the configured board profiles
(`HEXEVOICE_FIRMWARE_REQUIRED_PROFILES`, default `esp_box_3,ha_voice_pe`).
`HEXEVOICE_FIRMWARE_RELEASE_URL` is accepted as an alias for an asset base URL,
and `HEXEVOICE_FIRMWARE_ARTIFACTS` can override the exact filenames to fetch
when a release adds another board profile.
For offline installs, copy `hexe_firmware*.bin`, `manifest*.json`, and
`SHA256SUMS` into a local directory and point `HEXEVOICE_FIRMWARE_SOURCE_DIR`
at it. Use `./scripts/firmware-artifacts-control.sh verify` after manual copies.

The external faster-whisper STT runtime code lives in the standalone
`src/stt/` package. `src/hexevoice/stt_service.py` remains as a compatibility
entrypoint, while service launch commands use `python -m stt.service`.
The runtime accepts faster-whisper tuning via `VOICE_STT_FASTER_WHISPER_LANGUAGE`,
`VOICE_STT_FASTER_WHISPER_BEAM_SIZE`, `VOICE_STT_FASTER_WHISPER_BEST_OF`,
`VOICE_STT_FASTER_WHISPER_WITHOUT_TIMESTAMPS`,
`VOICE_STT_FASTER_WHISPER_WORD_TIMESTAMPS`, and
`VOICE_STT_FASTER_WHISPER_MAX_INITIAL_TIMESTAMP`.
STT transcription responses include `timing_breakdown_ms` with audio
preparation, model transcribe call, decoding, post-processing, and total
durations for latency debugging.
Provider setup can select the external faster-whisper default model, extra
models to download/preload, device, and compute type. The safe default is
`device=cpu` with `compute_type=int8`. GPU mode uses `device=cuda` and normally
`compute_type=float16`, but it requires compatible NVIDIA drivers, CUDA/cuDNN
runtime libraries, and a CTranslate2/faster-whisper install that can use CUDA.
Runtime service status also exposes external STT `warm_model_health`, including
loaded state, loaded-at timestamp, load count, last load duration, and
`reload_required` when the running STT service does not match the backend's
expected model/device/compute/transcribe options.

The hosted installer can run the same STT readiness path with
`HEXEVOICE_SETUP_STT=true`. This may download the configured faster-whisper
model, so the default installer leaves it as an explicit opt-in:

```bash
HEXEVOICE_SETUP_STT=true ./install.sh
./scripts/faster-whisper-stt-control.sh doctor
./scripts/faster-whisper-stt-control.sh health
```

When the STT service URL is not the default `http://127.0.0.1:10300`, set
`STT_HEALTH_URL` or `VOICE_STT_SERVICE_BASE_URL` for the control script.

The Piper TTS runtime code lives in the standalone `src/tts/` package.
`services/piper_tts/app.py` remains as a compatibility wrapper, while the
container launches `tts.service:app` directly.
Hosted install can run the Piper readiness path with `HEXEVOICE_SETUP_TTS=true`.
This downloads the configured Piper voices when they are missing, builds/starts
the container, waits for `/health`, and applies the runtime warm-voice config:

```bash
HEXEVOICE_SETUP_TTS=true ./install.sh
./scripts/piper-tts-control.sh ready
./scripts/piper-tts-control.sh doctor
./scripts/piper-tts-control.sh health
```

By default the downloader uses the `rhasspy/piper-voices` Hugging Face repo at
the `v1.0.0` tag and derives the model path from Piper voice ids such as
`en_US-lessac-medium`. Set `PIPER_TTS_DOWNLOAD_VOICES` to a comma-separated
voice list and `PIPER_TTS_VOICE_REPO_URL` to use a mirror or different source.

Hosted install can also bring up the supervised openWakeWord runtime with
`HEXEVOICE_SETUP_WAKE=true`. This syncs the default Hexe model into
`runtime/openwakeword/models`, starts the Docker container, and waits for the
Wyoming TCP port to accept connections:

```bash
HEXEVOICE_SETUP_WAKE=true ./install.sh
./scripts/openwakeword-control.sh ready
./scripts/openwakeword-control.sh doctor
./scripts/openwakeword-control.sh health
```

Systemd user units are intentionally not enabled for auto-start and do not declare a restart policy. Core Supervisor is the lifecycle authority for managed node runtime behavior.

When supervisor integration is enabled, the backend registers and heartbeats through the local Unix socket:

- socket: `/run/hexe/supervisor.sock`
- register route: `POST /api/supervisor/runtimes/register`
- heartbeat route: `POST /api/supervisor/runtimes/heartbeat`

The registration metadata includes stable service entries for `backend`,
`openwakeword`, `stt_engine`, `tts_engine`, and `frontend`. The STT and TTS
entries are logical engine wrappers, so Supervisor sees the same service IDs
whether the active implementation is local, external, in-process, or cloud
backed. When `VOICE_STT_PROVIDER=external_faster_whisper`, `stt_engine` includes
the `faster_whisper_stt` implementation service id, control-script path, and
local STT URL. When `VOICE_TTS_PROVIDER=piper`, `tts_engine` includes the
`piper_tts` implementation service id, Docker container name, control-script
path, and local synthesis URL. Each service entry includes a `process` block
when the node can resolve one, with `pid`, `main_pid`, and runtime resource
fields so Core Supervisor can monitor the actual backend process, Docker
container init PID, or managed user-service process. The STT/TTS engine entries
also include `implementation_health` with the active implementation, provider,
model, health state, configured state, and last error. Core Supervisor can
inspect and control managed services through the node service proxy routes:

- `GET /api/services/status`
- `POST /api/services/restart` with `{"target":"backend"}` to queue a user-service backend restart
- `POST /api/services/start` with `{"target":"openwakeword"}`
- `POST /api/services/stop` with `{"target":"openwakeword"}`
- `POST /api/services/restart` with `{"target":"openwakeword"}`
- `POST /api/services/install` with `{"target":"stt"}` or `{"target":"faster_whisper_stt"}` when external faster-whisper STT is enabled
- `POST /api/services/start` with `{"target":"piper_tts"}` when Piper TTS is enabled
- `POST /api/services/stop` with `{"target":"piper_tts"}` when Piper TTS is enabled
- `POST /api/services/restart` with `{"target":"piper_tts"}` when Piper TTS is enabled
- `POST /api/services/restart` with `{"target":"stt"}`, `{"target":"stt_engine"}`, or `{"target":"faster_whisper_stt"}` when external faster-whisper STT is enabled
- `POST /api/services/restart` with `{"target":"tts"}`, `{"target":"tts_engine"}`, or `{"target":"piper_tts"}` when Piper TTS is enabled

The external faster-whisper STT service is installed through Supervisor rather
than by ad hoc stack restarts. The `stt_engine` metadata includes the user
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
usage when enabled. The same status payload includes process IDs for monitored
services where available.

The Voice Endpoint runtime page shows endpoint status as summary cards. Selecting
an endpoint opens a blurred-background detail popup with the full registry,
voice-state, latency, session, and raw debug payload for that endpoint.

Registered short utterance intents such as `yes`, `no`, `stop`, `ok`, and
`okay` are treated as follow-up-scoped by default so they do not accidentally
fire as global commands. A registered intent can explicitly opt into global
matching with `constraints.short_intent_scope: "global"` when that behavior is
intentional.
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
