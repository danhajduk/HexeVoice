# Firmware BLE Onboarding Integration

Status: Task 291 BLE onboarding validation coverage. This document records how
HexeVoice implements endpoint BLE onboarding against the current
Core/Supervisor `ble.provision_wifi` contract without inventing a parallel host
Bluetooth API.

Core/Supervisor sources checked:

- Core contract: `/home/dan/hexe/hexe/docs/core/ble-onboarding-contract.md`
- Core hardware access model: `/home/dan/hexe/hexe/core/backend/app/system/hardware.py`
- Core node hardware access docs: `/home/dan/hexe/hexe/docs/core/node-hardware-access.md`
- Core schema: `/home/dan/hexe/hexe/docs/json_schema/ble_onboarding_provisioning.schema.json`
- Supervisor broker route: `/api/supervisor/hardware/bluetooth/ble/provision-wifi`
- Supervisor broker models/tests:
  `/home/dan/hexe/hexe/supervisor/backend/app/supervisor/models.py` and
  `/home/dan/hexe/hexe/supervisor/backend/tests/test_supervisor_bluetooth_broker.py`

## Current Contract

Core/Supervisor already define the host-side contract HexeVoice needs for the
next implementation tasks:

| Item | Current value |
| --- | --- |
| Operation | `ble.provision_wifi` |
| Lease scope | `hardware.bluetooth.ble.provision_wifi` |
| Contract version | `1.0` |
| Envelope schema version | `1.0` |
| Voice payload schema id | `hexe.voice_node.wifi_backend.v1` |
| Encryption algorithm | `aes-256-gcm` |
| Key agreement | `x25519-hkdf-sha256` |
| GATT service UUID | `7f9c0000-5f04-4d8b-9a46-7c0f7a100000` |
| Device identity characteristic | `7f9c0001-5f04-4d8b-9a46-7c0f7a100000` |
| Pairing nonce / claim code characteristic | `7f9c0002-5f04-4d8b-9a46-7c0f7a100000` |
| Provisioning status characteristic | `7f9c0003-5f04-4d8b-9a46-7c0f7a100000` |
| Encrypted credential write characteristic | `7f9c0004-5f04-4d8b-9a46-7c0f7a100000` |
| Ack/error characteristic | `7f9c0005-5f04-4d8b-9a46-7c0f7a100000` |

Core exposes the hardware access request schema at
`GET /api/system/nodes/hardware/access-requests/schema` and the Voice
provisioning payload schema at
`GET /api/system/nodes/hardware/ble/provisioning/schemas/voice`.

`ble.provision_wifi` access requests require a provisioning context with:

- `contract_version`
- `onboarding_session_id`
- `target_node_id`
- `node_profile_id`
- `payload_schema_id`
- `endpoint_ephemeral_public_key`
- `sequence`
- `expires_at`
- either `pairing_nonce` or `claim_code_ref`

Core binds this context into the hardware lease token and validates the same
context when Supervisor validates the lease. The live Core/Supervisor code
checks request, token, and broker-body consistency for onboarding session,
target node, node profile, payload schema, pairing nonce, and claim-code
reference.

## Ownership Boundary

| Owner | Responsibility |
| --- | --- |
| Core | requester authentication, Bluetooth access policy, hardware request records, pending approval under `ask`, lease issuance, lease validation, provisioning context binding, and audit state |
| Supervisor | host Bluetooth adapter selection, lease enforcement, broker route, Voice payload validation, response redaction, and physical BLE/GATT client backend |
| HexeVoice backend/operator flow | discover Core schemas, create lease request, handle `denied`/`pending`/`granted`, send bounded broker call to Supervisor, redact local state, report progress, and hand off to normal node onboarding after Wi-Fi joins |
| Hexe endpoint firmware | advertise the BLE peripheral, expose the GATT characteristics, validate/decrypt/apply the provisioning envelope, persist settings locally, reconnect Wi-Fi/backend, and stop advertising after success or timeout |
| Recovery app | advertise the canonical BLE service on supported recovery boards, provide an explicit local recovery-safe fallback when Core is offline, and report Core-governed encrypted provisioning as endpoint-app only |

HexeVoice must not request raw host Bluetooth, BlueZ DBus, `/sys`, privileged
containers, or unrestricted host command access.

