# HexeVoice

HexeVoice is a modular Hexe voice node built from the shared Node standard starter.

It includes:

- a typed FastAPI backend entrypoint
- a modular backend package under `src/hexevoice/`
- a React + Vite operator UI under `frontend/`
- a native ESP-IDF firmware track under `firmware/`
- operational scripts under `scripts/`
- starter docs under `docs/`
- runtime state and logs under `runtime/`
- baseline tests under `tests/`

## Structure

- `src/hexevoice/`
  Modular backend package with config, runtime, onboarding, trust, core, provider, persistence, diagnostics, and security boundaries
- `frontend/`
  Starter UI for onboarding, readiness, providers, and diagnostics
- `firmware/`
  Native Hexe standalone firmware for the ESP32-S3 Box track
- `scripts/`
  Environment-driven run and service-control scripts
  including `scripts/systemd/hexevoice-backend.service.in`, `scripts/systemd/hexevoice-stt.service.in`, and `scripts/systemd/hexevoice-frontend.service.in`
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
npm run dev -- --host 0.0.0.0 --port 8084
```

## Start Both Together

To launch both services together for local development on your requested ports:

```bash
./scripts/start-dev-stack.sh
```

This starts:

- API on `9004`
- UI on `8084`

You can override the defaults with `BACKEND_PORT`, `FRONTEND_PORT`, `BACKEND_HOST`, or `FRONTEND_HOST` if needed.

## Notes

- This scaffold follows the Hexe node standards entrypoint at `/home/dan/Projects/Hexe/docs/standards/Node/README.md`.
- Node onboarding is implemented end to end across the backend and frontend shell.
- HexeVoice registers and heartbeats to the Core Supervisor over `/run/hexe/supervisor.sock` when `HEXE_SUPERVISOR_ENABLED=true`.
- The operator UI follows the shared Hexe node visual standard and renders the full canonical 10-step flow plus post-setup operational overview surfaces.
- Local Python workflow should always use the repo-local `.venv` binaries.

## Implemented Flow

HexeVoice now implements the canonical Core node setup progression:

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

The backend persists onboarding, trust, provider, capability, governance, and operational-status state under `runtime/onboarding_state.json` by default. The frontend consumes those local APIs plus the Core-backed readiness projections to drive the setup and operational overview surfaces.

## Device Conversation Stub

There is now a minimal local device conversation route at `POST /api/assistant/turn`.

This is meant as a lightweight firmware target while the full wake/STT/TTS pipeline is still under construction.

Example request:

```json
{
  "endpoint_id": "esp-box-1",
  "text": "status"
}
```

The route currently supports simple local commands like `status`, `repeat`, and `stop`, and otherwise returns a deterministic fallback reply so the device can validate request/response flow end to end.

## Current Voice Boundary

Implemented today:

- Core onboarding, trust activation, provider setup, capability declaration, governance sync, and operational readiness.
- Supervisor runtime registration and heartbeat through the Unix socket.
- Backend health/status/readiness APIs.
- A text-only assistant turn route for firmware bring-up.
- Native firmware boot/display/buttons/Wi-Fi/microphone VAD baseline.

Not implemented yet:

- `/api/voice/ws`.
- Backend wake detection.
- Audio streaming from firmware to backend.
- STT/TTS adapters.
- Firmware TTS playback.
- Live endpoint/session dashboard telemetry.
