# Voice Event Envelope

HexeVoice endpoint transport messages use the versioned `VoiceEventEnvelope` defined in
`src/hexevoice/voice/contracts.py`.

Required envelope fields:

- `event_type`
- `event_id`
- `session_id`
- `endpoint_id`
- `direction`
- `timestamp`
- `schema_version`
- `payload`

Optional envelope fields:

- `sequence`

The current schema version is `hexevoice.voice.event.v1`. Firmware and backend messages are expected to emit the full
versioned envelope directly, including `event_id`, `timestamp`, `schema_version`, and `payload`. Unknown schema versions
are rejected by backend validation and surfaced through `session.error` plus `/api/voice/status` `event_diagnostics`;
firmware ignores malformed backend-to-endpoint envelopes instead of applying them.

JSON examples for this contract are stored in `docs/voice-event-envelope/`:

- `endpoint-session-start.example.json`
- `endpoint-vad-speech-started.example.json`
- `backend-volume-command.example.json`
- `endpoint-command-ack.example.json`
- `endpoint-command-error.example.json`
- `endpoint-tts-playback-completed.example.json`

The Task 061 schema set is stored in `docs/task-061-json-schemas/`.

Endpoint command acknowledgements use `command.ack`. Endpoint-side command failures use `command.error`. Both are
accepted endpoint-to-backend events and are exposed in `/api/voice/status` as `last_command_ack`,
`last_command_error`, and `event_diagnostics`.

For backend-to-endpoint commands that include a `request_id`, firmware first sends `command.ack` with
`status: "accepted"` and `message: "OK"` once the command envelope is received. It then sends any command-specific
progress, success, or error event needed to describe the actual work.

Endpoint TTS playback acknowledgements use `tts.playback.download_started`, `tts.playback.first_audio_frame`,
`tts.playback.completed`, and `tts.playback.failed`. The payload includes the `stream_id`, `audio_url`,
optional `byte_count`, and failure `reason`/`message` when applicable. The backend exposes the latest event as
`last_tts_playback` and a short `tts_playback_history` list in `/api/voice/status`.

Backend `endpoint.replay` commands may include `payload.loop: true` for alarm-style WAV playback. Firmware downloads
the audio once, repeats the local buffer without additional node commands, and stops the loop on `playback.stop` or a
local playback-stop button/mute control. Commands may also include `payload.mic_mode: "interrupt_only"` to keep the
endpoint microphone open during playback; omitted or `payload.mic_mode: "pause_for_playback"` preserves the normal
mic-pause behavior.

Voice PE firmware starts a short post-playback microphone ignore window after `tts.playback.completed`.
During that window the endpoint keeps updating its local noise floor but suppresses VAD, wake prediction,
micro-VAD chunking, and audio transport so speaker tail audio cannot start a duplicate voice session.

Firmware VAD start uses `vad.speech_started`. The envelope timestamp is the device-side speech-start timestamp, and
the payload carries the measured VAD `level` plus a `source` such as `firmware_vad`. Session history stores this under
`vad` and derives latency fields such as `vad_to_audio_end_ms`, `vad_to_first_audio_frame_ms`, and
`vad_to_playback_completed_ms` as later audio/TTS playback events arrive. Completed session records also include a
`latency_points` timeline for VAD voice detected, wake word detected, VAD silence, STT start/end, intent processing
done, TTS start/end, and session end. Timeline points carry both `offset_from_vad_ms` and
`offset_from_previous_ms` when the VAD start timestamp is known.

Endpoint wake election uses `wake.candidate`. Election-capable firmware sends it after `session.start` and before
streaming the full post-wake utterance. The payload may include `source`, `model`, `confidence`, `chunk_index`,
`chunk_count`, `detected_at`, `detection_window_ms`, `frame_level`, `speech_peak_level`, `noise_floor_level`,
`ambient_level`, `snr_db`, `endpoint_audio_profile_version`, and a privacy-safe `metadata` object. Firmware places
local candidate identifiers and timeout policy details in `metadata`, not as top-level fields, so the backend validator
can keep a tight event contract. The payload must not include raw audio, embeddings, or transcripts. Backend
`openWakeWord` detections are also converted into `backend_openwakeword` candidates so the backend wake path remains a
fallback when endpoint wake is absent, disabled, or the endpoint election wait times out.

The backend uses `VOICE_WAKE_ELECTION_WINDOW_MS` to keep a short arbitration window and scores candidates from wake
confidence plus small bonuses for available audio-quality metrics. It sends `wake.accepted` to the selected endpoint.
Losing or late candidates receive `wake.election.result` with `stand_down: true`, `winner_endpoint_id`, and an
`election` diagnostic containing the candidate list, winner, per-candidate score, score breakdown, and reason.
Existing endpoints that only stream `audio.chunk` remain compatible.

Current firmware reports wake-election protocol capability in heartbeat capabilities under
`capabilities.firmware.modules.wake_word`. The module exposes an experimental endpoint microWakeWord provider with the
official ESPHome `alexa` v2 model as the primary model and `Hexe` as the local product alias. Heartbeats include the
model id, manifest URL, TFLite URL, probability cutoff, sliding window, feature step, tensor-arena size, native inference
availability, `model_version`, asset hashes, embedded TFLite size, and a `micro_wake_engine` diagnostic block. The
adapter links Espressif's TFLM/ESP-NN runtime and reports `tflm_linked`, `feature_frontend_linked`,
`feature_frontend_ready`, `initialized`, `model_asset_available`, `model_asset_bytes`, `model_runtime_ready`,
`runtime_arena_bytes`, `ready`, and `reason`. The Alexa/Hexe model asset and int16-to-int8 audio preprocessor are
embedded in the firmware image, so a ready endpoint reports `endpoint_micro_wake_word_experimental` mode and submits
local `wake.candidate` events through the same backend election contract.

The wake module still exposes `candidate_event_type: "wake.candidate"`, `stand_down_event_type:
"wake.election.result"`, `candidate_source: "endpoint_micro_wake_word"`, `backend_fallback: true`, `fallback_source:
"backend_openwakeword"`, and a 300 ms `stream_after_timeout_backend_fallback` policy. While waiting for election,
firmware buffers microphone frames in the existing pre-roll ring. If the backend elects this endpoint, `wake.accepted`
releases the stream. If the backend sends `wake.election.result` with `stand_down: true`, firmware cancels local capture
and returns to idle/wake-armed without sending full utterance audio.

`audio.chunk` may include optional endpoint-side numeric quality metrics: `frame_level`, `noise_floor_level`,
`speech_peak_level`, `pre_roll_duration_ms`, `contains_pre_roll`, and `contains_speech`. These fields are
privacy-safe transport diagnostics, not raw audio or environment classification. Backend quality analysis prefers
these endpoint-provided ambient/noise-floor metrics when present and falls back to backend-derived in-memory pre-roll
analysis for older firmware that omits them.

Playback interruption remains backend-owned by default through stop-only STT and backend `playback.stop` commands.
Firmware also exposes an experimental local Stop keyword hook under `capabilities.firmware.modules.playback_stop_word`
and `capabilities.audio.input.playback_interrupt`. The configured local model matches the Kevin Ahrendt microWakeWord
Stop release used by Home Assistant Voice PE (`stop.json`/`stop.tflite`, cutoff 0.50, sliding window 5, tensor arena
21000). The Stop module shares the same `micro_wake_engine` diagnostics, asset hashes, and embedded TFLite size. The
Stop model asset is embedded in the firmware image, but the Stop runtime stays disabled until Task 264 so the endpoint
reports the local keyword as configured but unavailable while keeping `backend_stt_interrupt` active.
