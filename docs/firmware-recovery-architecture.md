# Firmware Recovery App Architecture

Status: Task 273 architecture contract; Task 274 skeleton implemented; Task 275 local rescue controls implemented

Reference: `docs/fw-roadmap.txt`

## Purpose

Define the first recovery/provisioning firmware application before a bootable
skeleton is added. Recovery exists to restore or provision a device when the
normal endpoint application is broken, unconfigured, or unable to reach Core.

Recovery is a separate firmware application type, not a normal runtime mode of
the endpoint app.

## Application Layout

The firmware tree uses two application entrypoint lanes:

| Application | App selector | Entrypoint | Purpose |
| --- | --- | --- | --- |
| Endpoint | `HEXE_FIRMWARE_APP=endpoint` | `firmware/apps/endpoint/main/` | Normal voice endpoint runtime |
| Recovery | `HEXE_FIRMWARE_APP=recovery` | `firmware/apps/recovery/main/` | Minimal provisioning, rescue install, and diagnostics |

The root ESP-IDF project selects an app by `HEXE_FIRMWARE_APP`. The Task 274
skeleton adds `firmware/apps/recovery/main/CMakeLists.txt` and
`firmware/components/recovery_runtime/`. Task 275 adds the local recovery
control plane in `firmware/components/recovery_runtime/recovery_control.cpp`.

Expected build shape:

```text
HEXE_FIRMWARE_APP=recovery HEXE_BOARD_PROFILE=<board> ./firmware/build.sh build
```

Recovery artifacts must declare `application_type=recovery`. Endpoint artifacts
must continue to declare `application_type=endpoint`.

## Shared Code Boundary

Recovery must not link `endpoint_runtime`.

Recovery may share small, dependency-light components only when they are safe
without Core, SD card, model bundles, or normal voice runtime state:

- board-profile metadata generation and safe pin constants
- partition schema metadata and validation helpers
- firmware manifest parsing, hash checks, and signature verification
- NVS/settings primitives with redacted readback
- minimal Wi-Fi station/AP provisioning helpers
- small diagnostics helpers for boot state, flash, PSRAM, storage, and board IO

Recovery must not include:

- wake-word or Stop model inference
- full audio streaming, STT, TTS, or assistant session logic
- Speaker ID or raw voice recording logic
- normal dashboard UI or large display assets
- media playback, automation, or timer behavior
- backend WebSocket/session protocols

If a helper currently lives inside `endpoint_runtime`, Task 274 or later tasks
must extract it into a focused shared component before recovery can use it.

## Entry Conditions

Recovery entry reasons must be explicit and reportable:

| Entry reason | Source | Expected behavior |
| --- | --- | --- |
| `physical_gesture` | Boot-time button or board-specific recovery sequence | Start recovery immediately |
| `main_requested` | Endpoint app writes a reboot-to-recovery flag, then restarts | Clear the one-shot flag after recovery observes it |
| `no_valid_main_slot` | Boot path cannot select a valid endpoint OTA slot | Stay in recovery until a valid main image is installed |
| `boot_failure_threshold` | Repeated failed endpoint boots exceed the configured threshold | Preserve counters for diagnostics and offer rollback/install actions |
| `factory_or_serial` | USB/full flash or serial maintenance path | Start recovery with local diagnostics enabled |

The first implementation should use board-specific physical gestures only where
the pin map is already complete. Missing gestures must be reported as
`unsupported` for that board rather than guessed.

## Recovery Interfaces

Recovery must work without Hexe Core and without an SD card.

Required interfaces:

- serial console diagnostics
- local temporary Wi-Fi AP when no configured network is usable
- local HTTP diagnostics and provisioning API
- LED/button status where the board has suitable controls
- minimal display/touch status on display boards, without full product UI

Optional later interface:

- BLE provisioning, if it fits the recovery partition and security model

## Recovery Diagnostics API

The local API is recovery-owned and intentionally small. All endpoints return
JSON, redact secrets, and must remain useful when Core is offline.

