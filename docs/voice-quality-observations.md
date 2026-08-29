# Voice Quality Observations

Voice quality observation logging is disabled by default. Operators enable it
locally with:

```bash
VOICE_QUALITY_OBSERVATION_LOG_ENABLED=true
VOICE_QUALITY_OBSERVATION_TRANSCRIPT_MODE=redacted
VOICE_QUALITY_OBSERVATION_DIR=runtime/voice_quality_observations
```

`VOICE_QUALITY_OBSERVATION_TRANSCRIPT_MODE` must be `redacted` or `full`.
`redacted` keeps transcript character counts and STT provider/model/confidence
without storing transcript text. `full` stores the STT transcript text and should
only be used when the operator intentionally wants local text retention.

When enabled, completed accepted voice turns append one JSON object per line to a
daily file:

```text
runtime/voice_quality_observations/YYYY-MM-DD.jsonl
```

Records are separate from voice session history, runtime logs, wake recordings,
micro-VAD debug chunks, placement calibration samples, and TTS artifacts. They
contain derived diagnostic fields only:

- observed/recorded timestamps
- endpoint and session IDs
- STT metadata and optional transcript text
- redacted Speaker ID public/display identity only when policy permits
- Speaker ID score, confidence, margin, tier, provider/model, and reason
- ambient/SNR status derived from audio-quality metrics
- audio-quality metrics, including duration, RMS/peak, clipping,
  active/silence ratio, ambient level, SNR, status, and warnings
- schema/source version fields

Observation records must not contain raw audio, base64 audio, embeddings,
voiceprints, biometric templates, passcodes, or model-internal features. The log
does not create a new raw-recording path; raw audio remains transient unless an
existing explicit debug recorder is enabled elsewhere, and those debug recorders
retain their one-day cleanup behavior.

Retention is one calendar month by local file date, not a fixed 30 days. Cleanup
subtracts one calendar month from the current local date and deletes daily files
older than that cutoff. For example, on August 26 the cutoff is July 26, so
`2026-07-25.jsonl` is deleted and `2026-07-26.jsonl` is kept.

Status and cleanup APIs:

```bash
curl http://127.0.0.1:9004/api/voice/quality-observations
curl -X POST http://127.0.0.1:9004/api/voice/quality-observations/cleanup
```
