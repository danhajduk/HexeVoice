# Endpoint SD Media Delivery Contract

HexeVoice can deliver persistent media assets from the node to an endpoint SD card. The contract is endpoint-owned storage with node-owned validation and transfer orchestration.

## Destinations

Allowed destinations are fixed and may not be overridden by client paths:

- `picture`: `/sdcard/hexe/pictures`
- `sprite`: `/sdcard/hexe/sprites`
- `sound`: `/sdcard/hexe/sounds`

Transfer requests provide a filename only. Absolute paths, `..`, path separators, empty names, control characters, and hidden dotfile names are rejected.

## Media Types

`picture`

- Full-screen UI/background image.
- Runtime destination: `/sdcard/hexe/pictures`.
- Preferred endpoint format: raw RGB565, `320x240`, exactly `153600` bytes.
- Accepted upload extensions: `.rgb565`, `.png`, `.jpg`, `.jpeg`.
- PNG/JPEG uploads must be converted by the node before endpoint transfer.

`sprite`

- Smaller overlay/item image.
- Runtime destination: `/sdcard/hexe/sprites`.
- Preferred endpoint format: raw RGB565 plus metadata describing width and height.
- Accepted upload extensions: `.rgb565`, `.png`, `.jpg`, `.jpeg`, `.json`.
- Sprites must include `width` and `height` metadata before firmware activation.

`sound`

- Local cue or UI sound.
- Runtime destination: `/sdcard/hexe/sounds`.
- Preferred endpoint format: WAV PCM compatible with the firmware speaker path.
- Accepted upload extension: `.wav`.
- Required metadata: sample rate, channel count, bits per sample, and duration when known.

## Transfer Envelope

Backend-to-endpoint media transfer commands use the existing versioned voice event envelope:

```json
{
  "event_type": "endpoint.media.transfer",
  "direction": "backend_to_endpoint",
  "endpoint_id": "esp-box-1",
  "event_id": "evt-...",
  "schema_version": "voice.v1",
  "timestamp": "2026-04-27T00:00:00Z",
  "payload": {
    "request_id": "media-...",
    "media_type": "picture",
    "asset_id": "Logo 320x240",
    "filename": "Logo 320x240.rgb565",
    "destination": "picture",
    "download_url": "/api/endpoint/media/files/media-...",
    "content_type": "application/octet-stream",
    "size_bytes": 153600,
    "sha256": "hex-encoded-sha256",
    "overwrite": true,
    "activate": true,
    "metadata": {
      "pixel_format": "rgb565",
      "width": 320,
      "height": 240
    }
  }
}
```

## Endpoint Result Events

Firmware reports terminal transfer state with:

- `endpoint.media.transfer_ack` for accepted, writing, verified, and activated state.
- `endpoint.media.transfer_error` for rejected, download failed, write failed, checksum mismatch, unsupported media, missing SD card, full SD card, or activation failure.

Result payloads include `request_id`, `filename`, `destination`, `status`, `message`, and `error_code` when applicable.

## Validation Rules

The node validates before queueing a transfer:

- destination is one of `picture`, `sprite`, or `sound`
- filename is safe and matches the media type
- file size is within type-specific limits
- checksum is computed from the exact endpoint payload bytes
- converted RGB565 pictures are exactly `320 * 240 * 2` bytes
- sound files are WAV PCM and within cue size limits
- overwrite policy is explicit

The firmware validates again before final rename:

- SD card is mounted
- target directory exists or can be created
- temporary write succeeds
- byte count matches `size_bytes`
- checksum matches `sha256`
- final path is inside the expected destination directory

Firmware writes transfer downloads to a dot-prefixed temporary file in the target directory, verifies size and SHA-256, then renames into place. The worker acknowledges `accepted`, `started`, and `succeeded` states through `command.ack`; terminal failures are reported through `command.error`.

## Endpoint Inventory

Firmware includes a bounded SD inventory in its heartbeat capabilities under `storage.media_inventory`. Each media directory reports visible files with `filename` and `size_bytes`; hidden dotfiles and temporary transfer files are skipped. Inventory lists are capped per directory and set `truncated: true` when the endpoint sees more files than the heartbeat reports.

The node persists the latest heartbeat inventory with the endpoint registry and exposes it through `GET /api/endpoint/media/inventory/{endpoint_id}`.

## Current Size Limits

- picture: `153600` bytes after conversion
- sprite: up to `524288` bytes
- sound: up to `5242880` bytes

These limits are intentionally conservative for the first endpoint media-transfer implementation.

## Node API Surface

Current backend routes:

- `GET /api/endpoint/media` lists node-staged media assets.
- `POST /api/endpoint/media` accepts JSON/base64 media uploads and stores the endpoint-ready payload.
- `GET /api/endpoint/media/{asset_id}` returns asset metadata.
- `GET /api/endpoint/media/inventory/{endpoint_id}` returns the latest SD inventory reported by endpoint heartbeat.
- `DELETE /api/endpoint/media/{asset_id}` removes a staged asset.
- `GET /api/endpoint/media/files/{asset_id}` serves the endpoint-ready payload bytes.
- `POST /api/endpoint/media/{asset_id}/deliver` sends an `endpoint.media.transfer` command to the connected endpoint.

The first upload API intentionally uses JSON/base64 payloads so it does not require a multipart parser dependency. Dashboard upload flows can wrap local file reads into this request shape.
