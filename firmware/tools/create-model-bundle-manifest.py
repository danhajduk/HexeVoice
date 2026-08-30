#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hexevoice.firmware_bundles import (  # noqa: E402
    DEFAULT_MODEL_BUNDLE_ID,
    DEFAULT_MODEL_BUNDLE_KEY_ID,
    DEFAULT_MODEL_BUNDLE_VERSION,
    build_default_model_bundle_manifest,
    sign_model_bundle_manifest,
    validate_model_bundle_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a signed Hexe firmware model-bundle manifest.")
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=ROOT / "firmware/components/endpoint_runtime/voice/models",
        help="Directory containing audio_preprocessor_int8.tflite, alexa.json/tflite, and stop.json/tflite.",
    )
    parser.add_argument("--bundle-id", default=DEFAULT_MODEL_BUNDLE_ID)
    parser.add_argument("--version", default=DEFAULT_MODEL_BUNDLE_VERSION)
    parser.add_argument("--release-channel", default="dev")
    parser.add_argument("--board-profile", action="append", dest="board_profiles")
    parser.add_argument("--partition-schema", action="append", dest="partition_schemas")
    parser.add_argument("--key-id", default=os.environ.get("HEXEVOICE_MODEL_BUNDLE_KEY_ID", DEFAULT_MODEL_BUNDLE_KEY_ID))
    parser.add_argument("--signing-key", default=os.environ.get("HEXEVOICE_MODEL_BUNDLE_SIGNING_KEY"))
    parser.add_argument("--created-at-utc")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = build_default_model_bundle_manifest(
        model_dir=args.model_dir,
        bundle_id=args.bundle_id,
        version=args.version,
        release_channel=args.release_channel,
        board_profiles=args.board_profiles,
        partition_schemas=args.partition_schemas,
        created_at_utc=args.created_at_utc,
    )
    if args.signing_key:
        manifest = sign_model_bundle_manifest(manifest, signing_key=args.signing_key, key_id=args.key_id)

    errors = validate_model_bundle_manifest(manifest)
    if errors:
        parser.error("invalid model-bundle manifest: " + ", ".join(errors))

    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
