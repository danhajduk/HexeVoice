# Voice Loop Phase 1 Handoff

Created: 04/25/2026

## Integrated Loop Status

The first single-endpoint wake-to-reply loop is implemented as a configurable Phase 1 path:

1. Firmware endpoint configuration is generated from `firmware/config/endpoint.yaml` or the committed example template.
2. Firmware sends heartbeat metadata to `POST /api/endpoint/heartbeat`.
3. Firmware opens `/api/voice/ws`, starts a voice session, and sends bounded PCM chunk envelopes.
4. Backend wake authority inspects `audio.chunk` through a configurable `WakeDetector` adapter and emits `wake.accepted`.
5. Backend finalizes the turn on `audio.end`, routes captured transient audio through the configured STT adapter, routes the transcript through `AssistantTurnService`, and sends response text to the configured TTS adapter.
6. Backend exposes generated TTS audio at `/api/voice/tts/{stream_id}` when the real TTS provider succeeds.
7. Firmware maps backend events to endpoint UX phases, fetches WAV TTS audio from backend URLs, and writes PCM frames to the ESP-BOX speaker codec.
8. Frontend dashboard reads `/api/voice/status` and shows connection state, active session, latest transcript, latest response, TTS metadata, errors, supported actions, wake provider health, and STT/TTS provider health.

## What Is Real

- Shared voice event envelope and session state contract.
- `/api/voice/ws` WebSocket route.
- In-memory single-endpoint, single-session backend manager.
- Backend wake detector adapter boundary with deterministic test detector and configurable openWakeWord adapter.
- Backend openWakeWord dependency installed in `.venv` from `requirements.txt`.
- Backend STT -> assistant -> TTS adapter boundary with deterministic and OpenAI-compatible STT/TTS adapters.
- Transient in-memory audio aggregation for completed wake sessions.
- Playable backend TTS audio route at `/api/voice/tts/{stream_id}`.
- Firmware heartbeat, readiness-gated WebSocket session start, audio chunk send, `audio.end`, and cancel message paths.
- Firmware backend event-to-UX mapping.
- Firmware backend-authority wake alignment: VAD opens/closes transport, the top bar shows when audio is being streamed, and backend `wake.accepted` owns listening UX.
- Firmware WAV TTS download and speaker playback path.
- Frontend voice endpoint observability dashboard backed by live local APIs.
- Backend integration test for wake-to-reply and cancel behavior: `tests/test_voice_loop_integration.py`.

## Runtime Configuration

Development defaults keep the loop deterministic:

- `VOICE_WAKE_PROVIDER=openwakeword`
- `VOICE_STT_PROVIDER=deterministic`
- `VOICE_TTS_PROVIDER=deterministic`

For Phase 2 local faster-whisper STT, configure:

- `VOICE_STT_PROVIDER=faster_whisper`
- `VOICE_STT_PRELOAD=true`
- `VOICE_STT_FASTER_WHISPER_MODEL=base.en`
- `VOICE_STT_FASTER_WHISPER_DEVICE=cpu`
- `VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE=int8`

The faster-whisper provider writes each completed captured turn to a transient WAV file under `runtime/stt/faster-whisper`, runs the local faster-whisper model, returns the merged segment text, and removes the temporary audio file after the transcription attempt.

The assistant path is intentionally local for the current full-pipeline smoke test. It strips the configured wake word from the final transcript and returns `I heard <transcript>, no AI added yet.`. The voice node must not call OpenAI directly; future assistant integration should go through the AI Node.

Assistant adapter configuration:

- `VOICE_ASSISTANT_PROVIDER=local_echo` keeps the smoke-test response path active.
- `VOICE_ASSISTANT_PROVIDER=ai_node` enables the AI Node adapter boundary.
- `VOICE_ASSISTANT_AI_NODE_BASE_URL` points to the AI Node service when that integration is ready.
- `VOICE_ASSISTANT_AI_NODE_TURN_PATH=/api/assistant/turn`
- `VOICE_ASSISTANT_TIMEOUT_S=20`
- `VOICE_CONVERSATION_CONTEXT_TURNS=6`

