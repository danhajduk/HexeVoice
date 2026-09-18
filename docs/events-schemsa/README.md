# Timer Event Schemas

This folder contains the MQTT/Core timer event contracts used by HexeVoice.

Files:

- `timer-request-event.schema.json`: events HexeVoice publishes for local timer intents.
- `timer-response-event.schema.json`: response and lifecycle events timer-owning nodes should publish back for HexeVoice to announce, cache, or alarm.
- `timer-common.schema.json`: shared JSON Schema definitions.

Notes:

- The folder name follows the requested path: `docs/events-schemsa`.
- HexeVoice publishes request events directly to `hexe/events/timer/<event>` with QoS 1 and retain disabled.
- HexeVoice consumes canonical timer responses from `hexe/events/timer/+`, including `hexe/events/timer/completed`.
- Every topic path after `hexe/events/` must equal `event_type` with dots replaced by slashes. The canonical envelope uses `event_id` for deduplication and preserves the trusted node identity in `source.node_id`.
- Domain events are limited to 65,536 bytes and must not contain secrets, credentials, tokens, attachments, raw message bodies, or private raw content.
- Raw utterances and generated reply text/audio stay on the private voice path. The compatibility `recognized_text` field contains only the normalized command identifier, never the utterance transcript.
- Timer-owning nodes should include `endpoint_id`, `timer_id`, `state`, owner/source metadata, and timing fields whenever available so HexeVoice can route commands and alarms across nodes.
