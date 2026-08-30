# Recovery App

This directory contains the minimal recovery/provisioning firmware app.
The recovery app is buildable for S3 board profiles and reports serial JSON
diagnostics without linking the normal endpoint runtime.

Task 275 adds a local recovery control plane:

- temporary `HexeRecovery-<board>` Wi-Fi AP with optional station mode when
  saved Wi-Fi credentials exist
- plain local status page at `/`
- JSON APIs under `/api/recovery/*` for status, partitions, diagnostics,
  Wi-Fi provisioning, endpoint provisioning, signed endpoint-image install,
  boot-slot selection, and selective config reset
- streamed main-firmware install into the inactive OTA slot with signed
  metadata, SHA-256 verification, and no automatic reboot

The recovery app architecture contract lives in
`docs/firmware-recovery-architecture.md`.
