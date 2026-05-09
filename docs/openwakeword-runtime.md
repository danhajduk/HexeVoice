# openWakeWord Runtime Ownership

Created: 04/25/2026

## Archived External Container

The previously working wake-word runtime was owned by the HomeAssistant compose stack, not by HexeVoice:

- container name: `openwakeword`
- image: `rhasspy/wyoming-openwakeword`
- published port: `10400`
- compose project: `homeassistant`
- compose file: `/home/dan/Projects/HomeAssistant/docker-compose.yml`
- custom model mount: `/home/dan/Projects/HomeAssistant/openwakeword/models:/custom`
- previous restart policy: `unless-stopped`

On 04/25/2026 the container was stopped and its Docker restart policy was disabled:

```bash
docker update --restart=no openwakeword
docker stop openwakeword
```

The verified state after archival was:

```text
Restart=no Status=exited Exit=137
```

This prevents Docker itself from restarting the old container after host or Docker daemon restart.

## Remaining External Ownership Risk

The old service is still declared in the HomeAssistant compose file. If that external compose stack is started again, compose can recreate or start the `openwakeword` service regardless of the disabled Docker restart policy on the existing container.

HexeVoice should not edit `/home/dan/Projects/HomeAssistant` from this repository unless that cross-repo change is explicitly requested. The migration path is to add a HexeVoice-owned openWakeWord runtime and register that runtime with Core Supervisor.

## HexeVoice-Owned Container Definition

HexeVoice now owns a local openWakeWord container definition:

- compose file: `compose.openwakeword.yaml`
- control script: `scripts/openwakeword-control.sh`
- example env file: `scripts/openwakeword.env.example`
- default container name: `hexevoice-openwakeword`
- default image: `rhasspy/wyoming-openwakeword`
- default port: `10400`
- default model directory: `runtime/openwakeword/models`
- Docker restart policy: `no`

The runtime model directory is intentionally gitignored except for `.gitkeep`; trained `.tflite` and `.onnx` model files are local runtime assets.

To copy models from the old HomeAssistant-owned directory or from the current local `runtime/vioce_models/Hexa.tflite` file:

```bash
./scripts/openwakeword-control.sh sync-models
```

To start, stop, or inspect the HexeVoice-owned container:

```bash
./scripts/openwakeword-control.sh start
./scripts/openwakeword-control.sh status
./scripts/openwakeword-control.sh logs
./scripts/openwakeword-control.sh stop
```

The scripts provide local operator control without enabling Docker auto-restart, and they are the command surface used by the Supervisor service-action proxy.

## Supervisor Registration and Lifecycle

HexeVoice advertises the `openwakeword` service in the existing node runtime registration sent to Core Supervisor over `/run/hexe/supervisor.sock`.

The backend includes the service in `runtime_metadata.services` with:

- `service_id`: `openwakeword`
- `container_name`: `hexevoice-openwakeword`
- `control_script`: `scripts/openwakeword-control.sh`
- `managed_by`: `core_supervisor_service_action_proxy`

Supervisor service lifecycle actions use the standard node service proxy:

```text
GET  /api/services/status
POST /api/services/start   {"target":"openwakeword"}
POST /api/services/stop    {"target":"openwakeword"}
POST /api/services/restart {"target":"openwakeword"}
```

The node service action routes call `scripts/openwakeword-control.sh`, which keeps Docker restart policy set to `no`; lifecycle intent remains with Core Supervisor instead of Docker auto-restart.

## Backend Wake Provider

Use the supervised wake provider when the backend should stream firmware audio to the container over the Wyoming protocol:

```bash
VOICE_WAKE_PROVIDER=supervised_openwakeword
VOICE_WAKE_SERVICE_HOST=127.0.0.1
VOICE_WAKE_SERVICE_PORT=10400
VOICE_WAKE_MODELS=Hexa
```

`VOICE_WAKE_MODELS` is interpreted as wake-word names for the Wyoming service in this mode. The older in-process provider remains available with `VOICE_WAKE_PROVIDER=openwakeword`, where `VOICE_WAKE_MODELS` continues to mean local model paths.

`GET /api/voice/status` includes a `wake_history` array with the 10 most recent meaningful wake outcomes. Accepted entries include timestamp, endpoint id, session id, model, confidence, chunk index, and chunk count. No-detection entries include timestamp, endpoint id, session id, reason, and chunk count. It also includes `wake_confidence_history`, the 50 most recent wake scores that reported a confidence value, including below-threshold scores for tuning. Those samples are also written as `wake.confidence` JSON records in the voice record log.

Accepted wake sessions can be recorded for short-term wake-word and STT tuning by enabling:

```env
VOICE_WAKE_RECORDINGS_ENABLED=true
VOICE_WAKE_RECORDING_RETENTION_DAYS=7
VOICE_WAKE_RECORDING_PREROLL_MS=2000
```

When enabled, HexeVoice writes accepted wake sessions to `runtime/wake_recordings` by default. Each recording is a PCM WAV containing the wake pre-roll/wake chunk plus the post-wake audio sent to STT, with a JSON sidecar containing endpoint, session, model, confidence, format, duration, and expiration metadata. Cleanup removes `.wav` and `.json` recordings older than the configured retention window.

Validation notes are captured in `docs/supervised-openwakeword-validation.md`.
