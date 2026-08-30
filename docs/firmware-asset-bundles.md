# Firmware Asset Bundles

Hexe endpoint firmware treats config, calibration, media, prompt, tone, and UI
updates as signed mutable asset bundles. These bundles are separate from the
endpoint app image and from wake-word model bundles, so routine content changes
do not require reflashing firmware.

## Bundle Types

| Type | Default role | Default storage |
| --- | --- | --- |
| `config` | `endpoint_config` | `nvs_namespace` |
| `calibration` | `audio_calibration` | `nvs_namespace` |
| `media` | `media_asset` | `spiffs_versioned_directory` |
| `prompt` | `prompt_asset` | `spiffs_versioned_directory` |
| `tone` | `tone_asset` | `spiffs_versioned_directory` |
| `ui` | `ui_asset` | `spiffs_versioned_directory` |

## Manifest Contract

Asset-bundle manifests use `schema_version=hexe-asset-bundle-v1` and
`asset_api_version=hexe-asset-bundle-api-v1`. Each manifest records:

- bundle id, bundle type, version, creation time, release channel, and security
  policy
- compatible firmware API range, board profiles, and partition schemas
- schema compatibility mode, current schema version, accepted schema versions,
  and explicit migration rules
- activation storage, atomic pointer-swap strategy, active and rollback pointer
  names, required test-load, rollback support, and cleanup policy
- per-asset path, role, content type, size, and SHA-256
- HMAC-SHA256 signature over the canonical manifest without the `signature`
  field

Calibration bundles use `hexe-calibration-schema-v1` as their current schema
version. Other asset types use `hexe-<type>-asset-schema-v1` until a more
specific contract is needed.

## Activation Rules

Endpoint or backend activation must stage all files first, verify the signed
manifest, validate the target board and partition schema, test-load the bundle,
then update only the active pointer. The previous pointer is retained for
rollback. Cleanup may remove unreferenced versions after activation succeeds,
but it must preserve the active and previous versions.

Create and validate a signed development manifest with:

```bash
HEXEVOICE_ASSET_BUNDLE_SIGNING_KEY=local-dev-key \
python firmware/tools/create-asset-bundle-manifest.py \
  --bundle-root /path/to/assets \
  --bundle-type ui \
  --output /tmp/hexe-ui-asset-bundle-manifest.json
```

The tool refuses unsigned output during normal validation because endpoints must
never activate mutable config, calibration, media, prompt, tone, or UI assets
from an unsigned manifest.
