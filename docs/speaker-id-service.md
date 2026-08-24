# Speaker ID Service Contract

Task 232 defines the first contract for a local Speaker ID service owned by HexeVoice. Speaker ID is part of the Voice node provider stack, like STT, TTS, and wake-word services. It can run in-process or as a locally managed helper process/container, but it is not a separate trusted node.

Core sees Speaker ID through the Voice node capability declaration. HexeVoice owns the runtime configuration, service lifecycle, profile storage, local APIs, event publication, and per-turn integration.

Speaker ID handles biometric voice data. The default posture is local-only processing, explicit enrollment consent, no raw-audio retention, redacted events, and operator-controlled deletion.

## Goals

- Identify an enrolled speaker from a completed voice turn.
- Verify whether a voice sample matches a claimed profile.
- Enroll and manage speaker profiles with explicit consent.
- Keep HexeVoice functional when Speaker ID is disabled, unavailable, unauthorized, slow, or uncertain.
- Support multiple model/provider backends behind one service contract.
- Register Speaker ID with the same local supervisor/runtime patterns used by STT, TTS, and wake providers.

## Non-Goals

- Do not use speaker identity as authentication for security-sensitive actions in the first implementation.
- Do not store raw enrollment or turn audio by default.
- Do not publish speaker embeddings, voiceprints, or raw biometric features to MQTT, Core, logs, or UI payloads.
- Do not model Speaker ID as a separate Core node or require Core service resolution for local Voice turns.
- Do not require endpoint firmware changes for first-pass Speaker ID; backend-captured utterance audio is sufficient.

## Candidate Engines

The first implementation should benchmark these options behind one adapter API:

| Engine | Primary Fit | Notes |
| --- | --- | --- |
| SpeechBrain ECAPA-TDNN | Speaker verification and embedding extraction | SpeechBrain is a PyTorch speech toolkit with speaker-recognition support. The common ECAPA-TDNN VoxCeleb model is useful as a baseline, but model and dataset licenses must be checked before bundling. |
| WeSpeaker | Speaker embedding and verification | WeSpeaker is designed for speaker embedding learning and verification. The toolkit is Apache-2.0, while pretrained models can inherit dataset-specific licenses. |
| pyannote.audio | Diarization plus speaker embedding workflows | Useful when we later need diarization or multi-speaker segmentation. Some pretrained pipelines require gated model access even when the toolkit is open source. |
| NVIDIA NeMo | Speaker recognition on GPU-capable hosts | Good candidate for CUDA-capable deployments. NeMo Speech documents speaker identification and verification workflows. |

Provider selection, dependency installation, model download, license display, and benchmarks are implemented incrementally. The current adapter layer is intentionally import-safe: planned third-party engines appear in the catalog and report dependency/model metadata, but they do not import heavy optional packages until their runtime adapters are enabled.

## Runtime Adapter Layer

Task 233 added the first Speaker ID runtime boundary under `src/hexevoice/speaker_id/`.

- `hexevoice.speaker_id.adapters` defines normalized WAV loading, `SpeakerAudio`, `SpeakerEmbedding`, `SpeakerScore`, `SpeakerThresholds`, provider metadata, and the adapter protocol.
- `deterministic_signal` is the built-in CPU-only test adapter. It extracts a deterministic signal fingerprint from local 16-bit PCM WAV audio and can score two embeddings without external dependencies.
- `speechbrain_ecapa_tdnn`, `wespeaker`, `pyannote_audio`, and `nvidia_nemo_speaker` are cataloged as optional provider stubs with model, license, size, memory, CPU/CUDA, sample-rate, and enrollment-quality metadata.
- Missing optional providers report `missing_optional_dependency` through `status()` and raise `SpeakerIdProviderUnavailable` during embedding extraction instead of failing at import time.
- `scripts/benchmark-speaker-id.py` runs the same clips through selected providers and emits JSON with provider metadata, per-clip latency, embedding dimensions, and pairwise verification scores.

## Voice Capability Declaration

