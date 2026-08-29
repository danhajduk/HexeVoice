# Voice Metric Schemas

Task 239 defines the local/internal schema source for voice quality, Speaker ID, enrollment, validation, placement, and observation metrics.

These schemas are intentionally not a Core-facing contract yet. They are runtime and persistence shapes for HexeVoice diagnostics, logs, UI/status payloads, and later roadmap tracks. If a later task exposes one of these shapes to Core, that task should define the external contract explicitly.

## Compatibility

- Current schema version: `1`
- Code source: `src/hexevoice/voice/metric_schemas.py`
- All persisted, exported, or logged diagnostic records should include `schema_version`.
- Additive local fields can be added only when they remain privacy-safe and are documented here.
- Breaking field changes require a new schema version and migration notes for persisted/logged data.

## Redaction

Metric payloads intended for logs, status, UI diagnostics, or monthly observation files must not contain:

- raw audio bytes or base64 audio
- speaker embeddings, biometric templates, voiceprints, or embedding vectors
- spoken passcodes or PINs
- retained training audio

Use `assert_metric_payload_redacted()` before writing new metric payloads to logs or status surfaces.

## Schema Shapes

`AudioQualityMetric` is the Track 1/2 quality result. It records duration, RMS, peak, clipping count/ratio, active/silence ratio, speech RMS/peak/duration, status, warnings, and Track 2 ambient/SNR metadata. Track 2 fields include `ambient_rms`, `ambient_peak`, `ambient_duration_ms`, `snr_db`, `snr_status`, `snr_reason`, and `source`. Missing or insufficient ambient references use `snr_status="unavailable"` plus a reason instead of inventing an SNR value. `source` is `endpoint` when endpoint-provided numeric ambient/noise metrics were used, otherwise `backend`.

`AmbientSnrMetric` is the Track 2 ambient/noise result. It records ambient duration, ambient RMS, speech RMS, SNR, optional noise-floor RMS, and optional local classification labels.

Task 240 keeps raw ambient/pre-roll audio in memory only for accepted turn analysis. Persisted session history and status payloads store numeric metrics and counts, not raw ambient bytes. Existing debug capture services remain the only raw-audio retention path and retain their one-day debug retention behavior.

Task 241 lets newer endpoints report numeric `audio.chunk` metrics such as frame level, noise-floor level, speech peak level, pre-roll duration, and pre-roll/speech flags. Backend diagnostics prefer those endpoint-provided values when present and fall back to backend-derived in-memory pre-roll analysis for older firmware.

`SpeakerIdentityMetric` is the redacted Speaker ID result. It records status, policy, public speaker id, display name, confidence, confidence tier, score, margin, provider/model, broad age/access metadata, profile-learning consent, the diagnostic-only learning eligibility decision, reason, duration, and error. It never contains embeddings or raw audio.

Profile-learning eligibility is only a diagnostic candidate marker. Eligible
means the turn may be shown to an operator-review workflow later; it does not
permit automatic profile updates. The decision requires identified or verified
speaker status, `high` or `very_high` confidence, sufficient score margin,
acceptable audio quality, explicit consent for derived biometric updates, and a
route policy that does not forbid learning.

`IdentityPolicyDecisionMetric` records the intent identity requirement decision: policy, allow/reject/follow-up decision, required tier, observed tier, and reason.

`EnrollmentReadinessMetric` records whether an enrollment batch is ready, needs more samples, or is rejected for quality, with accepted counts and total speech duration.

`ValidationPhraseScoreMetric` records holdout or enrollment phrase scoring for STT, Speaker ID, and audio quality. Training phrases may be used for STT/audio-quality checks, but Speaker ID validation should prefer holdout phrases that did not train the profile.

`PlacementMetric` records endpoint placement outcomes over active or passive windows: endpoint id, window length, wake success rate, STT score, Speaker ID score, audio-quality status, ambient status, recommendation, and warnings.

`VoiceQualityObservationRecord` is the optional monthly diagnostic log row shape implemented by `src/hexevoice/persistence/voice_quality_observation_log.py`. It can include timestamp, endpoint/session ids, transcript text or character count depending on `VOICE_QUALITY_OBSERVATION_TRANSCRIPT_MODE`, STT provider/model/confidence, redacted Speaker ID summary when policy permits, audio-quality metrics, ambient/SNR status, and source version fields. Raw audio, embeddings, biometric templates, voiceprints, passcodes, and model-internal features remain excluded. Observation JSONL files use one-calendar-month local-date retention, not fixed 30-day retention.

## Confidence Tiers

Speaker confidence tiers are shared by identity policy, enrollment validation, admin-gated intents, and child/teen restrictions:

| Tier | Confidence |
| --- | --- |
| `very_high` | `>= 0.95` |
| `high` | `>= 0.85` and `< 0.95` |
| `medium` | `>= 0.70` and `< 0.85` |
| `low` | `> 0` and `< 0.70` |
| `none` | missing or zero confidence |

Admin maintenance intents should require `very_high` identity plus the spoken passcode policy defined by the admin-intent task. Sensitive personal intents should require at least `high` unless a later task sets a stricter per-intent rule.
