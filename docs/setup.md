# Setup

1. Create the repo-local virtual environment with `python3 -m venv .venv`.
2. Install backend requirements with `.venv/bin/pip install -r requirements.txt`.
3. Install frontend dependencies from `frontend/` with `npm install`.
4. Copy `scripts/stack.env.example` to `scripts/stack.env`.
5. Update backend and frontend commands if needed, keeping Python commands on `.venv/bin/...`.
6. Start the backend with `PYTHONPATH=src .venv/bin/python -m hexevoice.main`.
7. Start the frontend from `frontend/` with `npm run dev -- --host 0.0.0.0 --port 8080`.
8. Run backend tests with `PYTHONPATH=src .venv/bin/pytest`.
9. Run the frontend production validation with `cd frontend && npm run build`.

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
