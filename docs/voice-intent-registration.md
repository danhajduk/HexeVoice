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
- `timer.status`: publishes a timer status request domain event.
- `timer.stop`: publishes a timer stop request domain event.
- `timer.cancel`: publishes a timer cancel request domain event.
- `timer.adjust_time`: publishes a signed timer adjustment request domain event.
- `voice.time.query`: Voice Node owned local response for "What is the time?" without an external dispatch side effect. Its reply uses spoken-form clock text, such as `four oh five PM`, so TTS does not read leading-zero minutes literally.
- `voice.debug.followup`: Voice Node owned follow-up test intent. Say "test follow up" to make the backend ask `Should I complete the follow-up test?`, then answer "yes" or "no" to exercise the follow-up listening path.
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

Assistant turns use the same registered-intent matcher. The timer create intent
queues the existing MQTT `timer.create_requested` request, including request and
sent timestamps, but MQTT publication runs off the voice response path so it
does not block STT, intent handling, or TTS. The timer status intent recognizes
phrases such as `how much time is left on the timer`, replies immediately with
`Checking the timer.`, and publishes `timer.status_requested` to the node-scoped
timer topic. The timer-owning node should respond on
`hexe/events/timer/status_succeeded` with `endpoint_id`, `session_id`,
`remaining_text` or `remaining_hhmmss`, and a timer `state`; HexeVoice announces
the remaining time back to the endpoint.

Timer stop and cancel intents follow the same MQTT request pattern. `timer.stop`
recognizes phrases such as `stop the timer`, `dismiss the timer`, and the short
global utterance `stop`; it publishes `timer.stop_requested` to
`hexe/nodes/<voice-node-id>/events/timer/stop_requested`. `timer.cancel`
recognizes phrases such as `cancel the timer`, `delete the timer`, and
`clear the timer`; it publishes `timer.cancel_requested` to
`hexe/nodes/<voice-node-id>/events/timer/cancel_requested`. Both events include
`endpoint_id`, `session_id`, `scope`, `heard_text`, `requested_at`, and a
correlation id. The timer-owning node remains responsible for selecting,
stopping, cancelling, or rejecting ambiguous timers.

`timer.adjust_time` recognizes phrases such as `add five minutes to the timer`,
`extend the timer by ten minutes`, `remove two minutes from the timer`, and
`take two minutes off the timer`. It publishes `timer.adjust_time_requested` to
`hexe/nodes/<voice-node-id>/events/timer/adjust_time_requested` with
`delta_seconds` signed positive for add and negative for remove, plus
`delta_hhmmss`, `delta_text`, `direction`, `endpoint_id`, `session_id`, `scope`,
`heard_text`, `requested_at`, and a correlation id. The timer-owning node applies
the delta to the active timer for that endpoint or rejects ambiguous requests.

Timer expiry is event-driven. HexeVoice subscribes to the promoted
`hexe/events/timer/completed` event stream and resolves the target endpoint from
`data.endpoint_id`, falling back to `data.device_id` only when present. A valid
timer completion event queues endpoint audio playback with timer metadata,
including `timer_id`, source node, due/completed timestamps, and Core dedupe
key. Duplicate promoted events are ignored so a single timer completion does not
ring twice.

Voice Node owned local responses, such as `voice.time.query`, answer directly
from the backend runtime.

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
