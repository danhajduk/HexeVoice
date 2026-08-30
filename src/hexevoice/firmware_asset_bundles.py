from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import hmac
import json
import mimetypes
from pathlib import Path
import re
from typing import Any, Mapping

from hexevoice.firmware_bundles import (
    DEFAULT_COMPATIBLE_BOARD_PROFILES,
    DEFAULT_COMPATIBLE_PARTITION_SCHEMAS,
    sha256_file,
)


ASSET_BUNDLE_SCHEMA_VERSION = "hexe-asset-bundle-v1"
ASSET_BUNDLE_API_VERSION = "hexe-asset-bundle-api-v1"
CALIBRATION_SCHEMA_VERSION = "hexe-calibration-schema-v1"
ASSET_BUNDLE_SIGNATURE_ALGORITHM = "hmac-sha256"
ASSET_BUNDLE_SECURITY_POLICY = "signed_manifest_sha256_required"
ASSET_BUNDLE_SIGNATURE_SCOPE = "canonical_manifest_without_signature"
DEFAULT_ASSET_BUNDLE_VERSION = "2026.08.30"
DEFAULT_ASSET_BUNDLE_KEY_ID = "hexevoice-asset-dev-v1"
ASSET_BUNDLE_TYPES = ("config", "calibration", "media", "prompt", "tone", "ui")

_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_ROLE_BY_TYPE = {
    "config": "endpoint_config",
    "calibration": "audio_calibration",
    "media": "media_asset",
    "prompt": "prompt_asset",
    "tone": "tone_asset",
    "ui": "ui_asset",
}
_DEFAULT_STORAGE_BY_TYPE = {
    "config": "nvs_namespace",
    "calibration": "nvs_namespace",
    "media": "spiffs_versioned_directory",
    "prompt": "spiffs_versioned_directory",
    "tone": "spiffs_versioned_directory",
    "ui": "spiffs_versioned_directory",
}


class FirmwareAssetBundleError(ValueError):
    pass