Presence of a Bluetooth adapter is not authorization; HexeVoice must lease the
exact operation first.

## Deployment Roles

HexeVoice can participate in two roles:

- Target endpoint role: every BLE-capable endpoint board advertises the Hexe BLE
  onboarding peripheral when unprovisioned, explicitly placed in provisioning
  mode, or booted into recovery provisioning.
- Trusted requester role: a trusted HexeVoice backend may orchestrate Core
  hardware access and call the Supervisor broker to provision a nearby endpoint.

Most deployments need both roles: firmware is the target, while the HexeVoice
backend provides the operator workflow. If another trusted operator client owns
the requester role, HexeVoice firmware still implements only the target-side
GATT service and normal post-Wi-Fi onboarding remains unchanged.

## Payload To Firmware Settings Mapping

The current Voice payload maps cleanly to
`firmware/components/endpoint_runtime/system/settings.cpp` and the recovery
runtime's compatible NVS keys:

| Core Voice payload field | Firmware setting/NVS key | Notes |
| --- | --- | --- |
| `wifi_ssid` | `wifi_ssid` | Required, 1-32 characters |
| `wifi_password` | `wifi_password` | Optional/null only for open networks; must never be logged |
| `backend_host` | `backend_host` | Required, 1-253 characters |
| `http_port` | `http_port` | Required, 1-65535 |
| `ws_port` | `ws_port` | Required, 1-65535 |
| `use_tls` | `use_tls` | Required boolean, default true in Core schema |
| `endpoint_name` | `endpoint_id` | Optional stable endpoint key; use current/generated id when omitted |
| `display_name` | `display_name` | Optional operator-visible name |
| successful apply | `provisioned` | Set to `1` after settings validate and persist |

Firmware must write through the existing endpoint provisioning settings path
rather than introducing a BLE-only credential store. That keeps BLE
provisioning, backend-command provisioning, and recovery provisioning aligned.

## Endpoint Firmware Status

Task 288 implements the endpoint-side peripheral:

- The endpoint runtime now declares the Core `ble.provision_wifi` operation,
  lease scope, contract version, envelope schema version, Voice payload schema
  id, encryption algorithm, key agreement, and canonical GATT
  service/characteristic UUIDs.
- Native ESP32-S3 board profiles enable NimBLE peripheral/GATT support through
  generated board-profile metadata and build defaults.
- The firmware starts BLE onboarding only when the board supports native BLE and
  the endpoint is not already provisioned.
- Device identity and pairing metadata expose the endpoint ephemeral X25519
  public key so Supervisor can create the encrypted provisioning envelope.
- The heartbeat reports BLE onboarding capability, transport, UUIDs, state,
  advertising eligibility, and ack/error status without reporting credentials,
  ciphertext, pairing nonce values, claim codes, or derived keys.
- The encrypted credential write path validates envelope shape, contract
  version, schema id, onboarding session, target identity, pairing nonce
  binding, expiry, AAD bytes, key id, size, and replay sequence.
- The endpoint derives the provisioning key with X25519, HKDF-SHA256, and the
  contract salt/info strings, then decrypts the envelope with AES-256-GCM.
- The decrypted Voice payload apply path writes through
  `save_endpoint_provisioning`, requests Wi-Fi reconnect, and stops advertising
  after successful provisioning.

## HexeVoice Backend Status

Task 289 adds the HexeVoice requester/operator side:

- The local backend exposes `POST /api/endpoint/ble/provision-wifi` for
  Core-governed BLE provisioning attempts.
- The route discovers Core hardware-access and Voice BLE provisioning schemas
  before requesting a lease.
- The request to Core uses operation `ble.provision_wifi`, resource type
  `bluetooth`, optional Supervisor/adapter targeting, operator reason, and the
  provisioning context read from the endpoint BLE metadata.
- The service handles `pending`, `denied`, `granted`, and terminal Supervisor
  failures distinctly.
- Only granted hardware access calls Supervisor
  `/api/supervisor/hardware/bluetooth/ble/provision-wifi` with the lease token,
  target address when known, timeout, provisioning context, and Voice Wi-Fi
  credential payload.
