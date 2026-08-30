# Hexe Firmware OTA Plan

For the broader partition, recovery-app, mutable bundle, and multi-board
strategy, see `docs/firmware-partition-ota-roadmap.md`.

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
- signed binaries

## Security Notes

For local development:

- backend-pushed metadata is signed with HMAC-SHA256
- firmware verifies the signed metadata before download
- firmware verifies downloaded size and SHA-256 before finishing OTA
- HTTP is allowed for local Voice Node artifact hosting

For production deployments:

- add release signing
- use HTTPS only
- host firmware on a trusted Hexe domain
- rotate deployment signing keys when firmware moves to a new trust domain
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
- Firmware OTA client in `firmware/components/endpoint_runtime/system/ota.cpp`.
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

The backend sends an `ota.update` event to the connected endpoint WebSocket with
the firmware URL, version, board profile, size, SHA-256, signature algorithm,
signature key id, manifest signature, and the static release metadata exported
with the artifact. Static metadata includes application type, SoC/IDF target,
flash and PSRAM size, partition schema, app slot size, and firmware/model/asset/
calibration API versions. Current firmware validates the signed OTA metadata
before downloading, hashes the downloaded image bytes while ESP-IDF OTA streams
them, and only calls `esp_https_ota_finish()` when the downloaded size and
SHA-256 match. The stricter board geometry, partition-schema, app-type, and API
compatibility rejection path is tracked separately in the partition/OTA roadmap.

The pushed metadata is signed as HMAC-SHA256 over this canonical payload:

```text
profile
url
version
sha256
size_bytes
signature_algorithm
signature_key_id
```

Local development uses `HEXEVOICE_OTA_MANIFEST_KEY_ID=hexevoice-dev-v1` and `HEXEVOICE_OTA_MANIFEST_SIGNING_KEY=hexevoice-local-dev-ota-signing-key` by default. Firmware receives the same key id and signing key from `firmware/config/endpoint.yaml` through the generated `endpoint_config.h`; production deployments should set deployment-specific values before building firmware and running the backend.

Endpoint integrity policy:

- reject missing or malformed checksums before download
- reject missing, invalid, unsupported, or unknown-key signatures before download
- reject artifacts whose `profile` does not match the compiled board profile
- reject empty, same, or lower target versions as downgrade/replay attempts
- reject downloaded images whose byte count or SHA-256 differs from signed metadata

The endpoint reports OTA integrity failures through `command.error` with exact codes such as `missing_signature`, `invalid_signature`, `unsupported_profile`, `downgrade_or_replay`, `missing_checksum`, `invalid_checksum`, `invalid_size`, and `checksum_mismatch`.

`firmware/export-artifacts.sh` copies the app binary to:

```text
runtime/firmware/hexe_firmware.bin
runtime/firmware/hexe_firmware_esp_box_3.bin
runtime/firmware/hexe_firmware_ha_voice_pe.bin
```

`hexe_firmware.bin` remains the default ESP-BOX OTA artifact for compatibility. Profile-specific OTA pushes can target `hexe_firmware_esp_box_3.bin` or `hexe_firmware_ha_voice_pe.bin`. Runtime firmware binaries and generated manifests are intentionally ignored by git; `runtime/firmware/.gitkeep` keeps the directory present.

Each `manifest-<profile>.json` generated by `firmware/export-artifacts.sh`
records the static release contract for that binary. The backend preserves those
fields when serving `/api/firmware/manifest`, computing endpoint update
metadata, and pushing OTA commands, while still generating URL-specific
HMAC-SHA256 signatures at delivery time.

OTA versions must be lexically newer than the endpoint's current version. The
standard `firmware/build.sh` path therefore injects a timestamped project
version such as `zYYYYMMDDHHMMSS-<git-sha>` instead of using raw git hashes.
This avoids rejecting valid updates when a newer commit hash happens to sort
lower than the currently installed hash. Dirty tracked worktree changes are
blocked before OTA/runtime export so `_dirty` firmware versions are not served.

OTA command records use a longer backend timeout than normal endpoint commands
because downloading, flashing, and rebooting commonly takes more than 10
seconds. A device can be offline briefly during reboot and still complete the
update successfully.

Fresh hosted installs can populate `runtime/firmware` from a local export,
artifact base URL, git repository, or GitHub release without building firmware
on the Voice Node host:

```bash
./scripts/firmware-artifacts-control.sh download
./scripts/firmware-artifacts-control.sh verify
./scripts/firmware-artifacts-control.sh list
```

Configure one source before running `download`: `HEXEVOICE_FIRMWARE_SOURCE_DIR`,
`HEXEVOICE_FIRMWARE_ARTIFACT_URLS`, `HEXEVOICE_FIRMWARE_ARTIFACT_BASE_URL`,
`HEXEVOICE_FIRMWARE_REPO_URL`, or `HEXEVOICE_FIRMWARE_GITHUB_REPOSITORY` with
`HEXEVOICE_FIRMWARE_SOURCE=github-release`. Hosted install runs the same path
when `HEXEVOICE_SETUP_FIRMWARE=true`.

Because the partition table changed from a factory-only layout to OTA slots, each device needs one full USB flash of bootloader, partition table, OTA data, and app before backend-pushed OTA updates can work:

```bash
cd firmware/export
./flash-esptool.sh /dev/ttyACM0
```

Development OTA allows HTTP for local Voice Node hosting because metadata signing and firmware-side checksum enforcement protect the artifact identity. Production OTA should still move to HTTPS-only and rotate deployment signing keys when firmware is rebuilt for a new trust domain.

## Future Repo Work

The firmware track should later gain:

- release packaging script

## Practical Next Step

When we are ready to implement OTA in code, the first step should be:

1. add an OTA partition table
2. report the ESP-IDF app/project version embedded in the build
3. implement manual OTA from a fixed HTTPS URL
4. only after that add manifest-driven update checks

That gives Hexe a simple OTA foundation without overbuilding it.
