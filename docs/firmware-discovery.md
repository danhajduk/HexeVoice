# Firmware Endpoint Discovery

Created: 08/23/2026

Firmware endpoint discovery uses a local UDP offer flow so new endpoints can
find a HexeVoice node without compiling a fixed backend IP into the common path.
Static endpoint YAML remains the fallback for locked-down networks.

## Discovery Flow

1. After Wi-Fi is connected, firmware checks `kEndpointDiscoveryEnabled`.
2. If no runtime provisioning is already persisted, firmware broadcasts a JSON
   discovery request to `255.255.255.255:<discovery_udp_port>`.
3. The node's UDP listener validates the request and returns a discovery offer.
4. Firmware persists the offered backend host, HTTP port, WebSocket port, TLS
   mode, and endpoint identity through the NVS provisioning settings.
5. Heartbeat and voice WebSocket connections use the discovered settings.

The discovery schema is `hexevoice.endpoint.discovery.v1`.

## Node Offer Payload

The node returns:

- `accepted`
- `endpoint_id`
- `pairing_state`
- `backend_host`
- `http_port`
- `ws_port`
- `use_tls`
- `heartbeat_path`
- `voice_ws_path`
- `server_time`
- optional `reason`

`pairing_state` is:

- `paired` for a new or compatible endpoint identity.
- `stale_recovered` when an existing registry record was stale and is reclaimed
  by the discovering endpoint.
- `duplicate_online` when the same endpoint id is already online from another
  IP address.
- `invalid_request` for malformed UDP discovery packets.

## Configuration

Backend environment:

- `ENDPOINT_DISCOVERY_UDP_ENABLED=true`
- `ENDPOINT_DISCOVERY_UDP_HOST=0.0.0.0`
- `ENDPOINT_DISCOVERY_UDP_PORT=9134`
- `ENDPOINT_DISCOVERY_ADVERTISE_HOST=<lan-host>`
- `ENDPOINT_DISCOVERY_USE_TLS=false`

Firmware endpoint YAML:

```yaml
behavior:
  discovery_enabled: true
  discovery_udp_port: 9134
```

`firmware/config/endpoint.yaml` still provides static host/port values. Those
values are used if discovery is disabled, unavailable, or reset by the operator.

## Operator Visibility

Endpoint heartbeat capabilities report provisioning discovery state under
`capabilities.provisioning.discovery`, including whether discovery is enabled,
the UDP port, whether the endpoint has attempted discovery, and the latest
status (`paired`, `timed_out`, `rejected`, `invalid_offer`, `invalid_json`,
`send_failed`, `socket_failed`, or `persist_failed`). The dashboard shows those
fields in the Endpoint Settings panel.

For simulation and constrained networks, `POST /api/endpoint/discovery/offer`
exposes the same offer logic over HTTP.
