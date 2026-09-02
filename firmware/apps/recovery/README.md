# Recovery App

This directory contains the minimal recovery/provisioning firmware app.
The recovery app is buildable for S3 board profiles and reports serial JSON
diagnostics without linking the normal endpoint runtime.
Factory onboarding builds use `HEXE_FIRMWARE_APP=minimal` and reuse this app,
exporting flashable artifacts as `firmware/export-min-<board>`.

Task 275 adds a local recovery control plane:

- temporary `HexeRecovery-<board>` Wi-Fi AP with optional station mode when
  saved Wi-Fi credentials exist
- plain local status page at `/`
- JSON APIs under `/api/recovery/*` for status, partitions, diagnostics,
  Wi-Fi provisioning, endpoint provisioning, signed endpoint-image install,
  boot-slot selection, and selective config reset
- streamed main-firmware install into the inactive OTA slot with signed
  metadata, SHA-256 verification, and no automatic reboot

Task 290 adds local BLE rescue provisioning on native-BLE recovery boards:

- the recovery app advertises the canonical `ble.provision_wifi` GATT service
- `/api/recovery/ble/status` reports BLE mode, support, UUIDs, state, and
  ack/error status without secrets
- local recovery BLE writes require the recovery session id and pairing nonce
  before saving endpoint-compatible Wi-Fi/backend settings
- Core-governed encrypted BLE provisioning remains owned by the normal endpoint
  app path

The recovery app architecture contract lives in
`docs/firmware-recovery-architecture.md`.
