# Recovery App

This directory contains the minimal recovery/provisioning firmware app.
The recovery app is buildable for supported S3 and P4 board profiles and
reports serial JSON diagnostics without linking the normal endpoint runtime.
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

Task 290 adds local BLE rescue provisioning on BLE-capable recovery boards:

- the recovery app advertises the canonical `ble.provision_wifi` GATT service
- `/api/recovery/ble/status` reports BLE mode, support, UUIDs, state, and
  ack/error status without secrets
- local recovery BLE writes require the recovery session id and pairing nonce
  before saving endpoint-compatible Wi-Fi/backend settings
- Core-governed encrypted BLE provisioning remains owned by the normal endpoint
  app path

For `waveshare_p4_wifi6_touch_lcd_7b`, recovery enables NimBLE through the
ESP32-C6 hosted transport so an un-onboarded/factory device can advertise the
same recovery provisioning service before the normal endpoint firmware is
configured.
The P4 recovery app also initializes the 7-inch display and renders a live
onboarding screen before and during pairing. The screen shows the onboarding
state/reason, device name/id, Wi-Fi MAC, BLE MAC, firmware version, and whether
the local HTTP rescue path is active. It may load an optional 1024x600 RGB565
background from `/sdcard/hexe/pictures/recovery_bg.rgb565`, falling back to
`/sdcard/hexe/pictures/bg.rgb565` and then a procedural background; secrets are
never rendered on the panel.

For `waveshare_s3_touch_lcd_1_85c_box_v2`, the recovery app embeds three
360x360 RGB565 test plates for the round LCD: waiting to pair, pairing, and OTA
install progress. The display refresh loop selects the plate from BLE pairing
and firmware upload state so the minimal firmware has basic bench-test UI.

The recovery app architecture contract lives in
`docs/firmware-recovery-architecture.md`.
