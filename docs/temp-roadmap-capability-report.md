# HexeVoice Roadmap Capability Report

Status: temporary discussion draft
Created: 2026-08-27
Source note: `docs/posible road-map.txt`

## Purpose

This report compares the voice identity and audio-quality roadmap note against the current HexeVoice implementation. It is intended as a working file for discussion before creating a formal roadmap.

## Executive Summary

HexeVoice is already close to the identity-aware turn-routing part of the roadmap. STT and Speaker ID can run from the same utterance, personal routes can require identity, generic routes can skip identity, and Speaker ID has configurable confidence and margin thresholds.

The largest missing areas are the audio-quality analysis path, multi-endpoint wake election, placement calibration/reporting, and production-grade Speaker ID model enablement. Firmware already has useful raw ingredients for these features, including pre-roll buffering, frame energy levels, micro-VAD chunk metadata, and Voice PE adaptive noise-floor tracking, but those signals are not yet promoted into a node-level quality result or used for endpoint selection.

## Current Capability Map

### Implemented Or Mostly Implemented

- Voice WebSocket transport for endpoint sessions, audio chunks, VAD start, audio end, command acknowledgements, TTS playback events, and backend state events.
- Backend-owned wake detection with deterministic, in-process openWakeWord, and supervised openWakeWord options.
- Batch STT turn finalization after `audio.end`.
- STT adapter boundary for deterministic, OpenAI, local Faster-Whisper, and external Faster-Whisper.
- TTS adapter boundary for deterministic, OpenAI, and Piper.
- Speaker ID helper service contract with local profile store.
- Speaker enrollment using one or more uploaded/base64 WAV samples.
- Multiple speaker embeddings per profile.
- Configurable Speaker ID thresholds:
  - identify minimum confidence
  - identify minimum margin
  - verify minimum score
- Speaker ID per-turn integration:
  - runs alongside STT
  - can be optional, required, forbidden, or use-if-ready
  - blocks required personal routes when identity is unknown or unavailable
  - asks "Who is this?" for required identity failures
- Redacted Speaker ID session metadata.
- Endpoint firmware pre-roll buffer.
- Firmware micro-VAD chunk markers.
- Firmware energy-level VAD.
- Voice PE adaptive noise-floor gate.
- Optional wake recording and micro-VAD debug recording.
- Multi-endpoint connection tracking in the backend.

### Partially Implemented

- Speaker ID production model support.
  - The service contract is present.
  - The deterministic adapter works for tests.
  - SpeechBrain, WeSpeaker, pyannote, and NeMo are cataloged but runtime implementation is pending.
- Confidence handling.
  - Low confidence and low margin are detected.
  - Full high/medium/low tier behavior is not yet formalized.
- Enrollment quality.
  - Multiple samples are supported.
  - Required enrollment phrase count, duration, phrase diversity, and active-profile readiness gates are not enforced.
- Pre-roll.
  - Firmware preserves pre-roll and sends it after session start.
  - The node does not yet isolate pre-speech ambient audio for SNR analysis.
- Audio diagnostics.
  - Some low-level timing, VAD, wake confidence, and chunk metadata exists.
  - There is no unified audio-quality result joined into the turn.
- Streaming STT.
  - `transcript.partial` exists in the contract.
  - The current STT path remains final/batch after `audio.end`.

### Not Implemented Yet

- Dedicated audio-quality/environment analysis path.
- Per-turn metrics for:
  - SNR
  - RMS/speech level
  - clipping
  - ambient level
  - speech duration quality
  - overlapping speakers
  - background speech
  - TV/music/appliance classification
- Combined pre-intent decision object joining STT, Speaker ID, and audio quality.
- Endpoint wake election when multiple devices hear the same wake word.
- Stand-down command for losing endpoints.
- Placement calibration mode.
- Passive ambient sampling schedule.
- Active placement test flow with known phrase/speaker.
- Placement report with score and recommendations.
- Profile auto-learning policy.
- Safe "learn from this turn" gating based on confidence tier, margin, and audio quality.
- Privacy contract for unattended ambient metric sampling.

## Roadmap Tracks

### Track 1: Audio Quality Foundation

Goal: add a local, privacy-safe audio-quality analyzer for each accepted turn.

Suggested first implementation:

- Add `src/hexevoice/voice/audio_quality.py`.
- Define `AudioQualityResult`.
- Compute from PCM bytes:
  - duration_ms
  - rms
  - peak
  - clipping_count
  - clipping_ratio
  - silence_ratio or active_audio_ratio
  - speech_level_estimate
  - ambient_level_estimate when pre-roll is available
  - snr_db when both ambient and speech estimates exist
  - quality_status such as `ok`, `short_audio`, `low_snr`, `clipped`, `silent`
- Add tests using synthetic PCM.
- Include redacted quality metadata in voice session history and status.

Why first:

This gives the rest of the roadmap a stable signal layer. Speaker ID, endpoint election, and placement scoring all need the same quality metrics.

### Track 2: Pre-Roll And Ambient Reference

Goal: separate pre-speech ambient samples from post-wake speech samples.

Suggested first implementation:

