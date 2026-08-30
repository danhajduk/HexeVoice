# Firmware Partition and OTA Roadmap

Status: planning roadmap for Task 265

Reference input: `docs/fw-roadmap.txt`

## Purpose

Define the firmware storage and update strategy for the Hexe endpoint firmware
family without forcing one oversized universal image or requiring every mutable
asset to ride inside the main application OTA.

The roadmap keeps the current ESP-IDF firmware useful while moving toward:

- board-profile-specific binaries from one source tree
- dual-slot main application OTA with rollback
- a small recovery/provisioning application
- independently updated config, calibration, media, and wake-word model assets
- explicit partition schema compatibility checks

## Current Baseline

The current native firmware already has a useful OTA foundation:

- ESP32-S3 board profiles for `esp_box_3` and `ha_voice_pe`
- named ESP-IDF partition schemas under `firmware/partitions/`
- `ota_0` and `ota_1` application slots
- backend-pushed OTA events through `/api/firmware/ota/push`
- hosted artifacts under `runtime/firmware`
- signed OTA metadata and SHA-256 image verification
- firmware-side rejection for wrong profile, downgrade/replay, bad signature,
  bad checksum, and bad size

The current active S3 16 MiB partition schema is
`firmware/partitions/s3_16m_v1.csv`, a named development layout:

| Partition | Purpose | Current Size |
| --- | --- | ---: |
| `nvs` | settings and endpoint identity | 16 KiB |
| `otadata` | ESP-IDF OTA metadata | 8 KiB |
| `phy_init` | RF calibration data | 4 KiB |
| `ota_0` | main app slot A | 4 MiB |
| `ota_1` | main app slot B | 4 MiB |
| `storage` | SPIFFS data area | 2 MiB |

The active S3 16 MiB layout does not yet reserve a factory recovery app, model
banks, explicit calibration partitions, or coredump storage. The repository now
has named schema files for the S3 8 MiB, S3 16 MiB, and P4 32 MiB classes, but
the recovery/data-bank schemas are not the active buildable-device default until
the recovery application exists.

`firmware/tools/validate_partition_schema.py` validates board-profile partition
schema mappings, partition overlap/alignment, flash-size fit, OTA slot sizes,
and optional app-binary size gates. `firmware/build.sh` runs it after each
profile build.

## Product Direction

Use one firmware platform with board-specific binaries. Avoid a giant universal
binary that carries unused display, touch, SD, codec, and board-driver code.

Use two application types:

- `endpoint`: the normal voice endpoint application
- `recovery`: a small independent provisioning and rescue application

Do not split normal runtime features into many separately flashed apps. That
would add boot coordination complexity and would not solve the main OTA safety
problem. Instead, split updateable data into signed bundles while keeping one
normal endpoint app per board profile.

## Partition Schema Classes

Partition schemas are firmware contracts. A normal OTA image must declare the
schema it was built for, and the running firmware must reject images for any
other schema.

Initial schema names:

- `s3-8m-v1`
- `s3-16m-v1`
- `p4-32m-v1`

Partition-table changes are not ordinary OTA updates. A schema migration must
use recovery mode, USB/full flashing, or a later dedicated migration mechanism.

### S3 8 MiB Voice-Only Class

Target:

- ReSpeaker XVF3800 plus XIAO ESP32-S3

Starting allocation:

| Area | Target Size | Notes |
| --- | ---: | --- |
| bootloader, partition table, NVS, OTA data, PHY | about 256 KiB | reserve alignment and secure boot growth |
| factory recovery app | 1.0 MiB | provisioning, rescue OTA, basic diagnostics |
| `ota_0` endpoint app | 2.5 MiB | voice-only main app |
| `ota_1` endpoint app | 2.5 MiB | inactive OTA slot |
| model/config/calibration/coredump data | about 1.7 MiB | final value depends on recovery size |

Rationale:

- Current S3 application sizes are about 1.49 to 1.66 MiB after embedded
  Alexa/Stop microWakeWord assets.
- A 2.5 MiB slot leaves meaningful growth room for voice-only hardware.
- Large display/media stacks are excluded from this profile at compile time.

Open validation:

- confirm final XVF3800 app size after its audio frontend is implemented
- compile recovery before locking the factory partition size
- decide whether model A/B banks fit internally or whether only current plus
  embedded fallback is realistic on 8 MiB parts

### S3 16 MiB General Class

Targets:

- Home Assistant Voice PE
- ESP32-S3-BOX-3

Starting allocation:

| Area | Target Size | Notes |
| --- | ---: | --- |
| bootloader, partition table, NVS, OTA data, PHY | about 256 KiB | increase NVS from the current 16 KiB before production |
| factory recovery app | 1.0 to 1.25 MiB | local provisioning and rescue UI/API |
| `ota_0` endpoint app | 4 MiB | keep current app-slot class |
| `ota_1` endpoint app | 4 MiB | inactive OTA slot |
| model bank A | 512 KiB to 1 MiB | signed wake/model bundle |
| model bank B | 512 KiB to 1 MiB | rollback-capable model bundle |
| config/calibration storage | 512 KiB to 1 MiB | endpoint config, audio calibration, passive placement summaries |
| media/assets/storage/coredump | remainder | board-dependent |

