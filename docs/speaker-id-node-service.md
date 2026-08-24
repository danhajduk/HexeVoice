# Speaker ID Node Service Contract

Task 232 defines the first contract for a local Speaker ID node service. This service is separate from HexeVoice: HexeVoice captures voice turns, asks Core to resolve a Speaker ID capability, and calls the selected node service when policy allows it.

Speaker ID handles biometric voice data. The default posture is local-only processing, explicit enrollment consent, no raw-audio retention, redacted events, and operator-controlled deletion.

## Goals

- Identify an enrolled speaker from a completed voice turn.
- Verify whether a voice sample matches a claimed profile.
- Enroll and manage speaker profiles with explicit consent.
- Keep HexeVoice functional when Speaker ID is disabled, unavailable, unauthorized, slow, or uncertain.
- Support multiple model/provider backends behind one service contract.

## Non-Goals

- Do not use speaker identity as authentication for security-sensitive actions in the first implementation.
- Do not store raw enrollment or turn audio by default.
- Do not publish speaker embeddings, voiceprints, or raw biometric features to MQTT, Core, logs, or UI payloads.
- Do not hardcode Speaker ID host/port in HexeVoice. Use Core service resolution.

## Candidate Engines

The first implementation should benchmark these options behind one adapter API:

| Engine | Primary Fit | Notes |
| --- | --- | --- |
| SpeechBrain ECAPA-TDNN | Speaker verification and embedding extraction | SpeechBrain is a PyTorch speech toolkit with speaker-recognition support. The common ECAPA-TDNN VoxCeleb model is useful as a baseline, but model and dataset licenses must be checked before bundling. |
| WeSpeaker | Speaker embedding and verification | WeSpeaker is designed for speaker embedding learning and verification. The toolkit is Apache-2.0, while pretrained models can inherit dataset-specific licenses. |
| pyannote.audio | Diarization plus speaker embedding workflows | Useful when we later need diarization or multi-speaker segmentation. Some pretrained pipelines require gated model access even when the toolkit is open source. |
| NVIDIA NeMo | Speaker recognition on GPU-capable hosts | Good candidate for CUDA-capable deployments. NeMo Speech documents speaker identification and verification workflows. |

Provider selection, dependency installation, model download, license display, and benchmarks are Task 233 scope. This task only defines the common service shape.

## Core Capabilities

The Speaker ID node declares these task-family capabilities to Core:

- `voice.speaker.identify`
- `voice.speaker.verify`
- `voice.speaker.enroll`
- `voice.speaker.profile.manage`

HexeVoice requests `voice.speaker.identify` through Core:

```json
{
  "node_id": "node-voice-123",
  "task_family": "voice.speaker.identify",
  "type": "voice",
  "task_context": {
    "type": "voice",
    "endpoint_id": "esp-box-1",
    "privacy_class": "biometric"
  },
  "preferred_provider": "speechbrain"
}
```

Core returns a candidate with `provider_api_base_url`, `execution_endpoint_url`, `auth_mode`, and `required_scopes`. HexeVoice must use the resolved candidate and Core-issued authorization flow when authorization is required.

## Privacy Policy

Speaker ID data is biometric data.

- Enrollment must record explicit consent with `consent_id`, `consent_version`, `consented_at`, and a human-readable consent label.
- Profiles are local-only by default under the Speaker ID service runtime directory.
- Profiles store embeddings plus profile metadata; raw audio is deleted after embedding extraction unless `retain_audio=true` is explicitly enabled.
- Raw audio retention requires a separate operator-visible setting and must be reversible by deletion.
- MQTT/domain events must not include embeddings, raw audio, storage paths, or full transcripts.
- Public/log payloads use `speaker_public_id` and display labels only when policy permits.
- Deleting a profile removes embeddings, retained audio, enrollment samples, derived caches, and profile metadata.
- Speaker identity must not authorize protected actions until a later task defines multi-factor policy.

## API Contract

All endpoints are rooted at the resolved Speaker ID service base URL. Exact paths are intentionally simple and node-local:

- `GET /api/health`
- `GET /api/speaker-id/status`
- `POST /api/speaker-id/enroll`
- `POST /api/speaker-id/identify`
- `POST /api/speaker-id/verify`
- `GET /api/speaker-id/profiles`
- `GET /api/speaker-id/profiles/{profile_id}`
- `DELETE /api/speaker-id/profiles/{profile_id}`

### Health Response

```json
{
  "status": "ok",
  "ready": true,
  "version": "0.1.0",
  "provider": "speechbrain_ecapa_tdnn",
  "model_id": "speechbrain/spkrec-ecapa-voxceleb",
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

`audio` is either a pullable URL from HexeVoice or multipart upload metadata from a UI workflow. The service implementation may accept multipart upload later, but the JSON contract below is the Core/Voice path.

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
- Source topic: `hexe/events/nodes/{node_id}/speaker/<action>`

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
    "node_id": "node-speaker-id",
    "component": "speaker-id.service",
    "node_type": "speaker-id-node"
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

- Call Speaker ID only when enabled and when Core resolves a candidate for `voice.speaker.identify`.
- Run Speaker ID after utterance capture/STT audio preparation and before assistant routing when the latency budget allows.
- Store only redacted speaker result metadata in voice session history.
- Include speaker context in assistant requests only when operator policy enables personalization.
- Never block local intents, timer control, or endpoint safety actions on Speaker ID.
- Treat `unknown`, `low_confidence`, timeout, unauthorized, and service unavailable as normal degraded states.

## Open Decisions For Later Tasks

- Which provider becomes the default after local benchmark results.
- Whether profile embeddings should be encrypted at rest by default.
- Whether enrollment should require multiple samples before a profile becomes active.
- Whether speaker identity may influence assistant personalization automatically or only after operator opt-in.
- Whether diarization should be part of the first runtime or remain a pyannote-specific later extension.

## External References

- SpeechBrain: https://github.com/speechbrain/speechbrain
- SpeechBrain ECAPA-TDNN model card: https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- WeSpeaker: https://github.com/wenet-e2e/wespeaker
- WeSpeaker pretrained model license note: https://github.com/wenet-e2e/wespeaker/blob/master/docs/pretrained.md
- pyannote.audio: https://github.com/pyannote/pyannote-audio
- NVIDIA NeMo Speech speaker recognition: https://docs.nvidia.com/nemo-framework/user-guide/24.12/nemotoolkit/asr/speaker_recognition/intro.html
