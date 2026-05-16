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

## Temporary Setup Runner

Use `scripts/setup-runner.sh` when a fresh host should expose setup before
production services are ready. It starts the temporary backend on port `9100`
and the temporary UI on port `8180`, then watches
`http://<lan-host>:8084/setup` for the production UI. When production setup is
healthy, the temporary UI becomes a redirect for `120` seconds by default and
the runner exits.

The runner writes progress to `runtime/setup/bootstrap-status.json`. The
temporary or production backend exposes the same state at
`GET /api/setup/bootstrap/status`, including current action, completed actions,
pending downloads, retryable failures, lifecycle mode, and final redirect URL.
The first production setup page is available at `/setup/host`; it polls
`GET /api/setup/host-readiness` for host checks and uses targeted
`/api/setup/host-readiness/actions/<action>` calls for runtime directory prep,
CUDA preflight, host alias setup, Supervisor handoff, and saving the selected
new-node or migration setup mode.
Core connection and migration setup are split into `/setup/core` and
`/setup/migration`. `/api/setup/core` saves the Core URL even when Core is
temporarily offline and reports reachability as a warning. The migration setup
routes wrap the node migration preflight/import APIs and continue to reject any
bundle containing trust tokens.
Migrated nodes continue through `/setup/trust/reauth`, which starts Core
re-auth with `POST /api/system/nodes/reauth/sessions`, opens the returned Core
approval URL, finalizes the session, and saves the fresh activation payload.

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

HexeVoice now presents the full canonical 10-step onboarding flow in the frontend:

1. Node Identity
2. Core Connection
3. Bootstrap Discovery
4. Registration
5. Approval
6. Trust Activation
7. Provider Setup
8. Capability Declaration
9. Governance Sync
10. Ready

The onboarding card is the setup surface.
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