If the AI Node adapter is selected but unavailable or unconfigured, the voice node falls back to the local echo response so the full voice pipeline can still be validated.

The assistant service keeps a short in-memory rolling context by endpoint and by session. The current local echo adapter ignores the context so smoke-test replies remain unchanged, but the AI Node adapter includes the context list in its request payload.

Local Piper TTS can be selected with:

- `VOICE_TTS_PROVIDER=piper`
- `VOICE_TTS_PIPER_BASE_URL`
- `VOICE_TTS_PIPER_SYNTHESIZE_PATH=/api/tts`
- optionally `VOICE_TTS_PIPER_VOICE`

If Piper is selected but unavailable or unconfigured, the voice node falls back to deterministic TTS metadata so the full voice pipeline remains testable.

Firmware UX timing notes:

- Endpoint VAD now uses an explicit 1000 ms silence hold before sending `audio.end`.
- The LCD stays in Replying while real TTS audio playback is queued or active; `session.completed` no longer forces Idle until playback is done.
- Firmware plays a short local listening cue when the backend emits `wake.accepted`, so the endpoint gives immediate audible feedback before the user speaks the command.
- Speaker playback briefly pauses the microphone stream, then resumes it, to avoid the ESP-BOX I2S input path blocking local cue and TTS output.
- The endpoint no longer shows the idle screen until Wi-Fi, backend heartbeat, and the voice WebSocket are all connected; the boot FW label was removed from the LCD.

`GET /api/voice/status` exposes the latest transcript text plus `last_transcript_metadata` with provider id, model, confidence, duration in milliseconds, text length, and provider error. It also exposes `last_assistant` with provider id, model, response text, latency, text length, and error state, plus `last_turn_timings` with STT, assistant, TTS, and total turn duration in milliseconds. The backend log records transcript finalization, local faster-whisper latency, and the full turn timing breakdown.

For wake model setup:

- `VOICE_WAKE_THRESHOLD=0.5`
- `VOICE_WAKE_BUFFER_MS=1280`
- `VOICE_WAKE_PREDICTION_FRAME_MS=80`
- optionally `VOICE_WAKE_MODELS`
- optionally `VOICE_WAKE_AUTO_DOWNLOAD_MODELS=true`

## Still Deferred

- openWakeWord wake phrase/model tuning on real ESP microphone audio.
- AI Node assistant integration.
- Persistent endpoint registry and voice session history.
- Backend support for replay, mute, and reconnect operator actions.
- Full on-device acoustic validation after flashing the latest firmware.

## Verification

Completed on 04/25/2026 for the original MVP contract path:

- `PYTHONPATH=src .venv/bin/pytest -q tests/test_voice_loop_integration.py` -> 2 passed.
- `PYTHONPATH=src .venv/bin/pytest -q` -> 63 passed, 52 FastAPI deprecation warnings.
- `cd frontend && npm run build` -> passed.
- `cd firmware && idf.py build` -> blocked because `idf.py` is not installed in this shell.

Completed on 04/25/2026 for the Phase 1 provider/device pass:

- `.venv/bin/pip install -r requirements.txt` -> passed; installed `openwakeword==0.6.0` and runtime dependencies.
- `.venv/bin/python -c "import openwakeword; from openwakeword.model import Model"` -> passed.
- `PYTHONPATH=src .venv/bin/pytest -q` -> 76 passed, 60 FastAPI deprecation warnings.
- `./firmware/build.sh` -> passed; generated `firmware/build/hexe_firmware.bin` and exported firmware artifacts.

The repo now has a tested backend voice loop with configurable real wake/STT/TTS providers, firmware audio transport, and firmware WAV playback. Final proof of “Hexe, what time is it?” still requires setting live provider credentials/models, restarting the stack, flashing the latest firmware, and tuning wake/VAD on the physical device.