HexeVoice declares these task-family capabilities to Core when Speaker ID is enabled or installable:

- `voice.speaker.identify`
- `voice.speaker.verify`
- `voice.speaker.enroll`
- `voice.speaker.profile.manage`

Capability metadata should include local Voice API endpoints and provider/model status. Example declaration fragment:

```json
{
  "declared_capabilities": [
    "voice.speaker.identify",
    "voice.speaker.verify",
    "voice.speaker.enroll",
    "voice.speaker.profile.manage"
  ],
  "enabled_providers": ["speechbrain_ecapa_tdnn"],
  "capability_endpoints": {
    "voice.speaker.identify": {
      "method": "POST",
      "path": "/api/speaker-id/identify"
    },
    "voice.speaker.verify": {
      "method": "POST",
      "path": "/api/speaker-id/verify"
    },
    "voice.speaker.enroll": {
      "method": "POST",
      "path": "/api/speaker-id/enroll"
    },
    "voice.speaker.profile.manage": {
      "method": "GET",
      "path": "/api/speaker-id/profiles"
    }
  },
  "metadata": {
    "privacy_class": "biometric",
    "local_only_profiles": true,
    "raw_audio_retained_by_default": false
  }
}
```

HexeVoice does not ask Core to resolve Speaker ID for its own local voice turns. It calls its configured local Speaker ID service directly and reports the capability/provider state to Core as part of the Voice node.

## Privacy Policy

Speaker ID data is biometric data.

- Enrollment must record explicit consent with `consent_id`, `consent_version`, `consented_at`, and a human-readable consent label.
- Profiles are local-only by default under the HexeVoice Speaker ID runtime directory.
- Profiles store embeddings plus profile metadata; raw audio is deleted after embedding extraction unless `retain_audio=true` is explicitly enabled.
- Raw audio retention requires a separate operator-visible setting and must be reversible by deletion.
- MQTT/domain events must not include embeddings, raw audio, storage paths, or full transcripts.
- Public/log payloads use `speaker_public_id` and display labels only when policy permits.
- Deleting a profile removes embeddings, retained audio, enrollment samples, derived caches, and profile metadata.
- Speaker identity must not authorize protected actions until a later task defines multi-factor policy.

## API Contract

HexeVoice exposes the operator/runtime API under its normal API base URL. If the embedding engine runs as a helper process, these APIs remain the stable public surface and the backend calls the helper over a Unix domain socket by default. A localhost TCP base URL is allowed only as an explicit debug/fallback mode.

- `GET /api/health`
- `GET /api/speaker-id/status`
- `POST /api/speaker-id/enroll`
- `POST /api/speaker-id/identify`
- `POST /api/speaker-id/verify`
- `GET /api/speaker-id/profiles`
- `GET /api/speaker-id/profiles/{profile_id}`
- `DELETE /api/speaker-id/profiles/{profile_id}`

## Service Transport

The default backend-to-helper transport is HTTP/JSON over a Unix domain socket.

- Default socket path: `runtime/sockets/speaker-id.sock`
- Config override: `VOICE_SPEAKER_ID_SOCKET_PATH`
- Debug fallback config: `VOICE_SPEAKER_ID_BASE_URL`
- Debug fallback default: disabled
- Socket directory permissions: owner-only, mode `0700`
- Stale socket behavior: remove stale socket files on helper startup before binding
- LAN exposure: none by default; the helper must not open a TCP listener unless explicitly configured for diagnostics

The Voice backend remains the only normal public API surface. UI, setup, Core capability declarations, and operator scripts talk to HexeVoice on the node API; HexeVoice calls the local Speaker ID helper through the socket. If the helper later runs in Docker/Podman, the socket directory is bind-mounted into the container instead of exposing a service port.

### Health Response

```json
{
  "status": "ok",
  "ready": true,
  "version": "0.1.0",
  "provider": "speechbrain_ecapa_tdnn",
  "model_id": "speechbrain/spkrec-ecapa-voxceleb",
  "transport": "unix_socket",
  "socket_path": "runtime/sockets/speaker-id.sock",
  "profiles_count": 3,
  "last_error": null
}
```

