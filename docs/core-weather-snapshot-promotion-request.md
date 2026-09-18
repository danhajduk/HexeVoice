# Core prepared weather snapshot promotion

## Consumer

HexeVoice Task 320 consumes Interaction-owned `weather.snapshot.updated` events.

## Required routing

- Source topic: `hexe/nodes/<interaction_node_id>/events/weather/snapshot/updated`
- Governed consumer topic: `hexe/events/weather/snapshot/updated`
- Event type: `weather.snapshot.updated`
- Schema version: `weather.snapshot.v1`

Core must validate the publishing node identity against the source topic and preserve
`source_node_id`, `event_id`, `occurred_at`, and the complete `data` payload when it
promotes the event. Promotion must not add credentials to component URLs or merge
components from separate snapshot generations.

HexeVoice's operational MQTT identity must receive the promoted topic through its
normal governed event ACL. It must not receive Interaction's private node topic and
must not reuse Interaction's MQTT credentials.

## Live verification

On 2026-09-18, the first live events were rejected because Interaction used its domain
schema version at the transport envelope and omitted the standard `source` object.
Interaction was corrected to publish node-domain envelope version `1`, with the weather
contract version retained in `data.schema_version`.

Core then accepted and promoted the event to `hexe/events/weather/snapshot/updated`.
HexeVoice consumed it using only its own operational MQTT credentials and persisted the
same source node, event ID, snapshot ID, cache revision, prepared TTS revision, freshness,
and absent-radar state.

## Acceptance

1. A valid Interaction snapshot is delivered once on the governed consumer topic.
2. A source-topic/node-identity mismatch is rejected before promotion.
3. Replayed duplicate event IDs do not produce duplicate promoted events.
4. HexeVoice receives the event using only its own operational MQTT credentials.
