# Firmware Model Bundles

Hexe endpoint firmware treats wake-word model updates as a signed mutable
bundle, separate from the firmware image. Firmware OTA carries code and static
embedded fallbacks; model bundles carry microWakeWord assets that can be
activated later from `model_a` or `model_b`.

## Bundle Shape

A model bundle is a directory or archive with one `manifest.json` and the files
named by that manifest:

```text
manifest.json
audio_preprocessor_int8.tflite
alexa.json
alexa.tflite
stop.json
stop.tflite
```

Task 279 defines the signed package contract. Task 280 adds the endpoint
activation boundary: staged model assets must pass a microWakeWord test-load
before the endpoint atomically switches the active pointer. If the active
mutable bank is unavailable after boot, the endpoint selects the embedded
Alexa/Hexe and Stop models instead.

## Manifest Contract

Model-bundle manifests use `schema_version=hexe-model-bundle-v1` and
`model_api_version=hexe-model-bundle-api-v1`. The signed manifest records:

- bundle id, version, creation time, release channel, and security policy
- compatible firmware API range, board profiles, partition schemas, and required
  `model_a`/`model_b` storage banks
- preprocessing metadata for `audio_preprocessor_int8.tflite`, including sample
  rate, feature step, size, and SHA-256
- one wake model for Alexa with `alias=Hexe`
- one playback stop model for `Stop`
- per-model thresholds: probability cutoff, sliding window, feature step, and
  tensor arena size
- size and SHA-256 for every package file
- HMAC-SHA256 signature over the canonical manifest without the `signature`
  field

The local development signing algorithm matches firmware OTA metadata signing:
`hmac-sha256`. Production release jobs should replace local HMAC keys with the
final Hexe release-signing trust domain before public distribution.

## Current Embedded Bundle

The default manifest emitted by `firmware/tools/create-model-bundle-manifest.py`
is derived from the checked-in firmware assets:

| Role | Wake word | Alias | Model | Probability cutoff | Window | Feature step | Arena |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| wake | Alexa | Hexe | `alexa.tflite` | 0.90 | 5 | 10 ms | 22348 |
| playback_stop | Stop |  | `stop.tflite` | 0.50 | 5 | 10 ms | 21000 |

Create and validate a signed development manifest with:

```bash
HEXEVOICE_MODEL_BUNDLE_SIGNING_KEY=local-dev-key \
python firmware/tools/create-model-bundle-manifest.py \
  --output /tmp/hexe-model-bundle-manifest.json
```

The tool refuses unsigned output during normal validation because endpoints must
never activate mutable model assets from an unsigned manifest.

## Endpoint Activation

The endpoint firmware exposes a model-bundle manager behind
`voice/model_bundle.{h,cpp}`. It supports:

- internal A/B banks named `model_a` and `model_b`
- SD versioned bundle directories rooted at `/sdcard/hexe/models/`
- compatibility checks for `hexe-model-bundle-api-v1` and the compiled
  partition schema
- test-loading through the microWakeWord model validator before activation
- atomic NVS updates of active and previous bundle pointers
- rollback by swapping the active and previous pointers
- embedded fallback selection when no valid mutable assets are loaded

The current firmware boots with embedded fallback models. A future downloader
can stage signed bundle files into `model_a`, `model_b`, or an SD versioned
directory, then pass the loaded model assets to the activation manager without
changing the wake/Stop runtime path.
