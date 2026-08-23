# Firmware Validation Matrix

HexeVoice currently supports two firmware hardware profiles:

- `esp_box_3` - ESP-BOX-3 touchscreen/display target with SD media support.
- `ha_voice_pe` - Home Assistant Voice Preview Edition audio/LED-ring target with no panel display and NVS-only storage.

The machine-readable source for this matrix is
[`docs/firmware-validation-matrix.json`](firmware-validation-matrix.json). Keep
that file updated when adding a profile or changing validation status.
Reconnect/session-boundary field evidence is recorded in
[`docs/firmware-reconnect-session-results.json`](firmware-reconnect-session-results.json)
using the procedure in
[`docs/firmware-reconnect-session-validation.md`](firmware-reconnect-session-validation.md).

## Release Gate

Run the automated profile coverage before a firmware release:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_firmware_validation_matrix.py tests/test_firmware_voice_envelope.py tests/test_voice_loop_integration.py tests/test_voice_websocket.py
```

When ESP-IDF is available, also build both supported profiles:

```bash
cd firmware
./build.sh
```

Unsupported `HEXE_BOARD_PROFILE` values must remain rejected by
`firmware/build.sh`. A profile that is not listed in the JSON matrix is
unvalidated and should not be presented as production-ready to operators.

Run the reconnect/session-boundary field rig before release:

```bash
scripts/firmware-reconnect-session-validation.py --backend-url http://127.0.0.1:9004 --output docs/firmware-reconnect-session-results.json
```

## Manual Field Checks

The JSON matrix lists manual checks for the hardware-bound portions of each
profile. Before release, record results for:

- audio streaming from endpoint microphone to `/api/voice/ws`
- wake acceptance and below-threshold/no-wake behavior
- TTS playback, replay, and endpoint playback telemetry
- display or LED-ring state changes for each voice phase
- OTA, media delivery, and inventory behavior appropriate to the profile
- mute/volume controls and conflict handling
- backend restart, endpoint power-cycle, and reconnect behavior

`validation_state: "partial"` means automated coverage exists but field
validation is still required. Do not mark a profile as fully validated until all
manual checks for that profile have a passing run on physical hardware.
