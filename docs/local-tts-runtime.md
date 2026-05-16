# Local TTS Runtime

Created: 04/26/2026

## Provider Contract

HexeVoice can route text-to-speech through a local Piper service by setting:

```bash
VOICE_TTS_PROVIDER=piper
VOICE_TTS_PIPER_TRANSPORT=unix
VOICE_TTS_PIPER_SOCKET=runtime/sockets/tts.sock
VOICE_TTS_PIPER_SYNTHESIZE_PATH=/api/tts
```

`VOICE_TTS_PIPER_BASE_URL` remains available as an explicit TCP/debug override. When it is unset and `VOICE_TTS_PROVIDER=piper`, the backend uses the Piper Unix socket by default.

Backend TTS routing is selected with `VOICE_TTS_PROVIDER`:

- `deterministic`: test/development metadata-only synthesis.
- `openai`: OpenAI speech synthesis using the existing OpenAI TTS settings.
- `piper`: local Piper service over HTTP.

For `piper`, the backend writes returned WAV bytes into `runtime/voice_tts` and serves them through `/api/voice/tts/{stream_id}` for firmware playback. If the Piper request fails, the current fallback policy is deterministic synthesis so the voice session can still complete with observable provider status instead of hard failing the turn.

Firmware playback expects RIFF/WAVE PCM audio. The local Piper path stores generated audio as `.wav` artifacts and the backend serves those artifacts with `audio/wav`.
Piper voice models commonly emit 22.05 kHz audio. HexeVoice keeps that provider output as `{stream_id}.raw.wav`, then writes configured conversion variants with Python-SoXR streaming resamplers during the same artifact-generation pass. The default conversion set is `{stream_id}.48k.wav` and `{stream_id}.16k.wav`; `{stream_id}.22050.wav` can also be enabled. TTS sidecars expose `audio_url` as the stable base prefix, such as `/api/voice/tts/{stream_id}/`, plus explicit `audio_url_raw`, `audio_url_16k`, `audio_url_22050`, `audio_url_48k`, and `endpoint_audio_url` fields. Firmware uses `endpoint_audio_url`; kiosk and browser consumers can use the base URL and append the variant they need.

Speaker-capable firmware reports TTS playback progress back over the voice WebSocket with `tts.playback.download_started`, `tts.playback.first_audio_frame`, `tts.playback.completed`, and `tts.playback.failed`. These acknowledgements let the backend distinguish synthesis readiness from endpoint download and actual speaker output.

HexeVoice normalizes Piper WAV artifacts to `VOICE_TTS_OUTPUT_SAMPLE_RATE_HZ`, default `16000`, before serving them to firmware. Set `VOICE_TTS_OUTPUT_SAMPLE_RATE_HZ=0` to keep native Piper output for endpoints without an override. Endpoint-specific rates can be set with `VOICE_TTS_ENDPOINT_SAMPLE_RATES`; these values take precedence over the default output rate:

```env
VOICE_TTS_OUTPUT_SAMPLE_RATE_HZ=48000
VOICE_TTS_ENDPOINT_SAMPLE_RATES=esp-pe-1=48000,esp-box-1=16000
```

The conversion variant set is limited to 48 kHz, 22.05 kHz, and 16 kHz for now:

```env
VOICE_TTS_CONVERSION_SAMPLE_RATES=48000,22050,16000
```

At runtime, the Providers dashboard exposes the installed Piper models, each model's display name derived from the `.onnx.json` `dataset` field, each model's raw sample rate, the models kept warm, and the enabled conversion sample rates. The Runtime status page reports Piper using Piper-specific voice/model sources rather than the generic OpenAI TTS model default. The same data is available through:

```text
GET /api/tts/settings
PUT /api/tts/settings
```

`PUT /api/tts/settings` writes `runtime/voice_tts_settings.json` and updates `PIPER_TTS_WARM_VOICES` in `scripts/piper-tts.env`. The backend reports `restart_required=true` after saving because warm voice process changes are applied when the Piper container is restarted, while backend-side conversion policy changes are applied when the backend runtime is restarted.

Python-SoXR/libsoxr is documented in `docs/third-party-licenses.md`.

