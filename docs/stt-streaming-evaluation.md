# Streaming STT Evaluation

Task 098 evaluated whether transcription can start before endpoint utterance capture fully ends.

## Current Path

The endpoint sends `audio.chunk` events over `/api/voice/ws`; the backend stores those chunks in memory and calls STT only after `audio.end`. The active STT adapters (`openai`, `faster_whisper`, and `external_faster_whisper`) expose a single `transcribe(audio_summary)` call. Silence trimming already runs before that call for Faster Whisper and OpenAI paths.

The contract already reserves `transcript.partial`, but the session manager does not emit it today.

## Findings

- True streaming should be added first to the external Faster Whisper engine path, not the in-process adapter.
- The current endpoint transport is already chunked, so firmware does not need a new transport for a first pass.
- The backend needs a new streaming adapter boundary alongside `SpeechToTextAdapter`, because bolting partial transcripts onto `transcribe()` would confuse the batch path.
- Partial transcript events should be optional and best-effort; final transcript authority should still come from the final `audio.end` commit until the streaming engine is proven stable.
- Micro-VAD chunk markers are a useful cut point for streaming windows, but they should not be required. Regular `audio.chunk` cadence is enough for the first implementation.

## Recommended First Implementation

1. Add a `StreamingSpeechToTextAdapter` protocol with `start_session`, `accept_audio_chunk`, `finish_session`, and optional `status`.
2. Add an external STT engine endpoint such as `/stream/session`, `/stream/chunk`, and `/stream/end`, or a WebSocket if the engine implementation prefers it.
3. In `VoiceSessionManager._handle_audio_chunk`, feed chunks to the streaming adapter after wake acceptance.
4. Emit `transcript.partial` only when the partial text changes and is non-empty.
5. Keep the existing `audio.end` batch STT path as fallback and final authority.

## Deferred Decisions

- Whether partial transcripts should drive intent prefetch or only UI feedback.
- Whether streaming STT is enabled per provider, per endpoint, or per setup profile.
- Whether the external STT engine should use HTTP chunk posts or its own WebSocket.
- Whether partial transcript history belongs in `voice_session_history.json` or only live websocket events.
