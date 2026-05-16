# Setup

## One-Line Install

Install or update HexeVoice at `~/hexe/HexeVoice` from the hosted repository:

```bash
curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | bash
```

The installer clones or updates the checkout, creates `.venv`, installs Python
requirements, installs frontend dependencies, builds the frontend, and creates
`scripts/stack.env` from the example when it is missing.

Set `HEXEVOICE_RUN_BOOTSTRAP=true` to run `scripts/bootstrap.sh` at the end:

```bash
curl -fsSL https://raw.githubusercontent.com/danhajduk/HexeVoice/main/install.sh | HEXEVOICE_RUN_BOOTSTRAP=true bash
```

Optional overrides:

- `HEXEVOICE_INSTALL_ROOT=/path` changes the parent install directory.
- `HEXEVOICE_APP_DIR=/path/HexeVoice` changes the exact checkout path.
- `HEXEVOICE_REPO_URL=https://...` changes the Git remote.
- `HEXEVOICE_BRANCH=main` changes the branch.

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
