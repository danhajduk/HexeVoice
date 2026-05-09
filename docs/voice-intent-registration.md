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
- `voice.time.query`: Voice Node owned local response for "What is the time?" without an external dispatch side effect.

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
