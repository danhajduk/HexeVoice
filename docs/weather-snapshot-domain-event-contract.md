# Shared prepared weather snapshot contract

## Consumer

HexeVoice Task 320 consumes Interaction-owned `weather.snapshot.updated` events.

## Required routing

- Shared topic: `hexe/events/weather/snapshot/updated`
- Event type: `weather.snapshot.updated`
- Schema version: `weather.snapshot.v1`

Interaction publishes the canonical node-originated envelope directly with QoS 1,
retain disabled, and its trusted node identity in `source.node_id`. The topic path
must match `event_type`, and component URLs must not contain credentials.

HexeVoice receives the shared topic through its normal governed event ACL and uses
only its own operational MQTT credentials.

## Optional background component

Interaction may include a `data.bg` component for its prepared weather background. The event carries only the reference metadata: `status`, `revision`, `image_url`, `sha256`, `content_type`, `width`, and `height`. URLs must be public HTTP(S) references without embedded credentials or sensitive query parameters.

HexeVoice selects a visual source in this order: ready or stale `radar`, ready or stale `bg`, then a locally generated blank background. It verifies the selected source checksum and center-crops it to the endpoint's 800x420 RGB888 content asset. The current Interaction weather backgrounds are 1672x941 and require no source-side resizing.

## Live verification

On 2026-09-18, the first live events were rejected because Interaction used its domain
schema version at the transport envelope and omitted the standard `source` object.
Interaction was corrected to publish node-domain envelope version `1`, with the weather
contract version retained in `data.schema_version`.

The nodes now use the direct shared topic `hexe/events/weather/snapshot/updated`.
HexeVoice consumes it using only its own operational MQTT credentials and persists the
same source node, event ID, snapshot ID, cache revision, prepared TTS revision, freshness,
and absent-radar state.

## Acceptance

1. A valid Interaction snapshot is delivered once on the shared topic.
2. The publisher preserves its trusted identity in `source.node_id`.
3. Replayed duplicate event IDs do not produce duplicate processing.
4. HexeVoice receives the event using only its own operational MQTT credentials.
