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
when present, STT provider settings when present, runtime TTS settings when
present, TTS provider settings when present, and wake word provider settings
when present. Trust tokens are included only when the operator leaves **Include
trust secrets** enabled; without those tokens, the imported node must be
reactivated with Core.

Import validates the bundle, writes the supported local runtime state files, and
can rewrite destination-specific `api_base_url`, `ui_endpoint`, `core_base_url`,
and hostname values for the new machine. Large local artifacts are intentionally
not bundled. STT migration preserves model names, preload intent, device and
compute choices, and safe service/runtime options, but model binaries must be
downloaded or copied separately on the destination host. TTS migration preserves
the selected provider, Piper default voice/model, warm voice intent, endpoint
voice/sample-rate mappings, conversion policy, and safe Piper runtime metadata;
Piper model binaries and generated audio artifacts are not bundled. Wake
migration preserves provider choice, threshold/timing settings, selected wake
models, default wake word, model download/preload intent, service metadata, and
recording retention settings; the trained wake model binaries are not bundled,
and `Hexa` wake-word names are normalized to `Hexe` in migration settings. Copy
Piper models, wake models, firmware artifacts, endpoint media, service env files,
logs, and retained audio/history separately when those are needed.

Fresh installs can import a migration bundle from the first setup page before
starting onboarding. Enter the destination Core base URL, choose the migration
JSON bundle, confirm the destination API/UI URLs, and import. The later
operational Migration page remains available for exporting bundles and for
post-setup imports.

Before importing on a new host, run migration preflight. It validates the bundle,
reports the files/settings that would be written, checks Docker/Compose, Python,
npm, runtime directory/disk readiness, firmware directory presence, and can
optionally check Core reachability:

```bash
./scripts/migration-preflight.py migration-bundle.json \
  --core-url http://10.0.0.100:9001 \
  --api-url http://10.0.0.55:9004 \
  --ui-url http://10.0.0.55:8084

./scripts/migration-preflight.py migration-bundle.json --check-core
```

The import API also accepts `dry_run=true`; it validates destination overrides
and returns planned writes plus warnings without changing runtime state.

After install or migration, run the smoke test to prove the node is usable. It
checks backend status, frontend reachability, STT/TTS/wake runtime health,
runtime directories, and optional Docker visibility:

```bash
./scripts/post-install-smoke-test.py
./scripts/post-install-smoke-test.py --json
./scripts/post-install-smoke-test.py --backend-url http://127.0.0.1:9004 --frontend-url http://127.0.0.1:8084/
```

The command is read-only except for normal health probes and returns a non-zero
exit code when any required check fails.

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
- `scripts/restart-stack.sh` to restart the configured stack services
- `scripts/faster-whisper-stt-control.sh ready` to build/start the local STT
  container, wait for `/health` over `runtime/sockets/stt.sock`, and
  preload/download the configured model

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

STT model profiles can be selected with `VOICE_STT_PROFILE` or provider setup.
When no profile is selected, the existing single-model
`VOICE_STT_FASTER_WHISPER_*` settings remain the active custom profile.

Recommended profiles:

- `cpu_default`: `base.en`, CPU, `int8`, preload/download friendly for first
  installs.
- `cuda_fast_intent`: `small.en`, CUDA, `float16`, beam/best-of 1 for a fast
  intent-first pass.
- `cuda_accurate_fallback`: `medium.en`, CUDA, `float16`, beam/best-of 5 for a
  slower but more accurate fallback.

`cuda_fast_intent` declares `cuda_accurate_fallback` as its fallback profile.
The fallback decision helper triggers on empty transcripts, low confidence, STT
errors, or an unmatched intent. The current runtime reports the active and
fallback profile in STT status; full second-pass fallback execution can be
tuned after real CUDA-host benchmarks.

The hosted installer can run the same STT readiness path with
`HEXEVOICE_SETUP_STT=true`. This may download the configured faster-whisper
model, so the default installer leaves it as an explicit opt-in:

```bash
HEXEVOICE_SETUP_STT=true ./install.sh
./scripts/faster-whisper-stt-control.sh doctor
./scripts/faster-whisper-stt-control.sh health
```

The STT control script reads saved `external_faster_whisper` provider setup from
the onboarding state before `start`, `restart`, `ready`, or `build`. This keeps a
manual Docker restart aligned with the model selected in setup, even when
`scripts/stack.env` still contains the bootstrap CPU-safe default. Backend
service actions also schedule a provider-config reconcile after STT
install/start/restart so the running engine is corrected after it becomes
healthy.

During STT setup, `scripts/faster-whisper-stt-control.sh` auto-detects whether
the host can run the CUDA STT Docker profile. In `STT_CUDA_MODE=auto`, it first
runs a Docker GPU smoke check with `STT_CUDA_SMOKE_IMAGE` and then verifies the
selected CUDA STT image can import faster-whisper/CTranslate2 and report CUDA
compute support. When either check fails, setup keeps the CPU image/profile
without failing the install.

