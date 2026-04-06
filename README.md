# HexeVoice

HexeVoice is a new modular Hexe node scaffolded from the shared Node standard starter.

It includes:

- a typed FastAPI backend entrypoint
- a modular backend package under `src/hexevoice/`
- a React + Vite operator UI under `frontend/`
- operational scripts under `scripts/`
- starter docs under `docs/`
- runtime state and logs under `runtime/`
- baseline tests under `tests/`

## Structure

- `src/hexevoice/`
  Modular backend package with config, runtime, onboarding, trust, core, provider, persistence, diagnostics, and security boundaries
- `frontend/`
  Starter UI for onboarding, readiness, providers, and diagnostics
- `scripts/`
  Environment-driven run and service-control scripts
  including `scripts/systemd/hexevoice-backend.service.in` and `scripts/systemd/hexevoice-frontend.service.in`
- `docs/`
  Starter architecture, feature spec, setup, operations, and provider notes
- `runtime/`
  Mutable runtime state and logs
- `tests/`
  Starter backend test suite

## Backend Start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m hexevoice.main
```

## Frontend Start

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 8080
```

## Notes

- This scaffold follows the Hexe node standards entrypoint at `/home/dan/Projects/Hexe/docs/standards/Node/README.md`.
- Backend contracts are still starter-level placeholders and should be replaced with real onboarding, Core, provider, and governance logic as HexeVoice is implemented.