### Status Response

```json
{
  "schema_version": 1,
  "configured": true,
  "enabled": true,
  "provider": "speechbrain_ecapa_tdnn",
  "model": {
    "model_id": "speechbrain/spkrec-ecapa-voxceleb",
    "embedding_dimensions": 192,
    "sample_rate_hz": 16000,
    "device": "cpu",
    "loaded": true
  },
  "thresholds": {
    "identify_min_confidence": 0.72,
    "identify_min_margin": 0.08,
    "verify_min_score": 0.75
  },
  "transport": {
    "mode": "unix_socket",
    "socket_path": "runtime/sockets/speaker-id.sock",
    "http_fallback_enabled": false
  },
  "privacy": {
    "retain_audio": false,
    "local_only_profiles": true,
    "event_redaction": "biometric_default"
  },
  "profiles_count": 3,
  "last_error": null
}
```

### Enrollment Request

`audio` is either a local HexeVoice artifact URL or multipart upload metadata from a UI workflow. The service implementation may accept multipart upload later, but the JSON contract below is the Voice backend path.

```json
{
  "schema_version": 1,
  "request_id": "speaker-enroll-20260824-001",
  "profile": {
    "display_name": "Dan",
    "speaker_public_id": "speaker_dan",
    "labels": ["household"]
  },
  "consent": {
    "consent_id": "consent-speaker-dan-001",
    "consent_version": "speaker-id-consent-v1",
    "consented_at": "2026-08-24T10:00:00Z",
    "consented_by": "operator",
    "retention_policy": "embeddings_only"
  },
  "samples": [
    {
      "sample_id": "sample-001",
      "audio_url": "http://10.0.0.100:9004/api/voice/artifacts/enroll-001.wav",
      "content_type": "audio/wav",
      "duration_ms": 4200,
      "sample_rate_hz": 16000
    }
  ],
  "options": {
    "retain_audio": false,
    "replace_existing": false
  }
}
```

### Enrollment Response

```json
{
  "schema_version": 1,
  "request_id": "speaker-enroll-20260824-001",
  "status": "enrolled",
  "profile_id": "spk_9f3a2d",
  "speaker_public_id": "speaker_dan",
  "display_name": "Dan",
  "provider": "speechbrain_ecapa_tdnn",
  "model_id": "speechbrain/spkrec-ecapa-voxceleb",
  "sample_count": 1,
  "quality": {
    "accepted": true,
    "min_duration_ms": 2500,
    "warnings": []
  },
  "created_at": "2026-08-24T10:00:05Z"
}
```

### Identify Request

```json
{
  "schema_version": 1,
  "request_id": "speaker-identify-20260824-001",
  "endpoint_id": "esp-box-1",
  "session_id": "voice-session-123",
  "audio": {
    "audio_url": "http://10.0.0.100:9004/api/voice/artifacts/session-123.wav",
    "content_type": "audio/wav",
    "duration_ms": 2800,
    "sample_rate_hz": 16000
  },
  "options": {
    "candidate_profile_ids": [],
    "min_confidence": 0.72,
    "min_margin": 0.08,
    "return_top_k": 3
  }
}
```

### Identify Response

```json
{
  "schema_version": 1,
  "request_id": "speaker-identify-20260824-001",
  "status": "identified",
  "speaker": {
    "profile_id": "spk_9f3a2d",
    "speaker_public_id": "speaker_dan",
    "display_name": "Dan"
  },
  "confidence": 0.86,
  "score": 0.82,
  "score_margin": 0.14,
  "unknown_reason": null,
  "provider": "speechbrain_ecapa_tdnn",
  "model_id": "speechbrain/spkrec-ecapa-voxceleb",
  "latency_ms": 142.7
}
```

Unknown or low-confidence responses use the same shape:

