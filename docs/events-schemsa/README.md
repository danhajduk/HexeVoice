# Timer Event Schemas

This folder contains the MQTT/Core timer event contracts used by HexeVoice.

Files:

- `timer-request-event.schema.json`: events HexeVoice publishes for local timer intents.
- `timer-response-event.schema.json`: response and lifecycle events timer-owning nodes should publish back for HexeVoice to announce, cache, or alarm.
- `timer-common.schema.json`: shared JSON Schema definitions.

Notes:

- The folder name follows the requested path: `docs/events-schemsa`.
- HexeVoice publishes request events to `hexe/nodes/<voice-node-id>/events/timer/<event>`.
- HexeVoice consumes promoted timer responses from `hexe/events/timer/+`, including `hexe/events/timer/completed`.
- Timer-owning nodes should include `endpoint_id`, `timer_id`, `state`, owner/source metadata, and timing fields whenever available so HexeVoice can route commands and alarms across nodes.
