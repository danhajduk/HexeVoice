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

`AudioQualityMetric` is the Track 1/2 quality result. It records duration, RMS, peak, clipping count/ratio, active/silence ratio, speech RMS/peak/duration, status, warnings, and Track 2 ambient/SNR metadata. Track 2 fields include `ambient_rms`, `ambient_peak`, `ambient_duration_ms`, `snr_db`, `snr_status`, and `snr_reason`. Missing or insufficient ambient references use `snr_status="unavailable"` plus a reason instead of inventing an SNR value.

`AmbientSnrMetric` is the Track 2 ambient/noise result. It records ambient duration, ambient RMS, speech RMS, SNR, optional noise-floor RMS, and optional local classification labels.

Task 240 keeps raw ambient/pre-roll audio in memory only for accepted turn analysis. Persisted session history and status payloads store numeric metrics and counts, not raw ambient bytes. Existing debug capture services remain the only raw-audio retention path and retain their one-day debug retention behavior.

`SpeakerIdentityMetric` is the redacted Speaker ID result. It records status, policy, public speaker id, display name, confidence, confidence tier, score, margin, provider/model, reason, duration, and error. It never contains embeddings or raw audio.

`IdentityPolicyDecisionMetric` records the intent identity requirement decision: policy, allow/reject/follow-up decision, required tier, observed tier, and reason.

`EnrollmentReadinessMetric` records whether an enrollment batch is ready, needs more samples, or is rejected for quality, with accepted counts and total speech duration.

`ValidationPhraseScoreMetric` records holdout or enrollment phrase scoring for STT, Speaker ID, and audio quality. Training phrases may be used for STT/audio-quality checks, but Speaker ID validation should prefer holdout phrases that did not train the profile.

`PlacementMetric` records endpoint placement outcomes over active or passive windows: endpoint id, window length, wake success rate, STT score, Speaker ID score, audio-quality status, ambient status, recommendation, and warnings.

`VoiceQualityObservationRecord` is the future monthly diagnostic log row shape. It can include timestamp, endpoint/session ids, transcript text or character count, STT score/provider, Speaker ID metric, audio-quality metric, ambient/SNR metric, and placement metric. Raw audio and biometric templates remain excluded.

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