Rationale:

- The current development layout already proves 4 MiB main OTA slots.
- Voice PE has no SD card, so internal flash must hold model/config/calibration
  assets.
- ESP-BOX-3 has SD, but the main app must still run without SD for recovery and
  basic voice operation.

Open validation:

- measure recovery app size with display disabled and with ESP-BOX display
  minimal UI enabled
- choose whether ESP-BOX uses internal model banks or SD-first model bundles
- reserve coredump only after production crash-diagnostics policy is decided

### P4 32 MiB Display Class

Target:

- Waveshare ESP32-P4-WIFI6-Touch-LCD-7B

Starting allocation:

| Area | Target Size | Notes |
| --- | ---: | --- |
| bootloader, partition table, NVS, OTA data, PHY | about 512 KiB | allow larger security metadata |
| factory recovery app | 1.5 to 2.0 MiB | minimal display/touch recovery UI |
| `ota_0` endpoint app | 8 MiB | display, touch, C6 transport, audio stack |
| `ota_1` endpoint app | 8 MiB | inactive OTA slot |
| internal fallback assets | 2 to 4 MiB | minimal UI, fallback model, tones |
| config/calibration/coredump/cache | remainder | signed metadata and diagnostics |
| replaceable media/UI/model assets | microSD | versioned bundle directories |

Rationale:

- Display endpoints need larger app slots but should not embed rich UI/media
  assets in the main app image.
- The endpoint must remain provisionable and diagnosable without SD inserted.
- SD holds bulk assets; internal flash holds enough fallback state to remain
  useful.

## Recovery Application Scope

The recovery app is independent from the endpoint runtime.

It owns:

- initial Wi-Fi provisioning
- endpoint enrollment bootstrap
- local rescue OTA upload or Hexe-hosted download
- partition and boot-state inspection
- board-profile and partition-schema checks
- basic PSRAM, storage, audio, display, and touch diagnostics
- configuration reset and selective data erase
- next-boot partition selection

It must not include:

- the full voice pipeline
- normal wake-word inference
- speaker identification
- full LVGL/product UI
- normal automations
- media playback features

Early product policy: do not update recovery through normal OTA. Recovery
updates require USB/full service flashing until a separate signed recovery
update lane is justified.

## Update Lanes

### Main Application OTA

Use the current backend-pushed OTA path as the local-development foundation and
evolve it into channel-based signed releases.

Flow:

1. Backend or recovery provides a signed manifest and artifact URL.
2. Endpoint verifies board profile, SoC, flash size, partition schema, version,
   image size, hash, signature, and anti-rollback policy.
3. Endpoint downloads to the inactive app slot.
4. Endpoint verifies byte count and SHA-256 before finishing OTA.
5. Endpoint reboots into the new image in pending-verification state.
6. New image runs local self-tests.
7. Firmware marks the image valid only after local self-tests pass.
8. Bootloader rolls back after failed boot or failed validation.

Network reachability is not a validity requirement. A valid firmware must not
roll back only because Hexe Core, DNS, MQTT, Wi-Fi, or the Internet is
temporarily unavailable.

### Recovery / Full-Flash Lane

Use for:

- first factory provisioning
- partition-schema changes
- recovery app updates
- devices with two unusable OTA app slots
- repair after data corruption that prevents normal boot

The package includes bootloader, partition table, recovery app, endpoint app,
default model bundle, and signed manifest metadata.

### Model Bundle Lane

Use for wake words, Stop/interruption model, thresholds, and preprocessing
metadata.

Required behavior:

- download to inactive bank or inactive SD directory
- validate manifest, signature, hash, board compatibility, and firmware API
- test-load before activation
- atomically switch active pointer in encrypted NVS or signed `active.json`
- retain previous compatible bundle for rollback
- keep embedded fallback wake model in the main app

### Config and Calibration Lane

Use for board audio frontend parameters, endpoint config overlays, passive
placement calibration summaries, acoustic calibration values, and thresholds.

Rules:

- store sensitive identity and trust data only in encrypted NVS
- store small calibration values in NVS when transactional updates matter
- store larger calibration history in internal storage or SD
- version every calibration schema
- ignore incompatible calibration records after firmware rollback
- never allow calibration data to block recovery boot

### Media and UI Asset Lane

Use for display UI bundles, fonts, prompts, tones, animations, and cached TTS.

Rules:

- do not overwrite active assets in place
- validate full bundle before activation
- keep minimal embedded assets in the main app or recovery app
- SD-equipped devices use versioned directories on SD
- non-SD devices use internal storage only for small assets and required tones

## Manifest Contract

Every release package should declare:

