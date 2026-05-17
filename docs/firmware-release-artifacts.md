# Firmware Release Artifacts

HexeVoice installs endpoint firmware from a separate release/artifact repo by
default. The expected GitHub release source is:

```text
danhajduk/HexeFirmware
```

The installer downloads the latest release unless overridden:

```bash
HEXEVOICE_FIRMWARE_GITHUB_REPOSITORY=danhajduk/HexeFirmware \
HEXEVOICE_FIRMWARE_RELEASE_TAG=latest \
./scripts/firmware-artifacts-control.sh download
```

Each release should attach these assets:

```text
hexe_firmware.bin
hexe_firmware_esp_box_3.bin
hexe_firmware_ha_voice_pe.bin
manifest.json
manifest-esp_box_3.json
manifest-ha_voice_pe.json
SHA256SUMS
```

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

If the configured release is unavailable, `download` falls back to building the
firmware locally with `firmware/build.sh build` when
`HEXEVOICE_FIRMWARE_BUILD_FALLBACK=true`, which is the default. The fallback
requires ESP-IDF on the target host and still reports a retryable setup failure
if the build toolchain is missing.
