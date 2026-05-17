# Setup

## One-Line Install

Install or update HexeVoice at `~/hexe/HexeVoice` from the hosted repository:

```bash
curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | bash
```

The installer clones or updates the checkout, creates `.venv`, installs Python
requirements, installs frontend dependencies, builds the frontend, and creates
`scripts/stack.env` from the example when it is missing. By default it also
starts the temporary setup runner, opens the setup URL when the host can launch a
browser, and prepares the default install artifacts while setup is visible:
faster-whisper `base`, Piper `en_US-kathleen-low.onnx`, the `Hexe` wake model,
and configured firmware artifacts.

Set `HEXEVOICE_RUN_BOOTSTRAP=true` to run `scripts/bootstrap.sh` at the end:

```bash
curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_RUN_BOOTSTRAP=true bash
```

Optional overrides:

- `HEXEVOICE_INSTALL_ROOT=/path` changes the parent install directory.
- `HEXEVOICE_APP_DIR=/path/HexeVoice` changes the exact checkout path.
- `HEXEVOICE_REPO_URL=https://...` changes the Git remote.
- `HEXEVOICE_BRANCH=main` changes the branch.
- `HEXEVOICE_START_SETUP_RUNNER=false` skips the temporary setup UI/API.
- `HEXEVOICE_INSTALL_STATUS_UI=false` skips the early install-preparation page.
- `HEXEVOICE_INSTALL_STATUS_UI_PUBLIC_HOST=<host>` changes the hostname printed
  and opened for the early install page and setup runner handoff.
- `HEXEVOICE_INSTALL_STATUS_UI_OPEN_BROWSER=false` disables best-effort browser
  launch from the installer.
- `HEXEVOICE_INSTALL_STATUS_UI_TERMINAL_LINK=true` enables OSC-8 terminal
  hyperlinks for terminals that render them cleanly.
- `HEXEVOICE_INSTALL_QUIET=false` keeps installer command output visible after
  the preparation UI starts.
- `HEXEVOICE_INSTALL_LOG_PATH=/path/log` changes the quiet-mode install log.
- `HEXEVOICE_INSTALL_STATUS_UI_HANDOFF_DELAY_S=5` changes the preparation page
  redirect delay before it opens the real setup UI.
- `HEXEVOICE_SETUP_DEFAULT_ARTIFACTS=false` skips default artifact downloads.
- `HEXEVOICE_DEFAULT_STT_MODEL=base`, `HEXEVOICE_DEFAULT_PIPER_VOICE=en_US-kathleen-low`,
  and `HEXEVOICE_DEFAULT_WAKE_MODEL=Hexe` override default model choices.
- `HEXEVOICE_SETUP_HOST_ALIAS=true` adds optional local `/etc/hosts` aliases
  for `HexeVoice` and `HexeVoice.local` after checkout. The alias helper backs
  up the hosts file first and requires the explicit enable flag before writing.

Preview or apply the host alias manually:

```bash
./scripts/hostname-alias-control.sh dry-run
HEXEVOICE_ENABLE_HOST_ALIAS=true ./scripts/hostname-alias-control.sh install
```

## Uninstall / Partial Install Cleanup

Use `uninstall.sh` to clean up a failed or partial install. By default it stops
HexeVoice processes, removes generated user service files, removes HexeVoice
runtime containers, and leaves the checkout/runtime directory in place.

```bash
~/hexe/HexeVoice/uninstall.sh
```

To remove the checkout and runtime directory too:

```bash
~/hexe/HexeVoice/uninstall.sh --remove-app-dir
```

The uninstall script can also be run from the hosted repository:

```bash
curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/uninstall.sh | bash -s -- --remove-app-dir
```

Use `--yes` for non-interactive cleanup, `--dry-run` to preview actions, and
`--remove-host-alias` if the optional `HexeVoice` `/etc/hosts` alias was
installed.

## Temporary Setup Runner

Use `scripts/setup-runner.sh` when a fresh host should expose setup before
production services are ready. It starts the temporary backend on port `9100`
and the temporary UI on port `8180`, then watches
`http://<lan-host>:8084/setup/host` for the production UI. The temporary setup
page is `http://<lan-host>:8180/setup/host`. When production setup is healthy,
the temporary UI becomes a redirect for `120` seconds by default and the runner
exits.