```json
{
  "schema_version": 1,
  "request_id": "speaker-identify-20260824-002",
  "status": "unknown",
  "speaker": null,
  "confidence": 0.41,
  "score": 0.58,
  "score_margin": 0.02,
  "unknown_reason": "below_min_confidence",
  "provider": "speechbrain_ecapa_tdnn",
  "model_id": "speechbrain/spkrec-ecapa-voxceleb",
  "latency_ms": 138.1
}
```

### Verify Request

```json
{
  "schema_version": 1,
  "request_id": "speaker-verify-20260824-001",
  "claimed_profile_id": "spk_9f3a2d",
  "endpoint_id": "esp-box-1",
  "session_id": "voice-session-124",
  "audio": {
    "audio_url": "http://10.0.0.100:9004/api/voice/artifacts/session-124.wav",
    "content_type": "audio/wav",
    "duration_ms": 3100,
    "sample_rate_hz": 16000
  },
  "options": {
    "min_score": 0.75
  }
}
```

### Verify Response

```json
{
  "schema_version": 1,
  "request_id": "speaker-verify-20260824-001",
  "status": "verified",
  "matched": true,
  "claimed_profile_id": "spk_9f3a2d",
  "confidence": 0.88,
  "score": 0.84,
  "provider": "speechbrain_ecapa_tdnn",
  "model_id": "speechbrain/spkrec-ecapa-voxceleb",
  "latency_ms": 121.4
}
```

### Profile List Response

```json
{
  "schema_version": 1,
  "profiles": [
    {
      "profile_id": "spk_9f3a2d",
      "speaker_public_id": "speaker_dan",
      "display_name": "Dan",
      "sample_count": 3,
      "provider": "speechbrain_ecapa_tdnn",
      "model_id": "speechbrain/spkrec-ecapa-voxceleb",
      "created_at": "2026-08-24T10:00:05Z",
      "updated_at": "2026-08-24T10:03:12Z",
      "retained_audio_count": 0
    }
  ]
}
```

### Profile Delete Response

```json
{
  "schema_version": 1,
  "status": "deleted",
  "profile_id": "spk_9f3a2d",
  "deleted_at": "2026-08-24T11:00:00Z",
  "deleted_items": {
    "embeddings": 3,
    "metadata_records": 1,
    "retained_audio_files": 0,
    "derived_cache_files": 2
  }
}
```

## Domain Events

Machine-readable schemas live in `docs/events-schemsa/`:

- `speaker-id-common.schema.json`
- `speaker-id-event.schema.json`

Event topics should follow:

- Domain topic: `hexe/events/speaker/<action>`
- Source topic: `hexe/events/nodes/{voice_node_id}/speaker/<action>`

Required event types:

- `speaker.enrollment.started`
- `speaker.enrollment.completed`
- `speaker.enrollment.failed`
- `speaker.identified`
- `speaker.verification.completed`
- `speaker.profile.deleted`
- `speaker.unknown`
- `speaker.low_confidence`

Example identified event:

```json
{
  "schema_version": 1,
  "event_id": "speaker-identified-voice-session-123",
  "event_type": "speaker.identified",
  "occurred_at": "2026-08-24T10:05:00Z",
  "source": {
    "kind": "node",
    "node_id": "node-voice-123",
    "component": "hexevoice.speaker_id",
    "node_type": "voice-node"
  },
  "subject": {
    "family": "speaker",
    "record_id": "spk_9f3a2d"
  },
  "data": {
    "request_id": "speaker-identify-20260824-001",
    "endpoint_id": "esp-box-1",
    "session_id": "voice-session-123",
    "status": "identified",
    "speaker_public_id": "speaker_dan",
    "display_name": "Dan",
    "confidence": 0.86,
    "score": 0.82,
    "score_margin": 0.14,
    "provider": "speechbrain_ecapa_tdnn",
    "model_id": "speechbrain/spkrec-ecapa-voxceleb"
  },
  "privacy": {
    "classification": "biometric",
    "redaction": "no_embedding_no_audio_no_transcript",
    "contains_biometric_template": false,
    "contains_raw_audio": false
  }
}
```