- The local API response redacts lease tokens, Wi-Fi passwords, ciphertext,
  tags, nonces, claim-code references, endpoint/Supervisor public keys, and
  derived-key/decrypted-payload fields.
- Granted leases are released after the Supervisor attempt when Core returns a
  lease id; otherwise Core expiry remains the fallback cleanup path.
- The dashboard exposes a compact operator BLE provisioning form while keeping
  the existing connected-endpoint provisioning command as the post-join
  reconfiguration path.
- Normal Core node onboarding/trust is not bypassed. After the endpoint joins
  Wi-Fi, the existing discovery, heartbeat, registry, and trust workflow remain
  the source of truth for a connected endpoint.

## Recovery BLE Status

Task 290 adds a recovery-owned BLE rescue path:

- The recovery app links the shared GATT UUID bridge and a small local recovery
  provisioning component without linking the normal `endpoint_runtime`.
- Supported native-BLE recovery boards advertise the canonical
  `ble.provision_wifi` service while recovery is running.
- The local recovery write mode requires `mode: "local_recovery"`, the recovery
  onboarding session id, the short-lived pairing nonce, the target node id, and
  the same Voice Wi-Fi/backend credential payload shape before writing NVS.
- Recovery writes use the same `hexe_settings` keys used by HTTP recovery
  provisioning and normal endpoint provisioning.
- `/api/recovery/ble/status` reports support, mode, UUIDs, advertising state,
  and ack/error status without exposing Wi-Fi credentials, pairing nonce values,
  claim-code references, ciphertext, or derived keys.
- Core-governed encrypted BLE provisioning remains the normal endpoint firmware
  path; recovery returns `core_governed_requires_endpoint_app` for encrypted
  Core envelopes instead of accepting them silently.

## Validation Status

Task 291 adds CI/local validation coverage around the BLE onboarding boundary:

- `FakeBleGattEndpoint` models firmware-facing GATT identity, pairing nonce,
  credential writes, ack/error reads, replay rejection, local recovery mode, and
  safe status redaction without physical Bluetooth hardware.
- `FakeSupervisorBleBroker` models the Supervisor-facing broker boundary for
  adapter presence, adapter selection, granted/pending/denied lease status,
  lease expiry, and operation/scope isolation.
- Security checks prove `hardware.bluetooth.ble.scan` and
  `hardware.bluetooth.ble.status` leases cannot be reused for
  `ble.provision_wifi`; failures return `lease_scope_mismatch` before a target
  write happens.
- State-machine checks cover `wrong_adapter`, wrong target node, wrong pairing
  nonce, `malformed_payload`, `replay_detected`, failed Wi-Fi association, and
  `backend_unreachable`.
- Physical validation criteria live in
  `docs/ble-onboarding-physical-validation.md` for HA Voice PE,
  ESP32-S3-BOX-3, Waveshare S3 1.85C BOX V2, and the future P4/C6 flow.

## HexeVoice Implementation Plan

### Task 288: Endpoint firmware peripheral

- Add a BLE provisioning component behind board-profile capability flags.
- Implement the canonical GATT service and characteristic UUIDs.
- Advertise only while unprovisioned, in explicit provisioning mode, or in
  recovery provisioning.
- Generate or expose a short-lived pairing nonce or claim-code reference.
- Validate contract version, payload schema id, target node identity,
  onboarding session id, pairing nonce or claim-code binding, replay protection,
  payload size/chunking, and expiration.
- Decrypt and validate the credential envelope before writing settings.
- Persist values through `save_endpoint_provisioning`.
- Restart/reconnect Wi-Fi after success and disable advertising after success or
  timeout.
- Report BLE provisioning capability/status in heartbeat without exposing
  credentials, ciphertext, nonces, claim codes, or derived keys.

### Task 289: Backend/operator orchestration

- Discover Core request and Voice payload schemas before starting an attempt.
- Request `ble.provision_wifi` with the provisioning context and operator
  reason.
- Handle `denied`, `pending`, and `granted` distinctly.
- For granted leases, call Supervisor
  `/api/supervisor/hardware/bluetooth/ble/provision-wifi` with the lease token,
  provisioning context, target address when known, timeout, and Voice payload.
- Keep Wi-Fi credentials only in the bounded transmit request; redact them from
  logs, API responses, events, diagnostics, and UI state.