The runner writes progress to `runtime/setup/bootstrap-status.json`. The
temporary or production backend exposes the same state at
`GET /api/setup/bootstrap/status`, including current action, completed actions,
pending downloads, retryable failures, lifecycle mode, and final redirect URL.
The first production setup page is available at `/setup/host`; `/setup` opens
that same host preparation page. It polls
`GET /api/setup/host-readiness` for host checks and uses targeted
`/api/setup/host-readiness/actions/<action>` calls for runtime directory prep,
CUDA preflight, host alias setup, Supervisor handoff, and saving the selected
new-node or migration setup mode.
Core connection and migration setup are split into `/setup/core` and
`/setup/migration`. `/api/setup/core` saves the Core URL even when Core is
temporarily offline and reports reachability as a warning. When Core is
reachable, it also reports Core identity/version metadata and route-support
probes for node registration, migration re-auth, Supervisor enrollment,
capability profiles, and governance endpoints. The response separates the
operator-entered LAN/public URL, Core API URL, Core UI URL, and exact tested
endpoints so hosts with UI on port 80 and API on port 9001 are understandable.
Unreachable Core URLs are saved with `validation_state=deferred` and
`recheck_required_before_trust=true`; the Core step stays put until re-check
passes before trust or capability setup continues.
The migration setup
routes wrap the node migration preflight/import APIs, automatically run a dry
preflight when a bundle is uploaded, show planned writes/errors before import,
show an import plan for onboarding data, endpoint registry, voice intents,
STT/TTS/wake provider settings, skipped secrets/tokens, runtime asset
expectations, and required Core re-auth, and continue to reject any bundle
containing trust tokens. The setup page exposes migration source choices for an
uploaded bundle, a future constrained local backup path loader, and a future
old-node/Core fetch path; upload remains the active source until those fetch
paths are explicitly supported.
After a successful migration import, setup routes migrated bundles with a
preserved node ID to `/setup/trust/reauth`; bundles without a previous node ID
continue through the normal onboarding/provider path.
New nodes continue through `/setup/trust` as new-node onboarding. Migrated nodes
continue through `/setup/trust/reauth`, which starts Core re-auth with
`POST /api/system/nodes/reauth/sessions`, opens the returned Core approval URL,
finalizes the session, and saves the fresh activation payload.
The frontend route guard keeps new-node setup out of migration re-auth and keeps
migration setup out of new-node onboarding unless no migrated node identity has
been imported yet.
The migration re-auth page shows explicit status flags for waiting, approved,
rejected, expired, trust finalized, node ID received, and ready-to-continue.
It also surfaces Step 4 blockers for required migrated re-auth, Core
unreachable, unsupported re-auth, rejected or expired sessions, missing node
identity, and local trust activation failure.
Step 4 recovery actions are available from the trust screens and
`POST /api/setup/trust/actions/{action}`. Supported actions restart onboarding,
reopen the Core approval URL, re-poll approval, retry trust finalization, clear
expired terminal sessions, and re-check Core onboarding/re-auth support.
When re-auth finalize returns an approved node identity, setup refreshes local
state and automatically advances to `/setup/providers`.
Provider/runtime setup is exposed at `/setup/providers` and backed by
`/api/setup/providers/status`, `/api/setup/providers/config`, and
`/api/setup/providers/apply`. The status endpoint reports provider selection,
runtime service health, provider states, and blockers so the UI can poll during
downloads, restarts, and health checks.
Step 5 includes STT, TTS, and wake configuration controls for model/profile,
CPU or CUDA mode, language, default Piper voice, wake model, threshold, preload
lists, and runtime socket/port/health details.
The Step 5 apply endpoint accepts `action: "download-models"` to download or
sync selected provider assets before containers are started: faster-whisper
downloads the default model plus configured warm models, Piper downloads the
default voice plus warm voices, and openWakeWord syncs the default Hexe model
plus any selected local wake models.
The provider status payload includes an `apply_plan` preview covering config
writes, model downloads, Docker/container changes, Supervisor registration,
health validation, and the persisted provider selections that will drive the
scripts.
Step 5 also exposes `cuda_profile` for faster-whisper. The profile records the
operator override (`auto`, `cpu`, `cuda`, or `skip`), the recommended CPU/CUDA
mode from the host hint, the selected Docker image family, and the
`cuda-preflight` validation action.
The provider status payload also includes `asset_progress` entries for selected
STT models, Piper voices, and wake models with missing, downloading, downloaded,
preloading, healthy, failed, and retry states reflected in the setup UI.
Step 5 exposes Supervisor runtime registration status and can call
`POST /api/setup/supervisor/register-runtime` after the trusted node ID exists,
covering backend, frontend, STT, TTS, wake, and provider Docker services.
Step 5 continue is blocked when enabled providers are unhealthy, selected
models/voices/wake assets are missing, forced CUDA is unavailable, required
provider configs are not persisted, or Supervisor registration reports a local
failure.
Step 5 recovery actions can download selected assets, preload selected models,
restart providers, recreate provider containers, rebuild provider env/config,
switch faster-whisper between CPU and CUDA profiles, and re-register runtime
services with Supervisor.
Capability and governance setup is exposed at `/setup/capabilities` and backed
by `/api/setup/capabilities/status`,
`/api/setup/capabilities/selection`,
`/api/setup/capabilities/declare`, and
`/api/setup/capabilities/sync-governance`. The setup status remains blocked
until selected capabilities are current in Core and a governance bundle has been
refreshed locally.
The Step 6 status payload includes a manifest preview before declaration. The
preview contains the Core declaration payload, node identity, enabled providers,
provider models/configs, selected capabilities, runtime URLs or sockets, budget
metadata, and current governance metadata so the operator can inspect the exact
shape before declaring to Core.
The final setup gate is `/setup/ready`, backed by
`/api/setup/ready/status`, `/api/setup/ready/run-smoke-test`, and
`/api/setup/ready/complete`. The smoke test checks backend/frontend reachability,
trust, governance, providers, firmware, runtime directories, sockets, LAN URLs,
host alias state, and Core node visibility before setup can be completed. While
setup is incomplete, the frontend redirects `8084/` into the current `/setup/*`
route; after completion, `8084/` stays on the dashboard/fallback surface.

