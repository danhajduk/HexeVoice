# BLE Onboarding Physical Validation

Status: Task 291 validation checklist.

Use this checklist after flashing firmware to a real endpoint. CI covers the
contract and state-machine behavior with fake BLE/GATT harnesses; these steps
capture the physical evidence needed before enabling BLE onboarding broadly.

## Required Evidence

Record this for every physical run:

- board profile, firmware app, firmware git commit, partition schema, and build
  artifact path
- Core node id, Supervisor id, host adapter name, and operator policy mode
- BLE target address or advertising name
- provisioning mode: Core-governed endpoint firmware or local recovery
- visible device indication during advertising, apply, success, and failure
- final endpoint heartbeat after Wi-Fi joins the backend
- redaction check result for logs, API responses, UI status, and diagnostics

Do not paste plaintext Wi-Fi passwords, pairing nonce values, claim-code
references, lease tokens, encrypted envelopes, ciphertext, tags, AAD, public
keys, derived keys, or decrypted payloads into validation notes.

## Board Matrix

| Board | Expected BLE path | Required physical checks |
| --- | --- | --- |
| HA Voice PE | Native ESP32-S3 BLE, endpoint and recovery | Advertises `HexeVoice` in endpoint provisioning mode, advertises `HexeRecovery` in recovery, accepts Core-governed endpoint provisioning, accepts local recovery provisioning, stops advertising after success, reconnects to Wi-Fi/backend, and reports BLE heartbeat/status without secrets |
| ESP32-S3-BOX-3 | Native ESP32-S3 BLE, endpoint and recovery | Same endpoint and recovery flow as HA Voice PE, plus visible display/touch status must distinguish local recovery from Core-governed endpoint provisioning |
| Waveshare S3 1.85C BOX V2 | Native ESP32-S3 BLE, buildable scaffold | Verify advertising, GATT reads/writes, recovery fallback, display/touch indications, and post-Wi-Fi heartbeat after USB flashing on the real board |
| future P4/C6 | ESP32-P4 with ESP32-C6 radio path | Until the C6 transport is implemented, firmware must explicitly report BLE unavailable or coprocessor pending; later validation must repeat the same endpoint/recovery checklist through the C6 BLE bridge |

## Happy Path

1. Flash the endpoint image and matching partition table over USB.
2. Boot with provisioning unset or explicitly enter provisioning mode.
3. Confirm the endpoint advertises the canonical service
   `7f9c0000-5f04-4d8b-9a46-7c0f7a100000`.
4. Read identity, pairing nonce, provisioning status, and ack/error
   characteristics.
5. From the HexeVoice operator UI or API, request Core-governed
   `ble.provision_wifi` provisioning.
6. Confirm Core grants only the
   `hardware.bluetooth.ble.provision_wifi` lease scope.
7. Confirm Supervisor writes the encrypted credential envelope through the
   selected adapter and target address.
8. Confirm the endpoint accepts the write, saves settings, reconnects Wi-Fi,
   reaches the backend, emits heartbeat, and stops advertising.
9. Confirm status and logs remain redacted.

## Recovery Path

1. Boot the recovery app through the recovery button/chosen boot slot.
2. Open `/api/recovery/status` and `/api/recovery/ble/status`.
3. Confirm recovery reports local recovery mode and does not claim
   Core-governed encrypted BLE support.
4. Read the recovery BLE identity, pairing nonce, status, and ack/error
   characteristics.
5. Write a local recovery payload with `mode: "local_recovery"`, matching
   session id, matching target node id, matching pairing nonce, and the Voice
   Wi-Fi/backend payload.
6. Confirm the recovery app saves endpoint-compatible `hexe_settings` keys.
7. Reboot to the endpoint app and confirm normal Wi-Fi/backend heartbeat.
8. Confirm recovery HTTP status, BLE status, serial logs, and crash output do
   not expose plaintext credentials.

## Failure Cases

Every supported physical board should capture the expected failure state for:

- absent Bluetooth adapter: Core/Supervisor denies or fails before credentials
  are sent to a GATT target
- policy disabled: Core returns denied and HexeVoice does not call Supervisor
- policy ask pending: HexeVoice reports pending and does not call Supervisor
- lease expiry: Supervisor rejects the operation and the endpoint does not save
  settings
- wrong adapter: Supervisor rejects the request before writing credentials
- wrong node: endpoint/recovery rejects the payload before NVS writes
- wrong pairing nonce: endpoint/recovery rejects the payload before NVS writes
- malformed payload: endpoint/recovery returns an explicit malformed/invalid
  payload error
- failed Wi-Fi association: endpoint reports Wi-Fi apply/connect failure without
  leaking credentials
- backend unreachable: endpoint reports backend unreachable and remains
  recoverable through normal provisioning or recovery
- replayed sequence: endpoint rejects the second write with the same or lower
  sequence
- unsupported board/C6 bridge pending: firmware reports BLE unavailable or
  coprocessor pending rather than advertising a partial service

## Release Gate

BLE onboarding can be enabled by default for a board class only after:

- local tests pass for fake GATT, fake Supervisor, backend redaction, firmware
  static checks, and board-profile validation
- endpoint and recovery firmware builds fit the target partition layout
- one real board from the class completes the happy path and recovery path
- all required failure cases are either physically reproduced or marked not
  applicable with a reason
