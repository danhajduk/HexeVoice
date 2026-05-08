# Registered Intent JSON Schemas

This folder documents the Voice Node registered-intent contract used by other nodes and tools before they call the intent registration API.

Schemas:

- `intent-registration.schema.json`: request shape for registering an intent, including matching, extraction, dispatch, and reply audio options.
- `intent-reply.schema.json`: reusable `definition.reply` shape for intent creation, including reply text, generated audio, long-lived audio, and optional audio variants.
- `voice-intent-recognized-event.schema.json`: reusable `voice.intent.recognized` event emitted after a valid intent match.
- `reply-audio-sidecar.schema.json`: sidecar JSON stored beside generated intent reply audio.

Examples:

- `examples/timer-create.intent.json`
- `examples/kitchen-status-audio.intent.json`
- `examples/timer-cancel-long-lived-audio.intent.json`

Intent reply audio defaults to short-lived artifacts. Set `definition.reply.audio.lifetime` to `long_lived` for canned replies that should survive the five-minute cleanup window.