## HexeVoice Integration Rules

- Call Speaker ID only when enabled and when the local provider/helper service is healthy.
- Use the Unix socket helper transport by default; do not require or advertise a Speaker ID TCP port for normal local operation.
- Run Speaker ID and STT in parallel from the same completed VAD/utterance audio.
- Store only redacted speaker result metadata in voice session history.
- Include speaker context in assistant requests only when operator policy enables personalization.
- Never block local intents, timer control, or endpoint safety actions on Speaker ID.
- Treat `unknown`, `low_confidence`, timeout, unauthorized, and service unavailable as normal degraded states.
- Expose Speaker ID provider health in the same operational/status surfaces as STT, TTS, and wake.
- Include Speaker ID capabilities in Voice node capability declarations and governance refreshes when enabled or available.

## Parallel Turn Flow

Speaker ID must not sit in front of STT. After microphone capture and VAD produce a completed utterance audio segment, HexeVoice fans the same audio out to Speaker ID and STT at the same time:

```text
                   Speaker ID -> Dan / 0.91
                  /
Mic -> VAD -> audio
                  \
                   STT -> "turn on my office lights"

                both results enter

              Interaction Router
                    |
        speaker=Dan + transcribed text
```

The interaction router decides whether to wait for Speaker ID based on the matched intent, assistant route, or tool/action policy.

| Utterance | STT Result | Speaker ID Need | Router Behavior |
| --- | --- | --- | --- |
| "What is the time?" | `voice.time.query` | Not required | Execute the local intent as soon as STT identifies it. Speaker ID may finish later and be recorded as diagnostic metadata only. |
| "Turn on my office lights" | `home.office_lights.on` | Optional or policy-driven | Execute if policy allows household/endpoint context without identity; include speaker metadata if it is already available. |
| "What's on my calendar?" | personal assistant/calendar route | Required | Wait for Speaker ID up to the configured timeout. If identified, call the personal route with `speaker=Dan`. If unknown, ask a follow-up such as "Who is this?" |
| "Stop" during playback | `playback.stop` | Never required | Execute immediately and do not wait for Speaker ID. |

Speaker requirement is an explicit policy value, not inferred only from text:

- `speaker_identity_policy: "not_required"`: never wait for Speaker ID.
- `speaker_identity_policy: "use_if_ready"`: do not block, but attach speaker metadata if the result is already available.
- `speaker_identity_policy: "required"`: wait until Speaker ID returns identified/verified or timeout/unknown.
- `speaker_identity_policy: "forbidden"`: do not call or attach Speaker ID for this turn.

If Speaker ID is required and the result is `unknown`, `low_confidence`, timed out, disabled, or unauthorized, the router must not execute the personal action. It should enter a clarification state and ask who is speaking, then either start an enrollment/verification flow or fail closed according to policy.

The STT result remains authoritative for intent selection. Speaker ID only supplies speaker context and policy gating for routes that require identity.

## Open Decisions For Later Tasks

- Which provider becomes the default after local benchmark results.
- Whether profile embeddings should be encrypted at rest by default.
- Whether enrollment should require multiple samples before a profile becomes active.
- Whether diarization should be part of the first runtime or remain a pyannote-specific later extension.
- Exact default mapping from built-in intents and AI Node routes to `speaker_identity_policy`.

## External References

- SpeechBrain: https://github.com/speechbrain/speechbrain
- SpeechBrain ECAPA-TDNN model card: https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- WeSpeaker: https://github.com/wenet-e2e/wespeaker
- WeSpeaker pretrained model license note: https://github.com/wenet-e2e/wespeaker/blob/master/docs/pretrained.md
- pyannote.audio: https://github.com/pyannote/pyannote-audio
- NVIDIA NeMo Speech speaker recognition: https://docs.nvidia.com/nemo-framework/user-guide/24.12/nemotoolkit/asr/speaker_recognition/intro.html
