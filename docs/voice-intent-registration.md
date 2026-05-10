# Voice Intent Registration

Voice Node keeps registered intents in local node state, mirroring the AI Node prompt registration pattern: register a named contract, version it, manage lifecycle locally, and declare capability endpoints so another node can discover how to call it through Core service resolution.

## Capabilities

Voice Node declares:

- `voice.intent.register`
- `voice.intent.list`
- `voice.intent.dispatch`

The capability declaration includes HTTP endpoint metadata for registration, listing, lookup, lifecycle transition, review, and dry-run dispatch matching.

## Storage

The registry is stored at `VOICE_INTENT_REGISTRY_PATH`, or `voice_intents.json` beside the onboarding state when `ONBOARDING_STATE_PATH` is configured. Built-in Voice Node intents are seeded once and can be disabled, reviewed, or retired like any other intent.

Seeded built-ins:

- `timer.create`: publishes the existing timer create domain event.
- `voice.time.query`: Voice Node owned local response for "What is the time?" without an external dispatch side effect. Its reply uses spoken-form clock text, such as `four oh five PM`, so TTS does not read leading-zero minutes literally.
- `voice.confirm.yes` and `voice.confirm.no`: contextual Voice Node owned responses for pending follow-ups. They only match while the endpoint or session has an active follow-up; standalone "yes" or "no" is ignored by the local intent matcher.

## Register

`POST /api/voice/intents`

```json
{
  "intent_id": "kitchen.status",
  "intent_name": "Kitchen status",
  "service_id": "voice.local_intents",
  "version": "v1",
  "status": "active",
  "definition": {
    "utterance_examples": ["kitchen status"],
    "dispatch": {
      "type": "local_response",
      "command": "kitchen.status"
    },
    "response": {
      "reply_text": "Kitchen status accepted."
    },
    "matcher": {
      "type": "exact_example"
    }
  },
  "metadata": {
    "source": "setup_ui"
  }
}
```

## List

`GET /api/voice/intents`

Returns `registered_count`, `active_count`, `updated_at`, and the current intent records.

## Lifecycle

`POST /api/voice/intents/{intent_id}/lifecycle`

```json
{
  "status": "disabled",
  "reason": "operator_pause"
}
```

Active intents can match assistant turns. Disabled, retired, restricted, probation, review-due, or expired intents remain visible but do not dispatch.

## Dispatch Dry Run

`POST /api/voice/intents/dispatch`

```json
{
  "endpoint_id": "kiosk_kitchen_1",
  "text": "kitchen status"
}
```

Response:

```json
{
  "matched": true,
  "intent_id": "kitchen.status",
  "command": "kitchen.status",
  "slots": {},
  "reply_text": "Kitchen status accepted.",
  "provider_id": "registered_intent"
}
```

Assistant turns use the same registered-intent matcher. The timer intent still queues the existing MQTT timer request, including request and sent timestamps, but MQTT publication runs off the voice response path so it does not block STT, intent handling, or TTS. Voice Node owned local responses, such as `voice.time.query`, answer directly from the backend runtime.

## Conversation Follow-Ups

An intent can declare a short-lived yes/no follow-up by adding a `followup`
object, or `conversation.followup`, to its definition:

```json
{
  "definition": {
    "utterance_examples": ["delete cache"],
    "dispatch": {"type": "local_response", "command": "debug.delete_cache"},
    "reply": {"text_template": "Delete cache?"},
    "followup": {
      "required": true,
      "prompt": "Delete cache?",
      "yes_reply_text": "Deleting cache.",
      "no_reply_text": "Leaving cache alone.",
      "ttl_seconds": 30
    },
    "matcher": {"type": "exact_example"}
  }
}
```

The pending follow-up is scoped to the endpoint and session, expires after 5 to
300 seconds, and is cleared after the first `voice.confirm.yes`,
`voice.confirm.no`, or a different local intent. For endpoint voice sessions,
the backend waits for TTS playback to complete, then sends a follow-up listening
state and keeps the same audio stream open for 10 seconds. If no follow-up audio
arrives in that window, the backend sends `session.cancelled` with the message
`canceled` and returns the endpoint to idle.