Initial endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/recovery/status` | Overall recovery state, board identity, versions, network, actions |
| `GET` | `/api/recovery/partitions` | Partition table, OTA slots, selected boot slot, image states |
| `GET` | `/api/recovery/diagnostics` | PSRAM, flash, storage, audio/display/touch/button summaries |
| `POST` | `/api/recovery/wifi` | Save Wi-Fi station settings or start/stop temporary AP mode |
| `POST` | `/api/recovery/endpoint` | Save endpoint id, display name, backend host/ports, and TLS mode |
| `POST` | `/api/recovery/firmware/install` | Upload or fetch a signed endpoint image and install to an inactive main slot |
| `POST` | `/api/recovery/boot/select` | Select the next valid main slot or stay in recovery |
| `POST` | `/api/recovery/config/reset` | Reset selected provisioning/config/calibration state |

The local status page is served from `/` and links to the JSON diagnostics
endpoints. It is intentionally plain HTML so recovery does not depend on the
normal endpoint dashboard assets.

Minimum `/api/recovery/status` shape:

```json
{
  "schema_version": "hexe-recovery-status-v1",
  "application_type": "recovery",
  "recovery_api_version": "hexe-recovery-api-v1",
  "board_profile": "ha_voice_pe",
  "partition_schema": "s3-16m-recovery-v1",
  "soc": "esp32s3",
  "flash_size": "16MiB",
  "psram_size": "8MiB",
  "entry_reason": "physical_gesture",
  "core_required": false,
  "sd_required": false,
  "network": {
    "mode": "ap",
    "ssid_configured": false,
    "temporary_ap_active": true
  },
  "main_slots": [
    {
      "label": "ota_0",
      "application_type": "endpoint",
      "valid": true,
      "pending_verify": false,
      "version": "z20260830101519-864f558"
    }
  ],
  "actions": {
    "wifi_provisioning": true,
    "endpoint_provisioning": true,
    "firmware_upload": true,
    "boot_select": true,
    "selective_config_reset": true
  }
}
```

## Install And Validation Policy

Recovery installs endpoint images only into endpoint OTA slots. It must reject
packages that fail any of these checks:

- `application_type` is not `endpoint`
- board profile mismatch
- SoC, flash size, PSRAM size, or partition schema mismatch
- image does not fit the endpoint app slot
- missing or invalid SHA-256
- missing, invalid, unsupported, or untrusted signature in production mode
- incompatible firmware, model, asset, or calibration API ranges
- anti-rollback or release-channel policy violation where enabled

Task 275 uses streamed HTTP upload for the first local rescue install path.
The operator posts the endpoint image body to `/api/recovery/firmware/install`
with signed metadata headers:

| Header | Purpose |
| --- | --- |
| `X-Hexe-Application-Type` | Must be `endpoint` |
| `X-Hexe-Board-Profile` | Must match the compiled board profile |
| `X-Hexe-Partition-Schema` | Must match the compiled partition schema |
| `X-Hexe-Version` | Target endpoint firmware version |
| `X-Hexe-Image-Size` | Byte length, equal to HTTP body length |
| `X-Hexe-Image-Sha256` | Expected SHA-256 of the uploaded image body |
| `X-Hexe-Signature-Algorithm` | `hmac-sha256` for local development |
| `X-Hexe-Signature-Key-Id` | Must match the generated endpoint config key id |
| `X-Hexe-Manifest-Signature` | HMAC over the recovery install metadata |

Recovery hashes the body while writing to the inactive OTA slot, rejects checksum
mismatches, and selects the new endpoint partition for next boot only after
`esp_ota_end()` succeeds. It does not automatically restart after install.

When both main OTA slots are unusable, recovery remains the operator-facing
repair surface. The recovery app must still boot independently, expose serial
diagnostics and the temporary local HTTP/API surface, report partition state,
accept a signed endpoint image upload for an inactive OTA slot, and allow an
operator boot-slot selection after repair. A board/partition layout that does
not include a factory recovery app cannot guarantee this path after both
endpoint OTA slots become unusable; that layout requires USB/full flashing or a
future schema migration before it can make the same recovery guarantee.

The active S3 recovery-capable schema reserves 2 MiB for the factory recovery
app. That is intentionally larger than the current measured recovery binary so
Wi-Fi, HTTP rescue, diagnostics, and small board-specific status surfaces can
grow without squeezing the endpoint OTA slots.

Normal endpoint OTA must not update recovery during early product phases.
Recovery app updates require USB/full service flashing until a separate signed
recovery-update lane is designed.

## Handoff To Implementation Tasks

Task 274 built the minimal bootable recovery app skeleton:

- `firmware/apps/recovery/main/CMakeLists.txt`
- recovery `app_main`
- small recovery runtime component
- `application_type=recovery` build/export metadata
- serial and JSON status without Core, SD, wake models, or endpoint runtime

Task 275 adds operator-rescue features:

- local status page and recovery HTTP API
- Wi-Fi and endpoint provisioning writes to endpoint-compatible NVS keys
- signed endpoint firmware upload/install into an inactive OTA slot
- partition and boot-state inspection
- selective provisioning, Wi-Fi, settings, and calibration reset
