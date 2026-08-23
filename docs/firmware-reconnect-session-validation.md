# Firmware Reconnect And Session Boundary Validation

Use this procedure before a firmware release to record physical-device reconnect
and session-boundary evidence for every supported profile.

## Field Rig

Run the backend, connect the endpoint under test, then run:

```bash
scripts/firmware-reconnect-session-validation.py \
  --backend-url http://127.0.0.1:9004 \
  --profile esp_box_3=esp-box-1 \
  --profile ha_voice_pe=esp-pe-1 \
  --operator "<name>" \
  --release-id "<release-or-build-id>" \
  --output docs/firmware-reconnect-session-results.json
```

The script steps the operator through:

- backend restart while idle
- endpoint power cycle
- Wi-Fi loss and rejoin
- active-session disconnect
- post-TTS cooldown
- wake retry after rejected wake
- duplicate-session prevention

For unattended template generation or CI checks, use `--non-interactive`. That
mode records `blocked` unless explicit `--result PROFILE:SCENARIO=pass|fail|blocked`
overrides are provided by a field operator or test harness.

## Release Artifact

The current release-review artifact is
[`docs/firmware-reconnect-session-results.json`](firmware-reconnect-session-results.json).
It records `pass`, `fail`, or `blocked` for each scenario and profile. A release
cannot treat reconnect/session-boundary behavior as validated while any scenario
is `fail` or `blocked`.

Create or link follow-up tasks for every `fail` or `blocked` scenario before
release approval. The initial repo artifact is intentionally blocked because no
physical endpoints were attached during this repo-side run. Task 218 tracks the
required bench run that replaces those blocked entries with operator-recorded
pass/fail results.
