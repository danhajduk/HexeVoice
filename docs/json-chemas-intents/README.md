# Registered Intent JSON Schemas

This folder documents the Voice Node registered-intent contract used by other nodes and tools before they call the intent registration API.

Schemas:

- `intent-registration.schema.json`: request shape for registering an intent, including matching, extraction, dispatch, and reply audio options.
- `voice-intent-recognized-event.schema.json`: reusable `voice.intent.recognized` event emitted after a valid intent match.
- `reply-audio-sidecar.schema.json`: sidecar JSON stored beside generated intent reply audio.

Examples:

- `examples/timer-create.intent.json`
- `examples/kitchen-status-audio.intent.json`

The directory name intentionally follows the task request spelling: `json-chemas-intents`.