Endpoint-specific Piper voice overrides can be set with `VOICE_TTS_ENDPOINT_VOICES`. The value accepts comma-separated `endpoint_id=voice_id` entries or a JSON object. The local stack maps the Home Assistant Voice PE endpoint to Jenny, which emits 22.05 kHz audio:

```env
VOICE_TTS_ENDPOINT_VOICES=esp-pe-1=en_GB-jenny_dioco-medium
```

## Supervisor Shape

When Piper is selected as the TTS provider, HexeVoice advertises a `piper_tts` service in the node runtime metadata sent to Core Supervisor.

Default service metadata:

- `service_id`: `piper_tts`
- `service_name`: `Piper TTS`
- `container_name`: `hexevoice-piper-tts`
- `control_script`: `scripts/piper-tts-control.sh`
- `managed_by`: `core_supervisor_service_action_proxy`
- `base_url`: `http://hexevoice-piper-tts`
- `socket_path`: `runtime/sockets/tts.sock`
- `synthesize_path`: `/api/tts`

## Docker Runtime

HexeVoice owns the first Piper TTS container wrapper:

- compose file: `compose.piper-tts.yaml`
- control script: `scripts/piper-tts-control.sh`
- service module: `src/tts/service.py` launched as `tts.service:app`
- example env file: `scripts/piper-tts.env.example`
- default container name: `hexevoice-piper-tts`
- default image tag: `hexevoice/piper-tts:local`
- default socket: `runtime/sockets/tts.sock`
- default model directory: `runtime/piper-tts/models`
- Docker restart policy: `no`

The container exposes:

```text
GET  /health
POST /api/tts {"text":"hello","voice":"optional-model-name"}
```

`POST /api/tts` returns `audio/wav` bytes. If `voice` is provided and `/models/<voice>.onnx` exists, the service uses that model. Otherwise it uses `PIPER_TTS_MODEL_PATH`, which defaults to `/models/en_US-lessac-medium.onnx`.

During capability declaration, HexeVoice advertises installed Piper `.onnx` files as Core provider models under provider `piper`. Core service resolution can therefore use `preferred_model` to select a voice model, and the caller should pass that same model id to `POST /api/tts/synthesize` as `voice`. The Piper service accepts Core-normalized lowercase model ids as well as the original filename casing.

The compose service does not publish a TCP port by default. Backend calls use HTTP over the mounted Unix socket; set `VOICE_TTS_PIPER_TRANSPORT=tcp` or `VOICE_TTS_PIPER_BASE_URL` only for development/debugging.

To configure local model assets:

```bash
cp scripts/piper-tts.env.example scripts/piper-tts.env
mkdir -p runtime/piper-tts/models
```

Place Piper `.onnx` model files and optional `.onnx.json` config files in `runtime/piper-tts/models`, then set `PIPER_TTS_MODEL_PATH` in `scripts/piper-tts.env` if the default model name is not present.
To prepare the default voice and any configured warm voices automatically, run:

```bash
./scripts/piper-tts-control.sh ready
```

The ready command downloads missing voices, builds/starts the container, waits
for `/health`, and applies the warm-voice config. The default download source is
`https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0`; set
`PIPER_TTS_DOWNLOAD_VOICES` and `PIPER_TTS_VOICE_REPO_URL` in
`scripts/piper-tts.env` to choose voices or use a mirror.

Set `PIPER_TTS_WARM_VOICES` to keep one or more Piper model processes loaded for low-latency synthesis. The local runtime currently keeps the Box default voice on Kathleen low while also warming HFC female medium and Jenny:

```env
PIPER_TTS_MODEL_PATH=/models/en_US-kathleen-low.onnx
PIPER_TTS_WARM_VOICES=en_US-kathleen-low,en_US-hfc_female-medium,en_GB-jenny_dioco-medium
```

Warm voices reuse persistent Piper `--output-raw` processes and are wrapped back into WAV responses, so `/api/tts` keeps the same response shape while avoiding model reload delay for those voices. The warm reader waits for a one-second idle window before closing the current utterance so Jenny and other voices are not truncated on natural output gaps. Non-warm voices still use the cold per-request Piper process path.