```bash
./scripts/setup-runner.sh --handoff none
./scripts/setup-runner.sh --handoff systemd
./scripts/setup-runner.sh --handoff existing-supervisor
```

Joined or standalone Supervisor handoff is available when the Core Supervisor
installer is present. If Core or the installer is unavailable, the runner keeps
the temporary setup UI/API active instead of blocking the install.

```bash
CORE_SUPERVISOR_URL=http://10.0.0.100:9001 \
CORE_SUPERVISOR_ENROLLMENT_TOKEN=<one-time-token> \
./scripts/setup-runner.sh --handoff joined-supervisor
```

## Manual Setup

1. Create the repo-local virtual environment with `python3 -m venv .venv`.
2. Install backend requirements with `.venv/bin/pip install -r requirements.txt`.
3. Install frontend dependencies from `frontend/` with `npm install`.
4. Copy `scripts/stack.env.example` to `scripts/stack.env`.
5. Update backend and frontend commands if needed, keeping Python commands on `.venv/bin/...`.
6. Keep `HEXE_SUPERVISOR_ENABLED=true`, `HEXE_SUPERVISOR_API_TRANSPORT=socket`, and `HEXE_SUPERVISOR_API_SOCKET=/run/hexe/supervisor.sock` when this node should register with Core Supervisor.
7. Start the backend with `API_HOST=0.0.0.0 API_PORT=9004 PYTHONPATH=src .venv/bin/python -m hexevoice.main`.
8. Build the frontend with `scripts/rebuild-ui.sh`, then start it from `frontend/` with `VITE_PROXY_TARGET=http://127.0.0.1:9004 npm run preview -- --host 0.0.0.0 --port 8084`.
9. Run backend tests with `PYTHONPATH=src .venv/bin/pytest`.
10. Run the frontend production validation with `cd frontend && npm run build`.

## Operator Flow

HexeVoice now presents the install/setup/migration flow in the frontend:

1. Host Preparation
2. Core Connection
3. Migration Import
4. Trust Authorization
5. Provider Setup
6. Capabilities & Governance
7. Ready Check

The onboarding card remains available as a deeper legacy/canonical lifecycle
surface when needed, but the main setup shell leads with the install and
migration path.
The right-side overview panels are the post-setup/operator summary surface.

After trust activation, HexeVoice should register its runtime with Core Supervisor over `/run/hexe/supervisor.sock` and continue sending Supervisor heartbeats while running.

## Verification Commands

Backend:

```bash
PYTHONPATH=src .venv/bin/pytest -q
```

Frontend:

```bash
cd frontend
npm install
npm run build
```
