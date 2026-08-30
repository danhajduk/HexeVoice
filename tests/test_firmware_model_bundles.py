import hashlib
import hmac
import json
import subprocess
import sys
from pathlib import Path

from hexevoice.firmware_bundles import (
    MODEL_BUNDLE_API_VERSION,
    MODEL_BUNDLE_SCHEMA_VERSION,
    canonical_model_bundle_signature_payload,
    build_default_model_bundle_manifest,
    sign_model_bundle_manifest,
    validate_model_bundle_manifest,
    verify_model_bundle_signature,
)


MODEL_DIR = Path("firmware/components/endpoint_runtime/voice/models")


def test_default_model_bundle_manifest_is_derived_from_embedded_wake_assets():
    manifest = sign_model_bundle_manifest(
        build_default_model_bundle_manifest(model_dir=MODEL_DIR, created_at_utc="2026-08-30T00:00:00Z"),
        signing_key="test-model-key",
        key_id="test-key",
    )

    assert validate_model_bundle_manifest(manifest) == []
    assert verify_model_bundle_signature(manifest, signing_key="test-model-key")
    assert manifest["schema_version"] == MODEL_BUNDLE_SCHEMA_VERSION
    assert manifest["model_api_version"] == MODEL_BUNDLE_API_VERSION
    assert manifest["security_policy"] == "signed_manifest_sha256_required"
    assert manifest["compatibility"]["requires_partitions"] == ["model_a", "model_b"]
    assert manifest["preprocessing"]["sample_rate_hz"] == 16000
    assert manifest["preprocessing"]["asset"]["sha256"] == sha256(MODEL_DIR / "audio_preprocessor_int8.tflite")

    models = {model["id"]: model for model in manifest["models"]}
    assert models["alexa"]["role"] == "wake"
    assert models["alexa"]["wake_word"] == "Alexa"
    assert models["alexa"]["alias"] == "Hexe"
    assert models["alexa"]["model"]["sha256"] == sha256(MODEL_DIR / "alexa.tflite")
    assert models["alexa"]["metadata"]["sha256"] == sha256(MODEL_DIR / "alexa.json")
    assert models["alexa"]["thresholds"] == {
        "probability_cutoff": 0.9,
        "sliding_window_size": 5,
        "feature_step_size_ms": 10,
        "tensor_arena_size": 22348,
    }

    assert models["stop"]["role"] == "playback_stop"
    assert models["stop"]["wake_word"] == "Stop"
    assert "alias" not in models["stop"]
    assert models["stop"]["model"]["sha256"] == sha256(MODEL_DIR / "stop.tflite")
    assert models["stop"]["metadata"]["sha256"] == sha256(MODEL_DIR / "stop.json")
    assert models["stop"]["thresholds"] == {
        "probability_cutoff": 0.5,
        "sliding_window_size": 5,
        "feature_step_size_ms": 10,
        "tensor_arena_size": 21000,
    }

    hash_paths = {asset["path"] for asset in manifest["hashes"]}
    assert hash_paths == {
        "audio_preprocessor_int8.tflite",
        "alexa.json",
        "alexa.tflite",
        "stop.json",
        "stop.tflite",
    }


def test_model_bundle_signature_uses_canonical_manifest_without_signature():
    unsigned = build_default_model_bundle_manifest(model_dir=MODEL_DIR, created_at_utc="2026-08-30T00:00:00Z")
    signed = sign_model_bundle_manifest(unsigned, signing_key="test-model-key", key_id="test-key")
    payload = canonical_model_bundle_signature_payload(signed).encode("utf-8")
    expected = hmac.new(b"test-model-key", payload, hashlib.sha256).hexdigest()

    assert signed["signature"] == {
        "algorithm": "hmac-sha256",
        "key_id": "test-key",
        "scope": "canonical_manifest_without_signature",
        "value": expected,
    }
    assert "signature" not in json.loads(payload)


def test_create_model_bundle_manifest_cli_writes_signed_manifest(tmp_path):
    output = tmp_path / "manifest.json"
    subprocess.run(
        [
            sys.executable,
            "firmware/tools/create-model-bundle-manifest.py",
            "--model-dir",
            str(MODEL_DIR),
            "--created-at-utc",
            "2026-08-30T00:00:00Z",
            "--board-profile",
            "esp_box_3",
            "--partition-schema",
            "s3-16m-recovery-v1",
            "--signing-key",
            "test-model-key",
            "--key-id",
            "test-key",
            "--output",
            str(output),
        ],
        check=True,
    )

    manifest = json.loads(output.read_text())
    assert validate_model_bundle_manifest(manifest) == []
    assert verify_model_bundle_signature(manifest, signing_key="test-model-key")
    assert manifest["compatibility"]["board_profiles"] == ["esp_box_3"]
    assert manifest["compatibility"]["partition_schemas"] == ["s3-16m-recovery-v1"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