When `VOICE_TTS_PROVIDER=piper`, the backend also runs an `every_10_minutes` warmup task that synthesizes `hello` against the configured warm voices and endpoint-specific override voices. The generated artifacts are short-lived and are removed by the normal generated-voice cleanup loop. The latest warmup status is visible in `/api/voice/status` as `voice_tts_warmup`.

Every audio-producing TTS call writes a `{stream_id}.json` sidecar next to the generated audio with `created_at`, `expires_at`, `ttl_seconds`, `model_id`, `voice_id`, base and variant audio URLs, `audio_variant_sample_rate_hz`, and `audio_variant_source_sample_rate_hz`. Voice-session and intent-reply sidecars also include the recognized `transcript` that led to the generated response and the synthesized reply as `spoken_text`. Piper sidecars include `tts_timing_breakdown_ms` with `piper_generation_ms`, `raw_save_ms`, per-variant conversion timings such as `conversion_48k_ms` and `conversion_16k_ms`, `conversion_total_ms`, and `sidecar_write_ms`. Audio fetch routes update the same block with `last_endpoint_fetch_ms` and maintain an `endpoint_fetch` object with fetch count, last variant, route, path, and timestamp. The default expiry is one hour. Intent reply audio can override that with its own `ttl_seconds` or mark the artifact `long_lived`, in which case the intent sidecar overwrites the default adapter sidecar. Generated voice artifacts with metadata sidecars are checked every five minutes and removed when their `expires_at` passes. Audio files without a matching `.json` sidecar are treated as orphans and cleaned once per day at local `00:00`, after a ten-minute age guard. The latest orphan cleanup status is visible in `/api/voice/status` as `voice_orphan_cleanup`.

`GET /api/tts/artifacts` and `GET /api/voice/tts/artifacts` expose a local debug view of recent generated voice streams. Each item reports the stream id, provider/model/voice, selected variant, raw and output sample rates, per-variant sample rates, expiry, file sizes, playable URLs, timing breakdowns, and last endpoint fetch metadata. The optional `limit` query parameter is capped at 200.

Piper conversion policy defaults to `blocking_all`, which preserves the original behavior of producing every configured variant before the TTS call returns. Setting `VOICE_TTS_CONVERSION_POLICY=endpoint_required_sync`, or saving `conversion_policy: "endpoint_required_sync"` through `PUT /api/tts/settings`, makes the request block only on the endpoint-required variant plus the raw artifact. Other configured variants are marked in `pending_audio_variants` and generated by a background conversion thread, then the sidecar is updated with the completed URLs, sample rates, and `background_conversion_*_ms` timings.

To build and run the service locally:

```bash
./scripts/piper-tts-control.sh build
./scripts/piper-tts-control.sh start
./scripts/piper-tts-control.sh status
./scripts/piper-tts-control.sh logs
./scripts/piper-tts-control.sh stop
```

The control script creates the local model directory when starting or restarting the container. `restart` recreates only the Piper TTS compose service without rebuilding the image or restarting the backend. After pulling the `src/tts/` package split, run `./scripts/piper-tts-control.sh build` once so the image copies the new `src/tts` package and launches `tts.service:app`, then use `restart` to recreate the running container. It does not enable Docker auto-restart; lifecycle intent remains with Core Supervisor.

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

Recent voice sessions are also persisted to `runtime/voice_session_history.json` by default. The history record contains session ids, endpoint ids, timestamps, turn timings, wake metadata, a `latency_points` timeline, transcript/assistant/TTS metadata, error state, and replay eligibility. It does not persist raw microphone audio; accepted wake-session WAV capture is controlled separately by the wake recording settings.

The history APIs are:

```text
GET  /api/voice/sessions
GET  /api/voice/sessions/{session_id}
POST /api/voice/sessions/{session_id}/replay
```

Session replay uses the cached TTS stream metadata when the generated audio URL is still available. `POST /api/endpoint/replay` can also fall back to the latest eligible persisted session after a backend restart.

`POST /api/endpoint/speak` accepts `{ "endpoint_id": "...", "text": "..." }` and synthesizes the supplied text for immediate endpoint playback through the same endpoint audio delivery path.
