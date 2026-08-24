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
- `ENDPOINT_MDNS_ENABLED=true`
- `ENDPOINT_MDNS_SERVICE_NAME=HexeVoice`
- `ENDPOINT_MDNS_SERVICE_TYPE=_hexevoice._tcp.local.`
- `ENDPOINT_MDNS_ADVERTISE_HOST=<lan-ip>`

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

## mDNS Service Advertisement

The node can also publish an optional mDNS service for operator tooling:
`_hexevoice._tcp.local.`. The mDNS service port is the API port. TXT metadata
includes:

- `api_url`
- `ui_url`
- `api_port`
- `ui_port`
- `node_id`
- `node_type`
- `tls`
- `advertised_ip`

The advertised `api_url` and `ui_url` are always built with a LAN IP address,
not `hexe.local`, so tools can connect directly to the node at values such as
`http://10.0.0.100:9004` and `http://10.0.0.100:8084`. Set
`ENDPOINT_MDNS_ADVERTISE_HOST` to pin the address; otherwise the backend tries
`ENDPOINT_DISCOVERY_ADVERTISE_HOST`, `PUBLIC_API_BASE_URL`, and finally the
host's active LAN address.

mDNS uses the optional Python `zeroconf` package. If `zeroconf` is missing or
the multicast registration fails, the backend API continues to start and
reports the failure at `GET /api/endpoint/discovery/status`.

Manual validation:

```bash
avahi-browse -rt _hexevoice._tcp
curl http://10.0.0.100:9004/api/endpoint/discovery/status
```
