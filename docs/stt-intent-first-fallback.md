# Intent-First STT Fallback

Task 099 adds an intent-first STT path for Faster Whisper profiles.

## Behavior

When a Faster Whisper profile has `fallback_profile` and `fallback_when` configured, HexeVoice first transcribes with the fast profile. The assistant handles that transcript normally. If the result matches the profile fallback rules, the voice pipeline re-transcribes the same trimmed audio with the fallback profile and reruns assistant handling with the fallback transcript.

Fallback can trigger on:

- `empty_transcript`
- `low_confidence`
- `intent_unmatched`
- STT provider errors

The built-in CUDA intent-first profile is:

- `cuda_fast_intent`: `small.en`, CUDA, `float16`, low beam/best-of, fallback to `cuda_accurate_fallback`
- `cuda_accurate_fallback`: `medium.en`, CUDA, `float16`, higher beam/best-of

## Notes

The fallback path keeps the final assistant response authoritative. It emits `stt.fallback.completed` or `stt.fallback.failed` voice events and reports the latest fallback decision in STT provider status under `last_fallback`.

For the external Faster Whisper service, the requested model is now included in the `/transcribe` payload so the service can select the fast or fallback model per request.