- Extend firmware audio chunk metadata to label pre-roll chunks or include frame-level metrics.
- Alternatively, backend can infer pre-roll from wake acceptance timing and stored wake recorder metadata for a first pass.
- Preserve only short in-memory pre-roll metrics by default.
- Avoid raw pre-roll persistence unless existing wake recording debug mode is enabled.

Useful output:

- ambient_rms
- ambient_peak
- ambient_duration_ms
- snr_db compared with utterance speech level

### Track 3: Speaker ID Production Readiness

Goal: move from contract/test adapter to a real household-usable Speaker ID engine.

Suggested first implementation:

- Enable SpeechBrain ECAPA-TDNN adapter first, unless benchmarks show another provider is better.
- Keep model/license metadata operator-visible.
- Add provider install/readiness validation.
- Add benchmark script samples for household-like audio.
- Add enrollment readiness checks:
  - minimum sample count
  - minimum total duration
  - compatible sample rate
  - non-silent audio
  - no obvious clipping
- Add confidence tier mapping:
  - high: can identify and may become learning candidate
  - medium: can personalize low-risk routes only
  - low/unknown: do not guess

### Track 4: Intent Policy And Learning Policy

Goal: make speaker identity policy explicit and safe.

Suggested first implementation:

- Keep built-in generic intents as `not_required`.
- Mark personal services as `required`.
- Add a policy field to registered intent metadata where possible.
- Add `learning_eligible` only when:
  - speaker confidence is high
  - score margin is sufficient
  - audio quality is acceptable
  - profile has explicit consent
  - route is not forbidden for learning
- Do not update profiles automatically until this policy is tested with real household data.

### Track 5: Endpoint Wake Election

Goal: prevent multiple nearby endpoints from streaming the same utterance.

Suggested first implementation:

- Add candidate event type, for example `wake.candidate`.
- Candidate payload should include:
  - wake confidence
  - frame/speech level
  - ambient level
  - estimated SNR
  - endpoint audio profile/version
  - timestamp
- Backend opens a short election window, likely 150-300 ms.
- Backend selects winner by score.
- Backend sends:
  - `wake.accepted` or `capture.start` to winner
  - `capture.stand_down` or `endpoint.cancel` to losers
- First pass can be backend-only with simulated endpoints before firmware behavior changes.

### Track 6: Placement Calibration

Goal: help operators place endpoints where real voice performance is strong.

Suggested first implementation:

- Add manual placement test mode first.
- Operator selects endpoint and room/zone.
- UI asks for a known phrase and optional known speaker.
- Full pipeline runs:
  - STT
  - Speaker ID
  - audio quality
- Store only metrics and expected-vs-observed results by default.
- Produce per-location score:
  - STT success
  - Speaker ID confidence
  - SNR
  - clipping
  - background contamination
  - response consistency

Later implementation:

- Passive ambient sampling every N minutes for 24-48 hours.
- Store metrics only.
- Do not run STT or Speaker ID on unattended ambient samples.

## Proposed Milestones

### Milestone A: Turn Audio Quality

Deliverable:

- Audio-quality analyzer module.
- `AudioQualityResult` included in voice turn/session metadata.
- Unit tests with synthetic PCM.
- No firmware changes required for basic metrics.

### Milestone B: Ambient SNR

Deliverable:

- Pre-roll/ambient metrics available to backend.
- Per-turn SNR estimate.
- Clear unknown/low-SNR reasons in session diagnostics.

### Milestone C: Speaker ID Hardening

Deliverable:

- One real Speaker ID provider enabled.
- Enrollment quality checks.
- Confidence tiers.
- No automatic profile learning yet.

### Milestone D: Policy Join

Deliverable:

- STT, Speaker ID, and audio quality are combined before intent execution.
- Required identity failures can explain likely cause:
  - unknown speaker
  - low confidence
  - low margin
  - low SNR
  - speech too short
  - clipped audio

### Milestone E: Endpoint Election

Deliverable:

- Wake candidate protocol.
- Backend election window.
- Winner/stand-down command flow.
- Simulated multi-endpoint tests.
- Firmware implementation after protocol is validated.

### Milestone F: Placement Tools

Deliverable:

- Active placement test.
- Placement report.
- Optional passive ambient metrics mode.

## Recommended Immediate Next Task

Start with Milestone A: create the audio-quality analyzer and add it to the turn pipeline as a non-blocking diagnostic result.

Rationale:

- It is low risk.
- It can be tested without hardware.
- It supports Speaker ID calibration, endpoint election, and placement reporting.
- It does not require committing to YAMNet, RNNoise, pyannote, or other heavier models yet.

## Open Questions For Discussion

- Should audio-quality failures ever block intent execution, or should they only explain degraded confidence at first?
- What threshold values should be configurable immediately versus learned from calibration data?
- Should medium-confidence speaker identity be allowed for music/profile personalization?
- Should profile learning be manual-confirmation only for the first release?
- Should passive ambient sampling live on the endpoint, backend, or both?
- What privacy language should the UI show for placement calibration?
- Is endpoint election a near-term requirement or should it wait until at least two physical endpoints are active in the same room?

