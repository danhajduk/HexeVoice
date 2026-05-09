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

The current schema version is `hexevoice.voice.event.v1`. Firmware and backend messages are expected to emit the full
versioned envelope directly, including `event_id`, `timestamp`, `schema_version`, and `payload`. Unknown schema versions
are rejected by backend validation and surfaced through `session.error` plus `/api/voice/status` `event_diagnostics`;
firmware ignores malformed backend-to-endpoint envelopes instead of applying them.

JSON examples for this contract are stored in `docs/voice-event-envelope/`:

- `endpoint-session-start.example.json`
- `backend-volume-command.example.json`
- `endpoint-command-ack.example.json`
- `endpoint-command-error.example.json`

The Task 061 schema set is stored in `docs/task-061-json-schemas/`.

Endpoint command acknowledgements use `command.ack`. Endpoint-side command failures use `command.error`. Both are
accepted endpoint-to-backend events and are exposed in `/api/voice/status` as `last_command_ack`,
`last_command_error`, and `event_diagnostics`.

For backend-to-endpoint commands that include a `request_id`, firmware first sends `command.ack` with
`status: "accepted"` and `message: "OK"` once the command envelope is received. It then sends any command-specific
progress, success, or error event needed to describe the actual work.
