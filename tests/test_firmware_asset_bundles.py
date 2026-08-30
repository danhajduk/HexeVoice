import hashlib
import hmac
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hexevoice.firmware_asset_bundles import (
    ASSET_BUNDLE_API_VERSION,
    ASSET_BUNDLE_SCHEMA_VERSION,
    ASSET_BUNDLE_TYPES,
    CALIBRATION_SCHEMA_VERSION,
    build_asset_bundle_manifest,
    canonical_asset_bundle_signature_payload,
    sign_asset_bundle_manifest,
    validate_asset_bundle_manifest,
    verify_asset_bundle_signature,
)


@pytest.mark.parametrize("bundle_type", ASSET_BUNDLE_TYPES)
def test_asset_bundle_manifest_defines_signed_activation_contract(tmp_path, bundle_type):
    bundle_root = make_bundle_root(tmp_path, bundle_type)

    manifest = sign_asset_bundle_manifest(
        build_asset_bundle_manifest(
            bundle_root=bundle_root,
            bundle_type=bundle_type,
            created_at_utc="2026-08-30T00:00:00Z",
            board_profiles=["esp_box_3"],
            partition_schemas=["s3-16m-recovery-v1"],
        ),
        signing_key="test-asset-key",
        key_id="test-key",
    )

    assert validate_asset_bundle_manifest(manifest) == []
    assert verify_asset_bundle_signature(manifest, signing_key="test-asset-key")
    assert manifest["schema_version"] == ASSET_BUNDLE_SCHEMA_VERSION
    assert manifest["asset_api_version"] == ASSET_BUNDLE_API_VERSION
    assert manifest["bundle_type"] == bundle_type
    assert manifest["security_policy"] == "signed_manifest_sha256_required"
    assert manifest["compatibility"]["board_profiles"] == ["esp_box_3"]
    assert manifest["compatibility"]["partition_schemas"] == ["s3-16m-recovery-v1"]
    assert manifest["compatibility"]["schema_compatibility"]["mode"] == "migrate"
    assert manifest["compatibility"]["migration_rules"] == []
    assert manifest["activation"]["strategy"] == "atomic_pointer_swap"
    assert manifest["activation"]["test_load_required"] is True
    assert manifest["activation"]["rollback_supported"] is True
    assert manifest["activation"]["cleanup_policy"] == {
        "keep_previous_versions": 2,
        "preserve_active_and_previous": True,
        "remove_unreferenced_after_success": True,
    }
    assert manifest["assets"][0]["sha256"] == sha256(next(bundle_root.iterdir()))


def test_calibration_bundle_uses_stable_calibration_schema_version(tmp_path):
    bundle_root = make_bundle_root(tmp_path, "calibration")
    manifest = sign_asset_bundle_manifest(
        build_asset_bundle_manifest(
            bundle_root=bundle_root,
            bundle_type="calibration",
            created_at_utc="2026-08-30T00:00:00Z",
        ),
        signing_key="test-asset-key",
    )

    assert manifest["compatibility"]["schema_compatibility"]["current"] == CALIBRATION_SCHEMA_VERSION
    assert validate_asset_bundle_manifest(manifest) == []


def test_asset_bundle_signature_uses_canonical_manifest_without_signature(tmp_path):
    bundle_root = make_bundle_root(tmp_path, "ui")
    unsigned = build_asset_bundle_manifest(
        bundle_root=bundle_root,
        bundle_type="ui",
        created_at_utc="2026-08-30T00:00:00Z",
    )
    signed = sign_asset_bundle_manifest(unsigned, signing_key="test-asset-key", key_id="test-key")
    payload = canonical_asset_bundle_signature_payload(signed).encode("utf-8")
    expected = hmac.new(b"test-asset-key", payload, hashlib.sha256).hexdigest()

    assert signed["signature"] == {
        "algorithm": "hmac-sha256",
        "key_id": "test-key",
        "scope": "canonical_manifest_without_signature",
        "value": expected,
    }
    assert "signature" not in json.loads(payload)


def test_asset_bundle_validation_rejects_non_atomic_activation(tmp_path):
    bundle_root = make_bundle_root(tmp_path, "config")
    manifest = sign_asset_bundle_manifest(
        build_asset_bundle_manifest(
            bundle_root=bundle_root,
            bundle_type="config",
            created_at_utc="2026-08-30T00:00:00Z",
        ),
        signing_key="test-asset-key",
    )
    manifest["activation"]["strategy"] = "direct_overwrite"
    manifest["activation"]["cleanup_policy"]["preserve_active_and_previous"] = False

    assert "invalid_activation_strategy" in validate_asset_bundle_manifest(manifest)
    assert "cleanup_must_preserve_active_and_previous" in validate_asset_bundle_manifest(manifest)


def test_create_asset_bundle_manifest_cli_writes_signed_manifest(tmp_path):
    bundle_root = make_bundle_root(tmp_path, "prompt")
    output = tmp_path / "manifest.json"
    subprocess.run(
        [
            sys.executable,
            "firmware/tools/create-asset-bundle-manifest.py",
            "--bundle-root",
            str(bundle_root),
            "--bundle-type",
            "prompt",
            "--created-at-utc",
            "2026-08-30T00:00:00Z",
            "--board-profile",
            "ha_voice_pe",
            "--partition-schema",
            "s3-16m-recovery-v1",
            "--signing-key",
            "test-asset-key",
            "--key-id",
            "test-key",
            "--output",
            str(output),
        ],
        check=True,
    )

    manifest = json.loads(output.read_text())
    assert validate_asset_bundle_manifest(manifest) == []
    assert verify_asset_bundle_signature(manifest, signing_key="test-asset-key")
    assert manifest["bundle_type"] == "prompt"
    assert manifest["compatibility"]["board_profiles"] == ["ha_voice_pe"]
    assert manifest["compatibility"]["partition_schemas"] == ["s3-16m-recovery-v1"]


def make_bundle_root(tmp_path: Path, bundle_type: str) -> Path:
    bundle_root = tmp_path / bundle_type
    bundle_root.mkdir()
    suffix = {
        "config": ".json",
        "calibration": ".json",
        "media": ".wav",
        "prompt": ".txt",
        "tone": ".wav",
        "ui": ".png",
    }[bundle_type]
    (bundle_root / f"{bundle_type}{suffix}").write_bytes(f"{bundle_type}-asset".encode("utf-8"))
    return bundle_root


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
