# Firmware Provisioning

Created: 08/23/2026

Firmware keeps build-time endpoint config as the recovery default, but normal
operator setup can now persist endpoint provisioning in NVS.

## Persisted Settings

`firmware/components/endpoint_runtime/system/settings.cpp` owns the active runtime settings:

- endpoint id
- display name
- backend host
- backend HTTP port
- backend WebSocket port
- TLS enablement
- Wi-Fi SSID
- Wi-Fi password

On boot, firmware loads generated defaults from `endpoint_config.h` and local
Wi-Fi secrets, then overlays any NVS provisioning values. Reset erases only the
provisioning keys and returns to the build-time defaults.

## Command Contract

The node sends provisioning through the existing backend-to-endpoint command
envelope.

`endpoint.provisioning.apply` accepts these optional payload fields:

- `endpoint_id`
- `display_name`
- `backend_host`
- `http_port`
- `ws_port`
- `use_tls`
- `wifi_ssid`
- `wifi_password`

Omitted fields keep the endpoint's current active value. Firmware validates that
`endpoint_id` and `backend_host` are non-empty and that both ports are in the
1-65535 range before writing to NVS.

`endpoint.provisioning.reset` erases persisted provisioning and reloads
build-time defaults. Network and backend route changes are applied on reboot or
after reconnecting the relevant firmware tasks.

## Operator Paths

Display-capable firmware profiles and headless profiles use the same persisted
settings contract:

- ESP-BOX-3 reports provisioning status in heartbeat capabilities and can be
  configured from the operator dashboard's Endpoint Settings panel.
- Home Assistant Voice PE reports the same heartbeat capability fields and uses
  the same operator dashboard/API command path, without requiring a local screen.
- Headless automation can call `POST /api/endpoint/provisioning/apply` or
  `POST /api/endpoint/provisioning/reset`.

The HTTP API uses the connected target endpoint's `endpoint_id` to route the
command. For changing the device's future identity, the apply request uses
`provisioned_endpoint_id`, which the backend maps to the firmware command
payload field named `endpoint_id`.

## Recovery

Keep `firmware/config/endpoint.yaml` and `firmware/components/endpoint_runtime/secrets/wifi_secrets.h`
available for lab builds and recovery images. If runtime provisioning is reset
or unavailable, those generated defaults remain sufficient for a device to
reconnect to the node.
