# Firmware Production Readiness

Status: Task 286 production-readiness contract. This document defines the
release, manufacturing, security, recovery, and field-service gates required
before Hexe endpoint firmware is treated as production-ready.

References:

- `docs/fw-roadmap.txt` phase 8 hardening and production-readiness goals
- `docs/firmware-release-artifacts.md`
- `docs/firmware-validation-matrix.md`
- `docs/firmware-recovery-architecture.md`
- `docs/firmware-provisioning.md`
- Espressif ESP32-S3 security feature workflow:
  <https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/security/security-features-enablement-workflows.html>
- Espressif ESP32-S3 flash encryption:
  <https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/security/flash-encryption.html>
- Espressif Secure Boot v2:
  <https://docs.espressif.com/projects/esp-idf/en/stable/esp32/security/secure-boot-v2.html>

## Production Gate

Production firmware release approval requires all of these to be true:

- Endpoint and recovery artifacts are signed by a trusted production key.
- Firmware, model, config, calibration, media, prompt, tone, and UI bundles use
  signed manifests with SHA-256 verification before activation.
- Development-only unsigned or local HMAC signing paths are disabled for
  production release channels.
- Flash Encryption release mode and Secure Boot v2 are enabled during
  manufacturing for production devices.
- Each device has a unique flash-encryption key per physical device and a
  recorded, non-secret manufacturing identity record.
- Wi-Fi credentials, trust tokens, provisioning payloads, raw credential
  payloads, private signing keys, and private identity material are never
  written to release artifacts, logs, SD cards, diagnostics, or field-service
  reports.
- Recovery remains available without Core, SD card, wake models, or the normal
  endpoint runtime.
- Field-service access is explicit, auditable, and does not bypass signing,
  anti-rollback, credential redaction, or recovery partition protections.
- The hardening cases in `docs/firmware-validation-matrix.json` have current
  pass evidence for each applicable supported board profile.

Current repository status: development artifacts use local signing and local
provisioning helpers. Production secure boot, flash encryption, key ceremony,
anti-rollback enforcement, and manufacturing station automation are gates
defined here, not completed production enablement.

## Signing Policy

Release artifacts are split into independently signed lanes:

| Lane | Artifact | Activation owner | Production rule |
| --- | --- | --- | --- |
| Endpoint app | `application_type=endpoint` image and manifest | endpoint OTA client or recovery install API | Signed image and signed metadata required before download/install |
| Recovery app | `application_type=recovery` image and manifest | factory/service flashing process | Signed image required; normal endpoint OTA must not update recovery |
| Wake/Stop models | `hexe-model-bundle-v1` | endpoint model-bundle manager | Signed manifest, SHA-256 files, compatibility check, test-load before active pointer switch |
| Config/calibration/media/prompt/tone/UI | `hexe-asset-bundle-v1` | endpoint asset-bundle activation path | Signed manifest, SHA-256 files, migration/compatibility check, atomic activation |

Production releases must set a stable release channel, use production key ids,
and reject the `dev` channel unless the device is explicitly in a development
configuration. A production device must fail closed when a signature, hash,
board profile, SoC, flash geometry, partition schema, application type,
firmware API, model API, asset API, calibration schema, release channel, or
security policy does not match.

Private release keys must never be present in this repository, firmware export
folders, SD cards, recovery uploads, CI logs, or operator diagnostics.

## Key Rotation

Key rotation is required for both firmware images and mutable bundles.

Production key records must track:

- key id
- lane: endpoint, recovery, model, asset, or manufacturing
- creation time
- allowed release channels
- active, staged, retired, or revoked state
- minimum and maximum accepted firmware or bundle API versions
- reason and date for retirement or revocation

Rotation rules:

- New keys are introduced as staged trust roots before they become the only
  signing key for a lane.
- Firmware must be able to accept the current production key and at least one
  staged replacement key before a forced key cutover.
- Retired keys may remain accepted only for a bounded rollback window.
- Revoked keys must be rejected for all future installs and bundle activations.
- Recovery must honor the same endpoint image trust policy when installing main
  firmware during local repair.
- Key rotation must be tested with old firmware/new bundle, new firmware/old
  bundle, and rollback scenarios from the validation matrix.

Anti-rollback should be enabled only after the release cadence and recovery
process can safely recover devices that reject old images. Once enabled, every
production release must carry a monotonic security version compatible with the
device eFuse policy.

## Secure Boot And Flash Encryption

Production ESP32-S3 devices must use Flash Encryption release mode and Secure
Boot v2 together. The manufacturing process must follow Espressif's ordering:
enable flash encryption first, then enable Secure Boot v2, because later eFuse
write/read protections can make key protection steps irreversible.

Manufacturing requirements:

- Use a unique flash-encryption key per physical device.
- Burn flash-encryption and secure-boot eFuses only after the device has passed
  board identity, flash, PSRAM, button, mute, audio, display/LED, and recovery
  smoke checks.
