from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Any, Mapping


MODEL_BUNDLE_SCHEMA_VERSION = "hexe-model-bundle-v1"
MODEL_BUNDLE_API_VERSION = "hexe-model-bundle-api-v1"
MODEL_BUNDLE_SIGNATURE_ALGORITHM = "hmac-sha256"
MODEL_BUNDLE_SECURITY_POLICY = "signed_manifest_sha256_required"
MODEL_BUNDLE_SIGNATURE_SCOPE = "canonical_manifest_without_signature"
DEFAULT_MODEL_BUNDLE_ID = "hexe-microwake-defaults"
DEFAULT_MODEL_BUNDLE_VERSION = "2026.08.30"
DEFAULT_MODEL_BUNDLE_KEY_ID = "hexevoice-model-dev-v1"

DEFAULT_COMPATIBLE_BOARD_PROFILES = [
    "esp_box_3",
    "ha_voice_pe",
    "waveshare_s3_touch_lcd_1_85c_box_v2",
]
DEFAULT_COMPATIBLE_PARTITION_SCHEMAS = [
    "s3-16m-recovery-v1",
    "s3-8m-recovery-v1",
    "p4-32m-v1",
]

_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class FirmwareBundleError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_model_bundle_signature_payload(manifest: Mapping[str, Any]) -> str:
    unsigned_manifest = dict(manifest)
    unsigned_manifest.pop("signature", None)
    return json.dumps(unsigned_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sign_model_bundle_manifest(
    manifest: Mapping[str, Any],
    *,
    signing_key: str,
    key_id: str = DEFAULT_MODEL_BUNDLE_KEY_ID,
) -> dict[str, Any]:
    if not signing_key:
        raise FirmwareBundleError("A model-bundle signing key is required.")
    signed_manifest = dict(manifest)
    payload = canonical_model_bundle_signature_payload(signed_manifest).encode("utf-8")
    signed_manifest["signature"] = {
        "algorithm": MODEL_BUNDLE_SIGNATURE_ALGORITHM,
        "key_id": key_id,
        "scope": MODEL_BUNDLE_SIGNATURE_SCOPE,
        "value": hmac.new(signing_key.encode("utf-8"), payload, hashlib.sha256).hexdigest(),
    }
    return signed_manifest


def verify_model_bundle_signature(manifest: Mapping[str, Any], *, signing_key: str) -> bool:
    signature = manifest.get("signature")
    if not isinstance(signature, Mapping):
        return False
    if signature.get("algorithm") != MODEL_BUNDLE_SIGNATURE_ALGORITHM:
        return False
    value = signature.get("value")
    if not isinstance(value, str) or not _HEX_SHA256_RE.fullmatch(value):
        return False
    expected = sign_model_bundle_manifest(manifest, signing_key=signing_key, key_id=str(signature.get("key_id", "")))
    expected_signature = expected["signature"]["value"]
    return hmac.compare_digest(value, expected_signature)


def build_default_model_bundle_manifest(
    *,
    model_dir: Path,
    bundle_id: str = DEFAULT_MODEL_BUNDLE_ID,
    version: str = DEFAULT_MODEL_BUNDLE_VERSION,
    release_channel: str = "dev",
    board_profiles: list[str] | None = None,
    partition_schemas: list[str] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    model_dir = model_dir.resolve()
    if not model_dir.is_dir():
        raise FirmwareBundleError(f"Model directory does not exist: {model_dir}")

    created_at_utc = created_at_utc or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    preprocessor_path = model_dir / "audio_preprocessor_int8.tflite"
    wake_entry = _model_entry(
        model_dir=model_dir,
        metadata_filename="alexa.json",
        role="wake",
        model_id="alexa",
        alias="Hexe",
        source="esphome_micro_wake_word_models_v2",
    )
    stop_entry = _model_entry(
        model_dir=model_dir,
        metadata_filename="stop.json",
        role="playback_stop",
        model_id="stop",
        alias=None,
        source="kahrendt_microWakeWord_stop_beta_20241017_5",
    )

    assets = [
        _asset_digest(preprocessor_path, "audio_preprocessor_int8.tflite"),
        wake_entry["metadata"],
        wake_entry["model"],
        stop_entry["metadata"],
        stop_entry["model"],
    ]

    return {
        "schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
        "model_api_version": MODEL_BUNDLE_API_VERSION,
        "bundle_id": bundle_id,
        "version": version,
        "created_at_utc": created_at_utc,
        "release_channel": release_channel,
        "security_policy": MODEL_BUNDLE_SECURITY_POLICY,
        "compatibility": {
            "firmware_api_version": {
                "min": "hexe-firmware-main-api-v1",
                "max": "hexe-firmware-main-api-v1",
            },
            "board_profiles": board_profiles or DEFAULT_COMPATIBLE_BOARD_PROFILES,
            "partition_schemas": partition_schemas or DEFAULT_COMPATIBLE_PARTITION_SCHEMAS,
            "requires_partitions": ["model_a", "model_b"],
            "minimum_model_bank_bytes": 256 * 1024,
        },
        "preprocessing": {
            "id": "micro_wake_word_audio_preprocessor",
            "sample_rate_hz": 16000,
            "feature_step_size_ms": 10,
            "asset": assets[0],
        },
        "models": [wake_entry, stop_entry],
        "hashes": assets,
    }


def validate_model_bundle_manifest(manifest: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _require_equal(errors, manifest, "schema_version", MODEL_BUNDLE_SCHEMA_VERSION)
    _require_equal(errors, manifest, "model_api_version", MODEL_BUNDLE_API_VERSION)
    _require_equal(errors, manifest, "security_policy", MODEL_BUNDLE_SECURITY_POLICY)
    for field in ["bundle_id", "version", "created_at_utc", "release_channel"]:
        if not isinstance(manifest.get(field), str) or not str(manifest.get(field)).strip():
            errors.append(f"missing_{field}")

    compatibility = manifest.get("compatibility")
    if not isinstance(compatibility, Mapping):
        errors.append("missing_compatibility")
    else:
        if not _non_empty_string_list(compatibility.get("board_profiles")):
            errors.append("missing_compatible_board_profiles")
        if not _non_empty_string_list(compatibility.get("partition_schemas")):
            errors.append("missing_compatible_partition_schemas")
        if "model_a" not in compatibility.get("requires_partitions", []) or "model_b" not in compatibility.get(
            "requires_partitions", []
        ):
            errors.append("missing_model_ab_partition_requirement")

    preprocessing = manifest.get("preprocessing")
    if not isinstance(preprocessing, Mapping):
        errors.append("missing_preprocessing")
    else:
        _validate_asset(errors, preprocessing.get("asset"), "preprocessing.asset")
        if preprocessing.get("sample_rate_hz") != 16000:
            errors.append("unsupported_preprocessing_sample_rate")
        if preprocessing.get("feature_step_size_ms") != 10:
            errors.append("unsupported_preprocessing_feature_step")

    models = manifest.get("models")
    if not isinstance(models, list) or not models:
        errors.append("missing_models")
        models = []
    roles = {model.get("role") for model in models if isinstance(model, Mapping)}
    if "wake" not in roles:
        errors.append("missing_wake_model")
    if "playback_stop" not in roles:
        errors.append("missing_playback_stop_model")
    for index, model in enumerate(models):
        if not isinstance(model, Mapping):
            errors.append(f"invalid_model_{index}")
            continue
        prefix = f"models.{index}"
        for field in ["id", "role", "wake_word", "source"]:
            if not isinstance(model.get(field), str) or not str(model.get(field)).strip():
                errors.append(f"missing_{prefix}.{field}")
        if model.get("role") == "wake" and model.get("alias") != "Hexe":
            errors.append("wake_model_alias_must_be_hexe")
        _validate_asset(errors, model.get("metadata"), f"{prefix}.metadata")
        _validate_asset(errors, model.get("model"), f"{prefix}.model")
        thresholds = model.get("thresholds")
        if not isinstance(thresholds, Mapping):
            errors.append(f"missing_{prefix}.thresholds")
        else:
            for field in ["probability_cutoff", "sliding_window_size", "feature_step_size_ms", "tensor_arena_size"]:
                if field not in thresholds:
                    errors.append(f"missing_{prefix}.thresholds.{field}")

    hashes = manifest.get("hashes")
    if not isinstance(hashes, list) or not hashes:
        errors.append("missing_hashes")
    else:
        for index, asset in enumerate(hashes):
            _validate_asset(errors, asset, f"hashes.{index}")

    signature = manifest.get("signature")
    if not isinstance(signature, Mapping):
        errors.append("missing_signature")
    else:
        _require_equal(errors, signature, "algorithm", MODEL_BUNDLE_SIGNATURE_ALGORITHM)
        _require_equal(errors, signature, "scope", MODEL_BUNDLE_SIGNATURE_SCOPE)
        if not isinstance(signature.get("key_id"), str) or not signature.get("key_id"):
            errors.append("missing_signature_key_id")
        value = signature.get("value")
        if not isinstance(value, str) or not _HEX_SHA256_RE.fullmatch(value):
            errors.append("invalid_signature_value")

    return errors


def _model_entry(
    *,
    model_dir: Path,
    metadata_filename: str,
    role: str,
    model_id: str,
    alias: str | None,
    source: str,
) -> dict[str, Any]:
    metadata_path = model_dir / metadata_filename
    metadata = json.loads(metadata_path.read_text())
    model_filename = str(metadata["model"])
    model_path = model_dir / model_filename
    micro = metadata["micro"]
    entry: dict[str, Any] = {
        "id": model_id,
        "role": role,
        "wake_word": metadata["wake_word"],
        "source": source,
        "author": metadata.get("author", ""),
        "trained_languages": metadata.get("trained_languages", []),
        "upstream_version": metadata.get("version"),
        "minimum_esphome_version": micro.get("minimum_esphome_version", ""),
        "metadata": _asset_digest(metadata_path, metadata_filename),
        "model": _asset_digest(model_path, model_filename),
        "thresholds": {
            "probability_cutoff": micro["probability_cutoff"],
            "sliding_window_size": micro["sliding_window_size"],
            "feature_step_size_ms": micro["feature_step_size"],
            "tensor_arena_size": micro["tensor_arena_size"],
        },
    }
    if alias is not None:
        entry["alias"] = alias
    return entry


def _asset_digest(path: Path, relative_path: str) -> dict[str, Any]:
    if not path.is_file():
        raise FirmwareBundleError(f"Model bundle asset does not exist: {path}")
    return {
        "path": relative_path,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _require_equal(errors: list[str], payload: Mapping[str, Any], field: str, expected: str) -> None:
    if payload.get(field) != expected:
        errors.append(f"invalid_{field}")


def _non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def _validate_asset(errors: list[str], asset: Any, prefix: str) -> None:
    if not isinstance(asset, Mapping):
        errors.append(f"missing_{prefix}")
        return
    if not isinstance(asset.get("path"), str) or not asset.get("path"):
        errors.append(f"missing_{prefix}.path")
    if not isinstance(asset.get("size_bytes"), int) or asset.get("size_bytes") <= 0:
        errors.append(f"invalid_{prefix}.size_bytes")
    sha256 = asset.get("sha256")
    if not isinstance(sha256, str) or not _HEX_SHA256_RE.fullmatch(sha256):
        errors.append(f"invalid_{prefix}.sha256")
