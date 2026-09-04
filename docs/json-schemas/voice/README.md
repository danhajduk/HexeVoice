# HexeVoice Schema Catalog

HexeVoice exposes the public Voice-node schema catalog at:

- `GET /api/schemas`
- `GET /api/schemas/voice/v1`
- `GET /api/schemas/voice/v1/{schema_name}`

The `voice/v1` catalog is intended for other Hexe nodes and tools that need to discover the contracts before calling this node. It currently includes the registered-intent request and reply contracts, the recognized-intent event contract, the reply-audio sidecar contract, and the voice event envelope.

Individual schema responses are served as `application/schema+json`. Unknown schema names return `404`.
