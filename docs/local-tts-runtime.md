# Local TTS Runtime

Created: 04/26/2026

## Provider Contract

HexeVoice can route text-to-speech through a local Piper service by setting:

```bash
VOICE_TTS_PROVIDER=piper
VOICE_TTS_PIPER_SERVICE_HOST=127.0.0.1
VOICE_TTS_PIPER_SERVICE_PORT=10200
VOICE_TTS_PIPER_SYNTHESIZE_PATH=/api/tts
```

`VOICE_TTS_PIPER_BASE_URL` remains available as an explicit override. When it is unset and `VOICE_TTS_PROVIDER=piper`, the backend resolves the Piper base URL from the service host and port.

Backend TTS routing is selected with `VOICE_TTS_PROVIDER`:

- `deterministic`: test/development metadata-only synthesis.
- `openai`: OpenAI speech synthesis using the existing OpenAI TTS settings.
- `piper`: local Piper service over HTTP.

For `piper`, the backend writes returned WAV bytes into `runtime/voice_tts` and serves them through `/api/voice/tts/{stream_id}` for firmware playback. If the Piper request fails, the current fallback policy is deterministic synthesis so the voice session can still complete with observable provider status instead of hard failing the turn.

Firmware playback expects RIFF/WAVE PCM audio. The local Piper path stores generated audio as `.wav` artifacts and the backend serves those artifacts with `audio/wav`.
Piper voice models commonly emit 22.05 kHz audio; HexeVoice normalizes Piper WAV artifacts to `VOICE_TTS_OUTPUT_SAMPLE_RATE_HZ`, default `16000`, before serving them to firmware.

## Supervisor Shape

When Piper is selected as the TTS provider, HexeVoice advertises a `piper_tts` service in the node runtime metadata sent to Core Supervisor.

Default service metadata:

- `service_id`: `piper_tts`
- `service_name`: `Piper TTS`
- `container_name`: `hexevoice-piper-tts`
- `control_script`: `scripts/piper-tts-control.sh`
- `managed_by`: `core_supervisor_service_action_proxy`
- `base_url`: `http://127.0.0.1:10200`
- `synthesize_path`: `/api/tts`

## Docker Runtime

HexeVoice owns the first Piper TTS container wrapper:

- compose file: `compose.piper-tts.yaml`
- control script: `scripts/piper-tts-control.sh`
- example env file: `scripts/piper-tts.env.example`
- default container name: `hexevoice-piper-tts`
- default image tag: `hexevoice/piper-tts:local`
- default port: `10200`
- default model directory: `runtime/piper-tts/models`
- Docker restart policy: `no`

The container exposes:

```text
GET  /health
POST /api/tts {"text":"hello","voice":"optional-model-name"}
```

`POST /api/tts` returns `audio/wav` bytes. If `voice` is provided and `/models/<voice>.onnx` exists, the service uses that model. Otherwise it uses `PIPER_TTS_MODEL_PATH`, which defaults to `/models/en_US-lessac-medium.onnx`.

The compose service also has a Docker health check that calls `GET /health` inside the container.

To configure local model assets:

```bash
cp scripts/piper-tts.env.example scripts/piper-tts.env
mkdir -p runtime/piper-tts/models
```

Place Piper `.onnx` model files and optional `.onnx.json` config files in `runtime/piper-tts/models`, then set `PIPER_TTS_MODEL_PATH` in `scripts/piper-tts.env` if the default model name is not present.

To build and run the service locally:

```bash
./scripts/piper-tts-control.sh build
./scripts/piper-tts-control.sh start
./scripts/piper-tts-control.sh status
./scripts/piper-tts-control.sh logs
./scripts/piper-tts-control.sh stop
```

The control script creates the local model directory when starting or restarting the container. It does not enable Docker auto-restart; lifecycle intent remains with Core Supervisor.

Operator health checks:

```bash
curl http://127.0.0.1:10200/health
./scripts/piper-tts-control.sh status
```

When `VOICE_TTS_PROVIDER=piper`, the node service action proxy accepts:

```text
POST /api/services/start   {"target":"piper_tts"}
POST /api/services/stop    {"target":"piper_tts"}
POST /api/services/restart {"target":"piper_tts"}
```

Those routes call `scripts/piper-tts-control.sh`, matching the control surface advertised in Supervisor runtime metadata.

## Endpoint Replay

`POST /api/endpoint/replay` synthesizes a fresh short response from the last transcript when one is available:

```text
I heard <last transcript>
```

The synthesized replay uses the active TTS provider, so with Piper enabled it creates a new WAV artifact under `runtime/voice_tts/` and sends that artifact URL to the endpoint.
