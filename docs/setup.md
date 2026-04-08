# Setup

1. Create the repo-local virtual environment with `python3 -m venv .venv`.
2. Install backend requirements with `.venv/bin/pip install -r requirements.txt`.
3. Copy `scripts/stack.env.example` to `scripts/stack.env`.
4. Update backend and frontend commands if needed, keeping Python commands on `.venv/bin/...`.
5. Start the backend with `PYTHONPATH=src .venv/bin/python -m hexevoice.main`.
6. Run backend tests with `PYTHONPATH=src .venv/bin/pytest`.
7. Start the frontend from `frontend/` with `npm run dev -- --host 0.0.0.0 --port 8080`.