Before migrating to a CUDA-capable host, run the structured preflight. It checks
Docker, Docker Compose, host `nvidia-smi`, Docker GPU passthrough, the CUDA STT
image's faster-whisper/CTranslate2 CUDA support, and the configured model/device
fields. On non-GPU hosts it exits successfully in auto mode and reports
`selected_profile=cpu`.

Useful overrides:

```bash
./scripts/faster-whisper-stt-control.sh cuda-preflight
STT_CUDA_MODE=cpu ./scripts/faster-whisper-stt-control.sh ready   # force CPU
STT_CUDA_MODE=cuda ./scripts/faster-whisper-stt-control.sh ready  # require CUDA checks to pass
STT_CUDA_MODE=skip ./scripts/faster-whisper-stt-control.sh ready  # skip detection and use CPU
STT_CUDA_IMAGE=registry.example/hexevoice/faster-whisper-stt:cuda ./scripts/faster-whisper-stt-control.sh ready
```

For a comparable STT timing baseline, use the faster-whisper benchmark in
faster-only mode. `--generate-fixture` creates a tiny deterministic WAV when no
wake recordings are available; recorded command clips remain better for real
accuracy comparison.

```bash
.venv/bin/python scripts/benchmark-stt.py --faster-only --generate-fixture \
  --model base.en --device cpu --faster-compute-type int8 --repeat 3 \
  --json-output runtime/stt/cpu-benchmark.json
```

STT input trimming is enabled by default for provider-backed STT and runs before
audio is sent to OpenAI, local faster-whisper, or the external faster-whisper
container. It only trims raw `pcm_s16le` audio and keeps conservative leading
and trailing padding so wake tails and end-of-speech padding are removed without
cutting normal commands. Tune it with:

```bash
VOICE_STT_SILENCE_TRIM_ENABLED=true
VOICE_STT_SILENCE_TRIM_THRESHOLD=180
VOICE_STT_SILENCE_TRIM_LEADING_PADDING_MS=160
VOICE_STT_SILENCE_TRIM_TRAILING_PADDING_MS=500
VOICE_STT_SILENCE_TRIM_MIN_AUDIO_MS=350
```

Each transcript status/timing payload includes `silence_trim_*` fields when
trimming runs. Before moving to a larger CUDA model, compare wake recording or
micro-VAD debug clips before and after tuning, and watch for command cutoff,
filler-only transcripts, reduced STT input duration, and total STT latency.

When the STT service URL is not the default `http://127.0.0.1:10300`, set
`STT_HEALTH_URL` or `VOICE_STT_SERVICE_BASE_URL` for the control script. The
normal local runtime uses Unix sockets instead: `VOICE_STT_SERVICE_TRANSPORT=unix`
with `VOICE_STT_SERVICE_SOCKET=runtime/sockets/stt.sock`, and
`VOICE_TTS_PIPER_TRANSPORT=unix` with
`VOICE_TTS_PIPER_SOCKET=runtime/sockets/tts.sock`. TCP remains available only
when those transports are explicitly set to `tcp` or a base URL override is set
for development/debugging.

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

STT and TTS images include `python -m hexevoice.engine_health_ping`, a small
container helper that can post engine identity, hostname, config summary, health
state, and last error to `/api/engines/heartbeat` through either
`HEXEVOICE_NODE_HEALTH_SOCKET` or `HEXEVOICE_NODE_HEALTH_URL`. The wake-word
container stays on Wyoming/container health checks for now because its protocol
is not an HTTP voice engine API.

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
the `faster_whisper_stt` implementation service id, Docker container name,
control-script path, socket path, and local STT URL. When
`VOICE_TTS_PROVIDER=piper`, `tts_engine` includes the
`piper_tts` implementation service id, Docker container name, control-script
path, socket path, and local synthesis URL. Each service entry includes a `process` block
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

The external faster-whisper STT service is now installed through the
HexeVoice-owned Docker Compose control script. The `install` action builds the
image, and `ready` builds/starts the container, waits on the Unix-socket
`/health` endpoint, and preloads the configured model.

When Piper TTS runtime settings are saved, the provider page marks them as
restart-required. A successful `tts` or `piper_tts` restart through the service
proxy clears that flag and records `restart_applied_at` in the runtime settings.

`GET /api/services/status` also exposes runtime page metadata for the operator
UI: Backend, STT, and TTS component health, per-component CPU/memory usage where
the runtime can observe it, supervisor registration status, and whether a
component has a supported restart target. In-process STT reports backend-process
resource usage. External faster-whisper STT and Piper TTS report Docker
container usage when enabled. The same status payload includes process IDs for
monitored services where available.

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
