# Voice Event Envelope

HexeVoice endpoint transport messages use the versioned `VoiceEventEnvelope` defined in
`src/hexevoice/voice/contracts.py`.

Required envelope fields:

- `event_type`
- `event_id`
- `session_id`
- `endpoint_id`
- `direction`
- `timestamp`
- `schema_version`
- `payload`

Optional envelope fields:

- `sequence`

The current schema version is `hexevoice.voice.event.v1`. The backend still accepts legacy firmware messages that omit
`event_id` or `schema_version`; missing values are filled server-side. Unknown schema versions are rejected by backend
validation and surfaced through `session.error` plus `/api/voice/status` `event_diagnostics`.

JSON examples for this contract are stored in `docs/voice-event-envelope/`:

- `endpoint-session-start.example.json`
- `backend-volume-command.example.json`
- `endpoint-command-ack.example.json`
- `endpoint-command-error.example.json`

The Task 061 schema set is stored in `docs/task-061-json-schemas/`.

Endpoint command acknowledgements use `command.ack`. Endpoint-side command failures use `command.error`. Both are
accepted endpoint-to-backend events and are exposed in `/api/voice/status` as `last_command_ack`,
`last_command_error`, and `event_diagnostics`.