- Build bootloader and app images with production security settings.
- Sign bootloader, endpoint, and recovery images with trusted production keys.
- Encrypt only partitions that are marked encrypted in the partition table, and
  use the correct address offsets for each board profile.
- Disable plaintext production flashing paths before shipment.
- Delete host-side flash-encryption keys after provisioning when the process
  uses externally generated encryption keys.
- Store only non-secret evidence in manufacturing records.

Manufacturing must stop if any eFuse state is unexpected. eFuse burns are
one-way operations, so a device with mismatched board profile, key purpose,
flash size, secure-version state, or download-mode policy must be quarantined
for service review instead of retried blindly.

## Provisioning And Manufacturing Flow

Factory provisioning must create a device that can boot, recover, and join the
normal Hexe onboarding path without leaking credentials.

Required manufacturing stations:

1. Board intake: scan or assign serial number, board profile, hardware revision,
   MAC-derived hardware id, enclosure revision, and production lot.
2. Open-device smoke test: verify flash, PSRAM, buttons, mute control, audio
   input/output, display/touch or LED status, SD behavior if present, and radio
   connectivity.
3. Secure image flash: write bootloader, partition table, recovery app, endpoint
   app, OTA data, NVS defaults, and any required encrypted partitions.
4. Security enablement: burn flash-encryption and secure-boot eFuses following
   the approved ceremony, then verify the final eFuse summary.
5. Provisioning: write endpoint defaults, trusted release roots, and optional
   factory network settings through NVS generation or the recovery API.
6. Recovery check: boot recovery, confirm `/api/recovery/status`,
   `/api/recovery/partitions`, and local firmware install validation without
   Core.
7. Endpoint check: boot endpoint, confirm heartbeat identity, firmware metadata,
   model fallback readiness, controls, audio path, and backend connection.
8. Final enclosure check: verify microphone openings, speaker openings, mute
   control access, service access, thermal clearance, labels, and reset/recovery
   affordances after assembly.
9. Record release: store non-secret artifact ids, manifest hashes, key ids,
   board profile, partition schema, test results, and operator initials.

`provisioning.env` remains a local development and factory-input file only. It
must not be published as a release artifact or copied to SD cards. Manufacturing
logs may record that Wi-Fi or backend settings were provisioned, but never the
plaintext values.

## Recovery And Field Service

Recovery is the supported field-service surface for broken or unconfigured main
firmware. It must remain smaller, plainer, and more independent than the normal
endpoint app.

Field-service rules:

- Service technicians may install signed endpoint images through recovery.
- Recovery must reject recovery-app updates until a separate signed
  recovery-update lane is designed.
- Recovery diagnostics must redact secrets and remain useful without Core.
- Local temporary AP mode must be visibly a service/recovery mode, not normal
  operation.
- Selective reset must distinguish Wi-Fi, endpoint identity, provisioning,
  settings, calibration, and mutable bundle state.
- Boot-slot selection must be limited to valid endpoint OTA partitions.
- Recovery must not link wake-word, Speaker ID, STT, TTS, assistant session, or
  normal endpoint dashboard logic.
- Service access must require deliberate physical access or an approved
  operator action. It must not be reachable as a silent remote backdoor.

Field reports must include device id, board profile, firmware version, recovery
version, partition schema, failure category, action taken, and final boot state.
They must not include passwords, trust tokens, private keys, raw recordings, or
speaker embeddings.

## Enclosure And Hardware Readiness

Hardware is production-ready only when firmware behavior and physical design are
validated together.

Required physical checks:

- Microphone openings support the board's intended near-field and far-field
  pickup without rubbing, blockage, or orientation ambiguity.
- Speaker openings avoid rattling, clipping, overheating, and direct acoustic
  feedback into the microphones at normal response volume.
- AEC or fallback behavior is validated for wake during TTS/music playback.
- Hardware mute is obvious, reachable, and reports a trusted muted state in
  firmware and operator UI.
- Buttons, rotary controls, touch controls, LEDs, and displays match the board
  profile and do not trigger unintended sessions.
- SD access, if present, has a safe insertion/removal path and cannot expose
  private keys or plaintext credentials.
- Service access is deliberate and documented: recovery gesture, serial pads,
  USB port, reset path, and enclosure opening process.
- Thermal and power behavior passes long-duration room-use testing from the
  validation matrix.

Board profiles that have not completed these physical checks may be buildable
for development but must not be marked production-ready.

## Release Evidence

Before shipment or production deployment, keep a release evidence package with:

- git commit and clean build status
- endpoint and recovery artifact names, versions, sizes, SHA-256 values, and
  manifest signatures
- signing key ids and release channel
- board profile and partition schema
- secure boot and flash encryption eFuse summary
- provisioning method and redacted provisioning status
- validation matrix result ids, including long-duration and fault-injection runs
- recovery boot and signed repair-install result
- enclosure/audio/mute/service-access signoff

The evidence package may include logs only after secret redaction. Raw
credential payloads, private keys, trust tokens, exact Wi-Fi passwords, raw
voice recordings, and speaker embeddings are forbidden.
