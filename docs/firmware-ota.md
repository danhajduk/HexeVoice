# Hexe Firmware OTA Plan

## Goal

Define a simple OTA path for the native Hexe firmware so devices can update over Wi-Fi later without requiring USB flashing.

## Recommendation

Use standard `ESP-IDF` HTTPS OTA for the first version.

This is the simplest good path because it:

- is already supported by Espressif
- avoids inventing a custom updater too early
- works well for direct device-to-server updates
- can later be wrapped in a richer Hexe release system

## Recommended Flow

1. Hexe boots and connects to Wi-Fi.
2. Hexe checks a small update manifest from a Hexe-hosted HTTPS endpoint.
3. If a newer version is available, Hexe downloads the firmware image over HTTPS.
4. Hexe validates the image and installs it into the inactive OTA partition.
5. Hexe reboots into the new version.
6. If boot succeeds, mark the app as valid.
7. If boot fails, roll back automatically.

## Why This Is The Best First OTA

For the first standalone firmware generation, the best OTA is:

- one binary
- one manifest
- one HTTPS endpoint

That keeps the system understandable while still being production-shaped.

## Minimum Pieces Needed

### 1. OTA Partition Table

The firmware must use an OTA-capable partition table with:

- factory or ota_0
- ota_1
- NVS
- optional storage partition

This lets Hexe download the next firmware image into the inactive slot safely.

### 2. Firmware Version

Each firmware build should carry a version string such as:

- `0.1.0`
- `0.1.1`
- `2026.04.10-1`

Hexe uses this to compare the currently running version against the manifest.

### 3. Update Manifest

Start with a very small JSON document, for example:

```json
{
  "version": "0.1.1",
  "build": "2026-04-10.1",
  "url": "https://downloads.hexe.ai/firmware/hexe-box-esp32s3-0.1.1.bin",
  "sha256": "optional-checksum-here",
  "min_battery": 0,
  "notes": "Boot animation and display bring-up improvements."
}
```

Hexe can fetch this from a stable endpoint such as:

```text
https://downloads.hexe.ai/firmware/stable.json
```

### 4. OTA Client In Firmware

The firmware OTA module should eventually own:

- update check scheduling
- manifest fetch
- version comparison
- download and apply
- success/failure reporting
- rollback confirmation

## Suggested Hexe OTA States

At the UI level, OTA should be treated as a normal device state:

- `idle`
- `update_available`
- `downloading_update`
- `installing_update`
- `restarting`
- `update_failed`

This will make OTA feel like part of the product, not a hidden maintenance trick.

## First OTA Policy

Keep the first OTA policy intentionally simple:

- only check when Wi-Fi is connected
- only update from HTTPS
- only update when idle
- skip update while voice is active
- reboot automatically after successful install

Later you can add:

- staged rollout channels
- beta/stable streams
- forced critical updates
- signed manifests
- signed binaries

## Security Notes

For the first version:

- use HTTPS only
- host firmware on a trusted Hexe domain
- prefer SHA-256 verification from the manifest

For a later hardened version:

- add signed manifests
- add release signing
- pin trust roots or server certificates if needed

## What To Host

At minimum, host:

- firmware `.bin` file
- one JSON manifest per channel

Example:

```text
https://downloads.hexe.ai/firmware/stable.json
https://downloads.hexe.ai/firmware/beta.json
https://downloads.hexe.ai/firmware/hexe-box-esp32s3-0.1.1.bin
```

## Development Recommendation

Do not build OTA first.

Implement in this order:

1. native boot screen
2. display bring-up
3. button handling
4. audio bring-up
5. backend connection
6. OTA

That way OTA updates something real instead of becoming early plumbing for an incomplete firmware.

## Implemented Local OTA Foundation

Status: initial implementation added on 04/25/2026.

The native firmware now has:

- OTA-capable partition table with `ota_0`, `ota_1`, and `otadata`.
- Firmware version text drawn on the load screen as `FW <app version>`.
- Firmware OTA client in `firmware/main/system/ota.cpp`.
- Firmware OTA progress bar on the LCD while an update is downloading.
- Firmware disables the voice WebSocket and audio upload path while OTA is active.
- Backend `ota.update` WebSocket event handling in the endpoint firmware.
- Backend artifact hosting from `runtime/firmware`.
- Backend OTA push API:

```text
GET  /api/firmware/manifest
GET  /api/firmware/artifacts/hexe_firmware.bin
POST /api/firmware/ota/push
```

Push payload:

```json
{
  "endpoint_id": "esp-box-1",
  "filename": "hexe_firmware.bin",
  "version": "0.1.1"
}
```

The backend sends an `ota.update` event to the connected endpoint WebSocket with the firmware URL, version, size, and SHA-256. The endpoint downloads the image with ESP-IDF OTA and reboots on success.

`firmware/export-artifacts.sh` copies the app binary to:

```text
runtime/firmware/hexe_firmware.bin
```

Runtime firmware binaries and generated manifests are intentionally ignored by git; `runtime/firmware/.gitkeep` keeps the directory present.

Because the partition table changed from a factory-only layout to OTA slots, each device needs one full USB flash of bootloader, partition table, OTA data, and app before backend-pushed OTA updates can work:

```bash
cd firmware/export
./flash-esptool.sh /dev/ttyACM0
```

Development OTA allows HTTP for local Voice Node hosting. Production OTA should move to HTTPS-only and signed manifests before wider deployment.

## Future Repo Work

The firmware track should later gain:

- signed update manifest format
- firmware checksum verification in firmware before reboot
- release packaging script

## Practical Next Step

When we are ready to implement OTA in code, the first step should be:

1. add an OTA partition table
2. add a firmware version constant
3. implement manual OTA from a fixed HTTPS URL
4. only after that add manifest-driven update checks

That gives Hexe a simple OTA foundation without overbuilding it.