- product id
- release channel
- firmware version and build id
- application type: `endpoint` or `recovery`
- board profile
- SoC
- required flash and PSRAM size
- partition schema
- main firmware API version
- recovery API minimum version
- model API version
- asset API version
- calibration schema compatibility
- bootloader compatibility
- image or bundle size
- SHA-256 hash
- signing-key id
- signature

The current OTA payload already covers profile, URL, version, SHA-256, size,
signature algorithm, key id, and manifest signature. Task 265 extends that
contract with board-profile schema, flash geometry, app type, API compatibility,
and package/bundle metadata.

## Local Validation Before Marking Valid

Common checks:

- partition schema matches compiled expectation
- board profile matches compiled expectation
- encrypted NVS opens and required records are readable
- PSRAM initializes and passes a small allocation test
- audio input initializes
- audio output initializes where present
- embedded fallback wake model loads
- at least one wake-model inference window executes
- internal storage mounts or enters clean fallback mode
- radio/network hardware initializes
- critical firmware tasks stay alive through the validation window

Display checks:

- display initializes
- touch initializes where present
- minimal internal UI renders
- SD absence does not prevent basic provisioning or error display

## Size Guardrails

Use build-time gates for every board profile:

| Slot Class | Warn At | Reject At |
| --- | ---: | ---: |
| 2.5 MiB | 1.875 MiB | 2.125 MiB |
| 4 MiB | 3.0 MiB | 3.4 MiB |
| 8 MiB | 6.0 MiB | 6.8 MiB |

Recovery app guardrails:

- warn at 75 percent of reserved recovery partition
- reject at 85 percent unless the partition table is explicitly revised

## Roadmap

### Phase 0: Freeze Contracts

Deliverables:

- architecture decision record for main app plus recovery app
- partition schema names and target CSV layouts
- release manifest schema
- model-bundle manifest schema
- config/calibration schema compatibility rules
- signing and key-rotation policy

Exit criteria:

- each supported board profile maps to exactly one partition schema
- normal OTA rejects mismatched schema/profile packages
- partition migration is explicitly out of normal OTA scope

### Phase 1: Partition Tooling and Size Gates

Deliverables:

- `firmware/partitions/` with `s3_8m_v1.csv`, `s3_16m_v1.csv`,
  and `p4_32m_v1.csv`
- board-profile build selection for partition CSV
- CI/script validation for flash total, partition alignment, and app size
- generated artifact manifest including partition schema and flash geometry

Exit criteria:

- existing `esp_box_3` and `ha_voice_pe` builds pass the S3 16 MiB guardrails
- size failures produce clear build errors
- runtime heartbeat reports partition schema

### Phase 2: Recovery App Skeleton

Deliverables:

- minimal recovery application target
- recovery-safe board diagnostics API
- physical or boot-counter recovery entry contract
- local recovery status page or display fallback
- no dependency on Core, SD, wake models, or normal endpoint runtime

Exit criteria:

- recovery app boots independently on one S3 board
- recovery app fits the reserved partition
- recovery can report partition/schema/boot status

### Phase 3: Main-App OTA Hardening

Deliverables:

- manifest fields for app type, partition schema, board profile, flash size, and
  firmware API compatibility
- pending-verification boot handling
- local self-test before `esp_ota_mark_app_valid_cancel_rollback`
- rollback test path for failed validation
- clear frontend/backend status for OTA phases and failures

Exit criteria:

- a bad app rolls back automatically
- a valid app does not roll back during network outage
- wrong-board and wrong-schema updates are rejected before download

### Phase 4: Independent Model Bundles

Deliverables:

- signed wake-word bundle format
- internal A/B model banks for S3 profiles where flash allows
- SD versioned model directories for display endpoints
- active bundle pointer with previous-compatible rollback
- embedded fallback model selection

Exit criteria:

- wake/Stop models can update without main firmware OTA
- incompatible model bundles are ignored after app rollback
- endpoint remains wake-capable through fallback assets

### Phase 5: Config, Calibration, Media, and UI Bundles

Deliverables:

- signed config overlay format
- calibration schema versioning and retention policy
- SD/internal media asset bundle format
- minimal embedded fallback assets
- atomic activation and rollback behavior for every mutable bundle

Exit criteria:

- passive placement calibration data survives normal app OTA
- incompatible calibration/config data does not break boot
- SD removal degrades display endpoints into a functional fallback UI

### Phase 6: P4 32 MiB Display Track

Deliverables:

- `waveshare_p4_7b` board profile
- P4 partition schema and build target
- display/touch/audio/radio storage decisions validated against size gates
- SD-first media/model asset strategy

Exit criteria:

- P4 endpoint boots the shared runtime
- main app remains within the 8 MiB slot target
- basic voice/provisioning remains usable without SD

## Immediate Follow-Up Tasks

- Add partition-schema metadata to firmware artifacts and OTA payloads.
- Split partition CSVs into named board-profile classes.
- Add build-time partition and app-size validation.
- Add heartbeat reporting for partition schema and flash geometry.
- Define the recovery app target and its minimum diagnostics API.
- Define signed bundle manifests for wake models, config/calibration, and UI
  assets.
