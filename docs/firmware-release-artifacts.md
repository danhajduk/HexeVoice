# Firmware Release Artifacts

HexeVoice installs endpoint firmware from a separate release/artifact repo by
default. The expected GitHub release source is:

Production signing, key rotation, secure boot, flash encryption,
manufacturing, recovery, and field-service gates are defined in
`docs/firmware-production-readiness.md`.

```text
danhajduk/HexeFirmware
```

The installer downloads the latest release unless overridden:

```bash
HEXEVOICE_FIRMWARE_GITHUB_REPOSITORY=danhajduk/HexeFirmware \
HEXEVOICE_FIRMWARE_RELEASE_TAG=latest \
./scripts/firmware-artifacts-control.sh download
```

Each endpoint release should attach these assets:

```text
hexe_firmware.bin
hexe_firmware_esp_box_3.bin
hexe_firmware_ha_voice_pe.bin
manifest.json
manifest-esp_box_3.json
manifest-ha_voice_pe.json
SHA256SUMS
```

Mutable endpoint assets use their own signed bundle manifests. Wake-word model
bundles are defined in `docs/firmware-model-bundles.md`; config, calibration,
media, prompt, tone, and UI bundles are defined in
`docs/firmware-asset-bundles.md`. Firmware releases keep the embedded
Alexa/Hexe and Stop models as fallbacks while later mutable bundle activation
can update `model_a`/`model_b` without reflashing the endpoint app.

The generated profile manifests include the static firmware release contract:
application type, board profile, SoC/IDF target, flash size, PSRAM size,
partition schema, app slot size, firmware/model/asset/calibration API versions,
release channel, security policy, image size, SHA-256, signing key id,
signature algorithm, and a signature scope that records that OTA payloads are
signed by the backend when delivered. The default local release channel is
`dev`; production release jobs should set `FIRMWARE_RELEASE_CHANNEL=stable`.
Endpoint OTA accepts only the `signed_manifest_sha256_required` security policy.

The active S3 endpoint profiles use `s3-16m-recovery-v1`, which reserves a
2 MiB factory recovery app and two 4 MiB endpoint OTA slots. Moving a device
from the legacy `s3-16m-v1` layout to this recovery-capable layout requires
USB/full flash because normal endpoint OTA cannot replace the partition table.

Flash export folders also include `flash-esptool.sh` and
`provisioning.env.example`. To pre-provision a device during USB flashing, copy
the example to `provisioning.env` inside the export folder and set the endpoint
id, display name, backend host/ports, TLS flag, and Wi-Fi credentials. The flash
helper converts that local text file into an ESP-IDF NVS image and writes it to
the firmware NVS partition at `0x9000`, which the firmware reads on boot.

`provisioning.env` can contain Wi-Fi credentials and must stay local. Do not
publish it as a release asset.

Build/export from the firmware source tree, then publish the contents of
`runtime/firmware` as release assets:

```bash
cd firmware
./build.sh
./export-artifacts.sh
cd ..
./scripts/firmware-artifacts-control.sh verify
```

The installer writes downloaded artifacts into `runtime/firmware` and verifies
required board profiles plus `SHA256SUMS` when present. A missing release should
be treated as a retryable setup failure, not a reason to block the rest of node
setup.

`firmware/build.sh` stamps OTA artifacts with an ESP-IDF project version shaped
like `zYYYYMMDDHHMMSS-<git-sha>`. The leading `z` and fixed UTC timestamp keep
versions lexically sortable for firmware OTA downgrade/replay checks, including
devices that were previously flashed with raw git-hash versions. The build
script refuses tracked uncommitted changes by default so runtime/OTA artifacts
do not carry `_dirty` versions.

If the configured release is unavailable, `download` falls back to building the
firmware locally with `firmware/build.sh build` when
`HEXEVOICE_FIRMWARE_BUILD_FALLBACK=true`, which is the default. The fallback
requires ESP-IDF on the target host and still reports a retryable setup failure
if the build toolchain is missing.