def canonical_asset_bundle_signature_payload(manifest: Mapping[str, Any]) -> str:
    unsigned_manifest = dict(manifest)
    unsigned_manifest.pop("signature", None)
    return json.dumps(unsigned_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sign_asset_bundle_manifest(
    manifest: Mapping[str, Any],
    *,
    signing_key: str,
    key_id: str = DEFAULT_ASSET_BUNDLE_KEY_ID,
) -> dict[str, Any]:
    if not signing_key:
        raise FirmwareAssetBundleError("An asset-bundle signing key is required.")
    signed_manifest = dict(manifest)
    payload = canonical_asset_bundle_signature_payload(signed_manifest).encode("utf-8")
    signed_manifest["signature"] = {
        "algorithm": ASSET_BUNDLE_SIGNATURE_ALGORITHM,
        "key_id": key_id,
        "scope": ASSET_BUNDLE_SIGNATURE_SCOPE,
        "value": hmac.new(signing_key.encode("utf-8"), payload, hashlib.sha256).hexdigest(),
    }
    return signed_manifest


def verify_asset_bundle_signature(manifest: Mapping[str, Any], *, signing_key: str) -> bool:
    signature = manifest.get("signature")
    if not isinstance(signature, Mapping):
        return False
    if signature.get("algorithm") != ASSET_BUNDLE_SIGNATURE_ALGORITHM:
        return False
    value = signature.get("value")
    if not isinstance(value, str) or not _HEX_SHA256_RE.fullmatch(value):
        return False
    expected = sign_asset_bundle_manifest(manifest, signing_key=signing_key, key_id=str(signature.get("key_id", "")))
    return hmac.compare_digest(value, expected["signature"]["value"])


def build_asset_bundle_manifest(
    *,
    bundle_root: Path,
    bundle_type: str,
    bundle_id: str | None = None,
    version: str = DEFAULT_ASSET_BUNDLE_VERSION,
    release_channel: str = "dev",
    board_profiles: list[str] | None = None,
    partition_schemas: list[str] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    if bundle_type not in ASSET_BUNDLE_TYPES:
        raise FirmwareAssetBundleError(f"Unsupported asset bundle type: {bundle_type}")
    bundle_root = bundle_root.resolve()
    if not bundle_root.is_dir():
        raise FirmwareAssetBundleError(f"Asset bundle root does not exist: {bundle_root}")

    files = sorted(path for path in bundle_root.rglob("*") if path.is_file() and path.name != "manifest.json")
    if not files:
        raise FirmwareAssetBundleError(f"Asset bundle has no files: {bundle_root}")

    created_at_utc = created_at_utc or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    asset_schema_version = _asset_schema_version(bundle_type)
    assets = [_asset_entry(bundle_root, path, bundle_type) for path in files]

    return {
        "schema_version": ASSET_BUNDLE_SCHEMA_VERSION,
        "asset_api_version": ASSET_BUNDLE_API_VERSION,
        "bundle_id": bundle_id or f"hexe-{bundle_type}-bundle",
        "bundle_type": bundle_type,
        "version": version,
        "created_at_utc": created_at_utc,
        "release_channel": release_channel,
        "security_policy": ASSET_BUNDLE_SECURITY_POLICY,
        "compatibility": {
            "firmware_api_version": {
                "min": "hexe-firmware-main-api-v1",
                "max": "hexe-firmware-main-api-v1",
            },
            "board_profiles": board_profiles or DEFAULT_COMPATIBLE_BOARD_PROFILES,
            "partition_schemas": partition_schemas or DEFAULT_COMPATIBLE_PARTITION_SCHEMAS,
            "schema_compatibility": {
                "mode": "migrate",
                "current": asset_schema_version,
                "accepted": [asset_schema_version],
                "migration_required": False,
            },
            "migration_rules": [],
        },
        "activation": {
            "storage": _DEFAULT_STORAGE_BY_TYPE[bundle_type],
            "strategy": "atomic_pointer_swap",
            "active_pointer": f"asset.{bundle_type}.active",
            "rollback_pointer": f"asset.{bundle_type}.previous",
            "test_load_required": True,
            "rollback_supported": True,
            "cleanup_policy": {
                "keep_previous_versions": 2,
                "remove_unreferenced_after_success": True,
                "preserve_active_and_previous": True,
            },
        },
        "assets": assets,
        "hashes": [{"path": asset["path"], "size_bytes": asset["size_bytes"], "sha256": asset["sha256"]} for asset in assets],
    }


def validate_asset_bundle_manifest(manifest: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _require_equal(errors, manifest, "schema_version", ASSET_BUNDLE_SCHEMA_VERSION)
    _require_equal(errors, manifest, "asset_api_version", ASSET_BUNDLE_API_VERSION)
    _require_equal(errors, manifest, "security_policy", ASSET_BUNDLE_SECURITY_POLICY)
    bundle_type = manifest.get("bundle_type")
    if bundle_type not in ASSET_BUNDLE_TYPES:
        errors.append("invalid_bundle_type")
    for field in ["bundle_id", "version", "created_at_utc", "release_channel"]:
        if not isinstance(manifest.get(field), str) or not str(manifest.get(field)).strip():
            errors.append(f"missing_{field}")

    _validate_compatibility(errors, manifest.get("compatibility"), bundle_type if isinstance(bundle_type, str) else "")
    _validate_activation(errors, manifest.get("activation"))
    _validate_assets(errors, manifest.get("assets"), bundle_type if isinstance(bundle_type, str) else "")
    _validate_hashes(errors, manifest.get("hashes"))
    _validate_signature(errors, manifest.get("signature"))
    return errors


def _asset_schema_version(bundle_type: str) -> str:
    if bundle_type == "calibration":
        return CALIBRATION_SCHEMA_VERSION
    return f"hexe-{bundle_type}-asset-schema-v1"


def _asset_entry(bundle_root: Path, path: Path, bundle_type: str) -> dict[str, Any]:
    relative_path = path.relative_to(bundle_root).as_posix()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "path": relative_path,
        "role": _DEFAULT_ROLE_BY_TYPE[bundle_type],
        "content_type": content_type,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _validate_compatibility(errors: list[str], compatibility: Any, bundle_type: str) -> None:
    if not isinstance(compatibility, Mapping):
        errors.append("missing_compatibility")
        return
    firmware_api_version = compatibility.get("firmware_api_version")
    if not isinstance(firmware_api_version, Mapping) or not firmware_api_version.get("min") or not firmware_api_version.get("max"):
        errors.append("missing_compatible_firmware_api_version")
    if not _non_empty_string_list(compatibility.get("board_profiles")):
        errors.append("missing_compatible_board_profiles")
    if not _non_empty_string_list(compatibility.get("partition_schemas")):
        errors.append("missing_compatible_partition_schemas")

    schema_compatibility = compatibility.get("schema_compatibility")
    if not isinstance(schema_compatibility, Mapping):
        errors.append("missing_schema_compatibility")
    else:
        if schema_compatibility.get("mode") not in {"exact", "compatible", "migrate"}:
            errors.append("invalid_schema_compatibility_mode")
        if schema_compatibility.get("current") != _asset_schema_version(bundle_type):
            errors.append("invalid_schema_compatibility_current")
        if not _non_empty_string_list(schema_compatibility.get("accepted")):
            errors.append("missing_schema_compatibility_accepted")
        if not isinstance(schema_compatibility.get("migration_required"), bool):
            errors.append("invalid_schema_compatibility_migration_required")

    migration_rules = compatibility.get("migration_rules")
    if not isinstance(migration_rules, list):
        errors.append("missing_migration_rules")
        return
    for index, rule in enumerate(migration_rules):
        if not isinstance(rule, Mapping):
            errors.append(f"invalid_migration_rules.{index}")
            continue
        for field in ["id", "from", "to", "strategy"]:
            if not isinstance(rule.get(field), str) or not rule.get(field):
                errors.append(f"missing_migration_rules.{index}.{field}")
        if rule.get("strategy") not in {"no_op", "rename_key", "transform_json", "regenerate"}:
            errors.append(f"invalid_migration_rules.{index}.strategy")


def _validate_activation(errors: list[str], activation: Any) -> None:
    if not isinstance(activation, Mapping):
        errors.append("missing_activation")
        return
    if activation.get("storage") not in {"nvs_namespace", "internal_ab_bank", "sd_versioned_directory", "spiffs_versioned_directory"}:
        errors.append("invalid_activation_storage")
    if activation.get("strategy") != "atomic_pointer_swap":
        errors.append("invalid_activation_strategy")
    for field in ["active_pointer", "rollback_pointer"]:
        if not isinstance(activation.get(field), str) or not activation.get(field):
            errors.append(f"missing_activation_{field}")
    if activation.get("test_load_required") is not True:
        errors.append("activation_requires_test_load")
    if activation.get("rollback_supported") is not True:
        errors.append("activation_requires_rollback")

    cleanup_policy = activation.get("cleanup_policy")
    if not isinstance(cleanup_policy, Mapping):
        errors.append("missing_cleanup_policy")
        return
    if not isinstance(cleanup_policy.get("keep_previous_versions"), int) or cleanup_policy.get("keep_previous_versions") < 1:
        errors.append("invalid_cleanup_keep_previous_versions")
    if cleanup_policy.get("remove_unreferenced_after_success") is not True:
        errors.append("cleanup_must_remove_unreferenced_after_success")
    if cleanup_policy.get("preserve_active_and_previous") is not True:
        errors.append("cleanup_must_preserve_active_and_previous")


def _validate_assets(errors: list[str], assets: Any, bundle_type: str) -> None:
    if not isinstance(assets, list) or not assets:
        errors.append("missing_assets")
        return
    expected_role = _DEFAULT_ROLE_BY_TYPE.get(bundle_type)
    for index, asset in enumerate(assets):
        _validate_asset(errors, asset, f"assets.{index}", expected_role)


def _validate_hashes(errors: list[str], hashes: Any) -> None:
    if not isinstance(hashes, list) or not hashes:
        errors.append("missing_hashes")
        return
    for index, asset in enumerate(hashes):
        _validate_asset(errors, asset, f"hashes.{index}", None, require_role=False)


def _validate_signature(errors: list[str], signature: Any) -> None:
    if not isinstance(signature, Mapping):
        errors.append("missing_signature")
        return
    _require_equal(errors, signature, "algorithm", ASSET_BUNDLE_SIGNATURE_ALGORITHM)
    _require_equal(errors, signature, "scope", ASSET_BUNDLE_SIGNATURE_SCOPE)
    if not isinstance(signature.get("key_id"), str) or not signature.get("key_id"):
        errors.append("missing_signature_key_id")
    value = signature.get("value")
    if not isinstance(value, str) or not _HEX_SHA256_RE.fullmatch(value):
        errors.append("invalid_signature_value")


def _validate_asset(
    errors: list[str],
    asset: Any,
    prefix: str,
    expected_role: str | None,
    *,
    require_role: bool = True,
) -> None:
    if not isinstance(asset, Mapping):
        errors.append(f"missing_{prefix}")
        return
    if not isinstance(asset.get("path"), str) or not asset.get("path"):
        errors.append(f"missing_{prefix}.path")
    if require_role:
        if not isinstance(asset.get("role"), str) or not asset.get("role"):
            errors.append(f"missing_{prefix}.role")
        elif expected_role is not None and asset.get("role") != expected_role:
            errors.append(f"invalid_{prefix}.role")
        if not isinstance(asset.get("content_type"), str) or not asset.get("content_type"):
            errors.append(f"missing_{prefix}.content_type")
    if not isinstance(asset.get("size_bytes"), int) or asset.get("size_bytes") <= 0:
        errors.append(f"invalid_{prefix}.size_bytes")
    sha256 = asset.get("sha256")
    if not isinstance(sha256, str) or not _HEX_SHA256_RE.fullmatch(sha256):
        errors.append(f"invalid_{prefix}.sha256")


def _require_equal(errors: list[str], payload: Mapping[str, Any], field: str, expected: str) -> None:
    if payload.get(field) != expected:
        errors.append(f"invalid_{field}")


def _non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)
