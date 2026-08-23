# Firmware Reconnect And Session Boundary Validation

Use this procedure before a firmware release to record physical-device reconnect
and session-boundary evidence for every supported profile.

## Field Rig

Run the backend and connect the endpoint under test. For the physical bench,
use the guided shell wrapper so one device can be validated at a time:

```bash
scripts/firmware-reconnect-session-bench.sh \
  --backend-url http://127.0.0.1:9004 \
  --profile esp_box_3=esp-box-1 \
  --operator "<name>" \
  --release-id "<release-or-build-id>" \
  --output docs/firmware-reconnect-session-results.json

scripts/firmware-reconnect-session-bench.sh \
  --backend-url http://127.0.0.1:9004 \
  --profile ha_voice_pe=esp-pe-1 \
  --operator "<name>" \
  --release-id "<release-or-build-id>" \
  --output docs/firmware-reconnect-session-results.json
```

The wrapper lists detected endpoints when `--profile` is omitted. For each
scenario, it prints the physical action, waits for the operator, collects
backend observations, and asks for `pass`, `fail`, or `blocked`. It merges the
selected profile into the existing release artifact without overwriting the
other profile's recorded results.

The lower-level runner can still record both profiles in one pass:

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

At each physical prompt, press Enter after performing the step to collect
backend observations. Type `s` or `skip` only when the step cannot be performed;
the scenario is recorded as `blocked` and must have follow-up work before
release approval.

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