- Release the lease after completion when a lease id is available, or allow
  expiry after terminal failure.
- Continue normal Core node onboarding/trust only after the endpoint joins the
  network and the existing HexeVoice endpoint registry/heartbeat sees it.
- Preserve the current backend-to-connected-endpoint provisioning command as the
  post-join reconfiguration path.

### Task 290: Recovery support

- Reuse the endpoint BLE component only if it fits the recovery partition and
  avoids endpoint-runtime dependencies.
- In Core-available mode, honor the same GATT contract and Core/Supervisor lease
  model.
- In Core-offline mode, provide an explicit local recovery-safe pairing path
  with the same NVS writes and redacted diagnostics.
- Make Core-governed and local-recovery BLE modes distinguishable in status,
  LED/display indications, logs, and local API responses.

### Task 291: Validation

- Add fake BLE/GATT harnesses for firmware-facing and Supervisor-facing flows.
- Cover lease status handling, pending approval, broker failures, redaction,
  wrong-node/wrong-adapter/wrong-nonce failures, malformed payloads, failed Wi-Fi
  association, backend unreachable, replay rejection, and timeout behavior.
- Add board-profile validation for BLE-capable and BLE-unsupported behavior.
- Document physical validation for HA Voice PE, ESP32-S3-BOX-3, Waveshare S3
  1.85C BOX V2, and the future P4/C6 board.

## Failure Modes To Preserve

Core-side:

- `denied` when policy is `disabled`, no trusted/online Supervisor reports
  Bluetooth governance, Bluetooth is absent, the node is not trusted, or lease
  signing is unavailable.
- `pending` when policy is `ask`; operator approval is required before broker
  use.
- `granted` only with a short-lived lease token scoped to
  `hardware.bluetooth.ble.provision_wifi`.
- Lease validation must fail for expired, released, wrong node, wrong resource,
  wrong operation, wrong Supervisor, wrong adapter, or mismatched provisioning
  context.

Supervisor-side:

- `403 claim_scope_missing` or related hardware access errors for wrong or
  mismatched lease material.
- `404 bluetooth_unavailable` or `bluetooth_adapter_not_found` when host
  hardware cannot satisfy the request.
- `422` for invalid Voice Wi-Fi/backend payloads.
- `ok=false`, `status=failed`, and `error=gatt_backend_unavailable` when no
  physical BLE/GATT backend is configured.
- `ok=false`, `status=failed`, and `error=gatt_backend_failed` when the backend
  raises while writing to the target.
- Responses must redact `credential_payload.wifi_password`.

Endpoint-side:

- Reject `invalid_nonce`, `invalid_claim_code`, `decrypt_failed`,
  `unsupported_schema`, `invalid_payload`, `wifi_apply_failed`,
  `backend_unreachable`, `timeout`, and `already_provisioned` deterministically.
- Do not leave BLE advertising on after successful provisioning or after a
  timeout when the endpoint is already provisioned.
- Do not persist partial credentials if validation or decryption fails.

## Security Notes

- Core must not receive, persist, or log plaintext Wi-Fi credentials.
- Supervisor receives plaintext Voice payload fields only for the bounded broker operation and must redact responses/events.
- The BLE write uses an encrypted provisioning envelope; BLE link encryption is
  useful but not sufficient by itself.
- The endpoint ephemeral X25519 private key is volatile and rotates with each
  pairing session; only the public key is exposed over GATT metadata.
- Firmware and recovery logs must not include plaintext credentials,
  ciphertext, pairing nonces, claim codes, derived keys, or trust tokens.
- The target endpoint owns final credential application and must fail closed if
  the provisioning envelope cannot be validated.

## External Follow-Ups

- Supervisor-owned: provide and validate a real physical BLE/GATT backend.
  Current Supervisor behavior is intentionally pluggable and fails closed with
  `gatt_backend_unavailable` when no backend is configured.
- Core/Supervisor-owned: keep schema discovery, hardware lease validation, and
  broker route docs synchronized with any future claim-code or nonce registry
  changes. HexeVoice should consume those contracts rather than defining local
  Core-compatible variants.
- Core-owned, if not already present in the active deployment: expose the
  operator-facing approval path for `ask` policy hardware requests so HexeVoice
  can surface pending access without bypassing policy.
