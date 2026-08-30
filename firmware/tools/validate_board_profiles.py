#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


ALLOWED_IDF_TARGETS = {"esp32s3", "esp32p4"}
ALLOWED_PARTITION_SCHEMAS = {
    "s3-8m-v1",
    "s3-8m-recovery-v1",
    "s3-16m-v1",
    "s3-16m-recovery-v1",
    "p4-32m-v1",
}
RECOVERY_PARTITION_SCHEMAS = {"s3-8m-recovery-v1", "s3-16m-recovery-v1", "p4-32m-v1"}
ALLOWED_SUPPORT_STATUS = {"active", "planned", "experimental", "unsupported"}
SECRET_KEY_PATTERNS = (
    "password",
    "secret",
    "token",
    "private_key",
    "signing_key",
    "ssid",
)


class ValidationError(ValueError):
    pass


def parse_scalar(value: str) -> Any:
    text = value.strip()
    if text == "":
        return ""
    if text in {"null", "Null", "NULL", "~"}:
        return None
    if text == "[]":
        return []
    if text == "{}":
        return {}
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    return text


def strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def yaml_lines(path: Path) -> list[tuple[int, str]]:
    parsed: list[tuple[int, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = strip_comment(raw_line).rstrip()
        if not line.strip():
            continue
        parsed.append((len(line) - len(line.lstrip(" ")), line.strip()))
    return parsed


def parse_simple_yaml(path: Path) -> Any:
    lines = yaml_lines(path)
    if not lines:
        return {}
    value, next_index = parse_yaml_block(lines, 0, lines[0][0])
    if next_index != len(lines):
        raise ValidationError(f"{path}: could not parse YAML near line {next_index + 1}")
    return value


def parse_yaml_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    current_indent, text = lines[index]
    if current_indent < indent:
        return {}, index
    if text.startswith("- "):
        return parse_yaml_list(lines, index, current_indent)
    return parse_yaml_dict(lines, index, current_indent)


def parse_yaml_dict(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        current_indent, text = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValidationError(f"unexpected indentation before {text!r}")
        if text.startswith("- "):
            break
        if ":" not in text:
            raise ValidationError(f"expected key/value entry, got {text!r}")
        key, raw_value = text.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            result[key] = parse_scalar(raw_value)
            continue
        if index >= len(lines) or lines[index][0] <= current_indent:
            result[key] = {}
            continue
        result[key], index = parse_yaml_block(lines, index, lines[index][0])
    return result, index


def parse_yaml_list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        current_indent, text = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or not text.startswith("- "):
            break
        item_text = text[2:].strip()
        index += 1
        if not item_text:
            if index >= len(lines) or lines[index][0] <= current_indent:
                result.append(None)
            else:
                item, index = parse_yaml_block(lines, index, lines[index][0])
                result.append(item)
            continue
        if ":" in item_text and not item_text.startswith(("http://", "https://")):
            key, raw_value = item_text.split(":", 1)
            item_dict: dict[str, Any] = {key.strip(): parse_scalar(raw_value.strip()) if raw_value.strip() else {}}
            while index < len(lines) and lines[index][0] > current_indent:
                child_indent, child_text = lines[index]
                if child_text.startswith("- "):
                    break
                if ":" not in child_text:
                    raise ValidationError(f"expected list item key/value entry, got {child_text!r}")
                child_key, child_raw_value = child_text.split(":", 1)
                child_key = child_key.strip()
                child_raw_value = child_raw_value.strip()
                index += 1
                if child_raw_value:
                    item_dict[child_key] = parse_scalar(child_raw_value)
                elif index < len(lines) and lines[index][0] > child_indent:
                    item_dict[child_key], index = parse_yaml_block(lines, index, lines[index][0])
                else:
                    item_dict[child_key] = {}
            result.append(item_dict)
        else:
            result.append(parse_scalar(item_text))
    return result, index


def load_profile(path: Path) -> dict[str, Any]:
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
    elif path.suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore

            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except ModuleNotFoundError:
            payload = parse_simple_yaml(path)
    else:
        raise ValidationError(f"{path}: unsupported profile extension")
    if not isinstance(payload, dict):
        raise ValidationError(f"{path}: profile root must be an object")
    return payload


def require_mapping(profile: dict[str, Any], key: str) -> dict[str, Any]:
    value = profile.get(key)
    if not isinstance(value, dict):
        raise ValidationError(f"{profile.get('board_profile', '<unknown>')}: {key} must be an object")
    return value


def require_list(profile: dict[str, Any], key: str) -> list[Any]:
    value = profile.get(key)
    if not isinstance(value, list):
        raise ValidationError(f"{profile.get('board_profile', '<unknown>')}: {key} must be a list")
    return value


def require_string(profile: dict[str, Any], key: str) -> str:
    value = profile.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{profile.get('board_profile', '<unknown>')}: {key} must be a non-empty string")
    return value


def walk_keys(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        keys: list[str] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            keys.append(child_prefix)
            keys.extend(walk_keys(child, child_prefix))
        return keys
    if isinstance(value, list):
        keys = []
        for index, child in enumerate(value):
            keys.extend(walk_keys(child, f"{prefix}[{index}]"))
        return keys
    return []


def validate_profile(profile: dict[str, Any], path: Path) -> None:
    required = {
        "schema_version",
        "board_profile",
        "display_name",
        "vendor",
        "model",
        "hardware_revision",
        "support_status",
        "sources",
        "build",
        "hardware",
        "features",
        "display",
        "audio",
        "vad",
        "wiring",
        "adapters",
        "wake",
        "storage",
        "controls",
        "indicators",
        "capability_overrides",
    }
    missing = sorted(required - set(profile))
    if missing:
        raise ValidationError(f"{path}: missing required keys: {', '.join(missing)}")

    if profile["schema_version"] != 1:
        raise ValidationError(f"{path}: schema_version must be 1")

    board_profile = require_string(profile, "board_profile")
    if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", board_profile):
        raise ValidationError(f"{path}: board_profile must be lower_snake_case")
    if path.parent.name != "schema" and path.parent.name != board_profile:
        raise ValidationError(f"{path}: parent directory must match board_profile {board_profile!r}")

    for key in walk_keys(profile):
        lowered = key.lower()
        if any(pattern in lowered for pattern in SECRET_KEY_PATTERNS):
            raise ValidationError(f"{board_profile}: board profile must not contain secret-like key {key!r}")

    support_status = require_string(profile, "support_status")
    if support_status not in ALLOWED_SUPPORT_STATUS:
        raise ValidationError(f"{board_profile}: invalid support_status {support_status!r}")

    sources = require_list(profile, "sources")
    if not sources:
        raise ValidationError(f"{board_profile}: sources must not be empty")
    for source in sources:
        if not isinstance(source, dict) or not source.get("label") or not str(source.get("url", "")).startswith("https://"):
            raise ValidationError(f"{board_profile}: every source needs a label and https:// URL")

    build = require_mapping(profile, "build")
    idf_target = build.get("idf_target")
    partition_schema = build.get("partition_schema")
    if idf_target not in ALLOWED_IDF_TARGETS:
        raise ValidationError(f"{board_profile}: invalid build.idf_target {idf_target!r}")
    if partition_schema not in ALLOWED_PARTITION_SCHEMAS:
        raise ValidationError(f"{board_profile}: invalid build.partition_schema {partition_schema!r}")
    if idf_target == "esp32s3" and not str(partition_schema).startswith("s3-"):
        raise ValidationError(f"{board_profile}: ESP32-S3 profiles must use an s3 partition schema")
    if idf_target == "esp32p4" and not str(partition_schema).startswith("p4-"):
        raise ValidationError(f"{board_profile}: ESP32-P4 profiles must use a p4 partition schema")
    if build.get("recovery_app") is True and partition_schema not in RECOVERY_PARTITION_SCHEMAS:
        raise ValidationError(f"{board_profile}: recovery_app profiles must use a recovery partition schema")
    definitions = build.get("compile_definitions")
    if not isinstance(definitions, list) or not definitions:
        raise ValidationError(f"{board_profile}: build.compile_definitions must not be empty")

    hardware = require_mapping(profile, "hardware")
    if hardware.get("soc") != idf_target:
        raise ValidationError(f"{board_profile}: hardware.soc must match build.idf_target")
    for key in ("flash_size", "psram_size"):
        if not re.fullmatch(r"[0-9]+(K|M)i?B", str(hardware.get(key, ""))):
            raise ValidationError(f"{board_profile}: hardware.{key} must be a size such as 16MiB")
    wireless = hardware.get("wireless")
    if not isinstance(wireless, dict) or not wireless.get("wifi") or not wireless.get("bluetooth"):
        raise ValidationError(f"{board_profile}: hardware.wireless requires wifi and bluetooth")

    features = require_mapping(profile, "features")
    for key in (
        "display",
        "touch",
        "sd_card",
        "speaker",
        "microphone",
        "led_ring",
        "status_led",
        "rotary_encoder",
        "buttons",
        "hardware_mute",
        "battery",
        "camera",
        "usb_otg",
    ):
        if not isinstance(features.get(key), bool):
            raise ValidationError(f"{board_profile}: features.{key} must be true or false")

    display = require_mapping(profile, "display")
    if display.get("available") != features["display"]:
        raise ValidationError(f"{board_profile}: display.available must match features.display")
    if features["display"]:
        if not isinstance(display.get("size_inches"), (int, float)):
            raise ValidationError(f"{board_profile}: display.size_inches is required when display is enabled")
        if not isinstance(display.get("width_px"), int) or not isinstance(display.get("height_px"), int):
            raise ValidationError(f"{board_profile}: display width_px and height_px are required")
        if display.get("touch") != features["touch"]:
            raise ValidationError(f"{board_profile}: display.touch must match features.touch")

    audio = require_mapping(profile, "audio")
    audio_input = audio.get("input")
    audio_output = audio.get("output")
    if not isinstance(audio_input, dict) or audio_input.get("available") != features["microphone"]:
        raise ValidationError(f"{board_profile}: audio.input.available must match features.microphone")
    if not isinstance(audio_output, dict) or audio_output.get("available") != features["speaker"]:
        raise ValidationError(f"{board_profile}: audio.output.available must match features.speaker")
    dsp = audio_input.get("dsp")
    if not isinstance(dsp, dict):
        raise ValidationError(f"{board_profile}: audio.input.dsp must be an object")
    for key in ("aec", "noise_suppression", "agc", "vad"):
        if not isinstance(dsp.get(key), bool):
            raise ValidationError(f"{board_profile}: audio.input.dsp.{key} must be true or false")

    vad = require_mapping(profile, "vad")
    firmware_vad = vad.get("firmware")
    if not isinstance(firmware_vad, dict):
        raise ValidationError(f"{board_profile}: vad.firmware must be an object")
    if firmware_vad.get("available") != features["microphone"]:
        raise ValidationError(f"{board_profile}: vad.firmware.available must match features.microphone")
    if firmware_vad.get("status") not in {"active", "planned", "unsupported"}:
        raise ValidationError(f"{board_profile}: vad.firmware.status must be active, planned, or unsupported")
    if firmware_vad.get("algorithm") != "energy_threshold":
        raise ValidationError(f"{board_profile}: vad.firmware.algorithm must be energy_threshold")
    if firmware_vad.get("input_source") != "pcm_audio_frames":
        raise ValidationError(f"{board_profile}: vad.firmware.input_source must be pcm_audio_frames")
    for key in ("configurable", "adaptive_noise_floor"):
        if not isinstance(firmware_vad.get(key), bool):
            raise ValidationError(f"{board_profile}: vad.firmware.{key} must be true or false")
    for key in ("frame_ms", "default_energy_threshold", "default_pause_ms", "silence_hold_ms"):
        if not isinstance(firmware_vad.get(key), int) or firmware_vad[key] <= 0:
            raise ValidationError(f"{board_profile}: vad.firmware.{key} must be a positive integer")
    expected_vad_status = "active" if support_status == "active" else "planned"
    if features["microphone"] and firmware_vad.get("status") != expected_vad_status:
        raise ValidationError(f"{board_profile}: vad.firmware.status must be {expected_vad_status}")

    wiring = require_mapping(profile, "wiring")
    if wiring.get("status") not in {"complete", "partial", "unknown"}:
        raise ValidationError(f"{board_profile}: wiring.status must be complete, partial, or unknown")
    for key in ("gpios", "i2c_buses", "i2s_buses", "spi_buses", "led_strips"):
        if not isinstance(wiring.get(key), list):
            raise ValidationError(f"{board_profile}: wiring.{key} must be a list")

    def validate_named_entries(entries: list[Any], key: str) -> None:
        names: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValidationError(f"{board_profile}: wiring.{key} entries must be objects")
            name = entry.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", name):
                raise ValidationError(f"{board_profile}: wiring.{key} entries need lower_snake_case names")
            if name in names:
                raise ValidationError(f"{board_profile}: duplicate wiring.{key} name {name!r}")
            names.add(name)

    def validate_pin(value: Any, key: str, required_for_build: bool) -> None:
        if value is None:
            if required_for_build:
                raise ValidationError(f"{board_profile}: buildable profile has incomplete pin mapping for {key}")
            return
        if not isinstance(value, int) or value < -1 or value > 99:
            raise ValidationError(f"{board_profile}: {key} must be an ESP GPIO number, -1, or null")

    buildable_wiring = profile.get("support_status") == "active" and wiring["status"] == "complete"
    validate_named_entries(wiring["gpios"], "gpios")
    validate_named_entries(wiring["i2c_buses"], "i2c_buses")
    validate_named_entries(wiring["i2s_buses"], "i2s_buses")
    validate_named_entries(wiring["spi_buses"], "spi_buses")
    validate_named_entries(wiring["led_strips"], "led_strips")
    for entry in wiring["gpios"]:
        validate_pin(entry.get("gpio"), f"wiring.gpios.{entry['name']}.gpio", buildable_wiring)
        if not isinstance(entry.get("active_low"), bool):
            raise ValidationError(f"{board_profile}: wiring.gpios.{entry['name']}.active_low must be true or false")
    for entry in wiring["i2c_buses"]:
        if entry.get("port") is not None and (not isinstance(entry.get("port"), int) or entry["port"] < 0):
            raise ValidationError(f"{board_profile}: wiring.i2c_buses.{entry['name']}.port must be a non-negative integer or null")
        validate_pin(entry.get("sda"), f"wiring.i2c_buses.{entry['name']}.sda", buildable_wiring)
        validate_pin(entry.get("scl"), f"wiring.i2c_buses.{entry['name']}.scl", buildable_wiring)
        if entry.get("clock_hz") is not None and (not isinstance(entry.get("clock_hz"), int) or entry["clock_hz"] <= 0):
            raise ValidationError(f"{board_profile}: wiring.i2c_buses.{entry['name']}.clock_hz must be positive or null")
        devices = entry.get("devices")
        if not isinstance(devices, list):
            raise ValidationError(f"{board_profile}: wiring.i2c_buses.{entry['name']}.devices must be a list")
        validate_named_entries(devices, f"i2c_buses.{entry['name']}.devices")
        for device in devices:
            address = device.get("address")
            if address is not None and (not isinstance(address, int) or address < 0 or address > 127):
                raise ValidationError(f"{board_profile}: wiring.i2c_buses.{entry['name']}.{device['name']}.address must be 0-127 or null")
            if buildable_wiring and address is None:
                raise ValidationError(f"{board_profile}: buildable profile has incomplete I2C address for {device['name']}")
    for entry in wiring["i2s_buses"]:
        if entry.get("port") is not None and (not isinstance(entry.get("port"), int) or entry["port"] < 0):
            raise ValidationError(f"{board_profile}: wiring.i2s_buses.{entry['name']}.port must be a non-negative integer or null")
        for pin_key in ("mclk", "bclk", "lrclk", "din", "dout"):
            validate_pin(entry.get(pin_key), f"wiring.i2s_buses.{entry['name']}.{pin_key}", False)
        if buildable_wiring:
            for pin_key in ("bclk", "lrclk"):
                validate_pin(entry.get(pin_key), f"wiring.i2s_buses.{entry['name']}.{pin_key}", True)
            if entry.get("din") is None and entry.get("dout") is None:
                raise ValidationError(f"{board_profile}: buildable I2S bus {entry['name']} needs din or dout")
    for entry in wiring["spi_buses"]:
        for pin_key in ("clk", "mosi", "miso", "cs", "dc", "reset", "backlight"):
            validate_pin(entry.get(pin_key), f"wiring.spi_buses.{entry['name']}.{pin_key}", buildable_wiring and pin_key != "miso")
    for entry in wiring["led_strips"]:
        validate_pin(entry.get("data"), f"wiring.led_strips.{entry['name']}.data", buildable_wiring)
        validate_pin(entry.get("power"), f"wiring.led_strips.{entry['name']}.power", False)
        if entry.get("pixel_count") is not None and (not isinstance(entry.get("pixel_count"), int) or entry["pixel_count"] <= 0):
            raise ValidationError(f"{board_profile}: wiring.led_strips.{entry['name']}.pixel_count must be positive or null")

    adapters = require_mapping(profile, "adapters")
    if not isinstance(adapters.get("buildable"), bool):
        raise ValidationError(f"{board_profile}: adapters.buildable must be true or false")
    if adapters["buildable"] and wiring["status"] != "complete":
        raise ValidationError(f"{board_profile}: buildable profiles must have complete wiring")
    source_files = adapters.get("source_files")
    if not isinstance(source_files, list):
        raise ValidationError(f"{board_profile}: adapters.source_files must be a list")
    if adapters["buildable"] and not source_files:
        raise ValidationError(f"{board_profile}: buildable profiles must define adapter source files")
    source_base = None
    if path.parent.parent.name == "boards":
        source_base = path.parent.parent.parent / "components" / "endpoint_runtime"
    for source in source_files:
        if not isinstance(source, str) or not re.fullmatch(r"(board|voice|system|ui)/[A-Za-z0-9_./-]+\.cpp", source):
            raise ValidationError(f"{board_profile}: invalid adapter source file {source!r}")
        source_path = source_base / source if source_base is not None else None
        if adapters["buildable"] and source_path is not None and not source_path.exists():
            raise ValidationError(f"{board_profile}: adapter source file does not exist: {source}")

    wake = require_mapping(profile, "wake")
    if wake.get("backend_fallback") is not True:
        raise ValidationError(f"{board_profile}: wake.backend_fallback must remain true")
    if wake.get("primary_model") != "alexa" or wake.get("alias") != "Hexe" or wake.get("stop_model") != "stop":
        raise ValidationError(f"{board_profile}: wake must declare alexa/Hexe and stop models")

    storage = require_mapping(profile, "storage")
    sd_card = storage.get("sd_card")
    if not isinstance(sd_card, dict) or sd_card.get("available") != features["sd_card"]:
        raise ValidationError(f"{board_profile}: storage.sd_card.available must match features.sd_card")

    overrides = require_mapping(profile, "capability_overrides")
    expected_overrides = {
        "display": features["display"],
        "touchscreen": features["touch"],
        "storage": features["sd_card"],
        "audio_input": features["microphone"],
        "audio_output": features["speaker"],
    }
    for key, expected in expected_overrides.items():
        if overrides.get(key) != expected:
            raise ValidationError(f"{board_profile}: capability_overrides.{key} must be {expected}")

    revisions = require_mapping(profile, "hardware_revision")
    supported = revisions.get("supported")
    unsupported = revisions.get("unsupported")
    if not isinstance(revisions.get("required"), bool):
        raise ValidationError(f"{board_profile}: hardware_revision.required must be true or false")
    if not isinstance(supported, list) or not supported:
        raise ValidationError(f"{board_profile}: hardware_revision.supported must not be empty")
    if not isinstance(unsupported, list):
        raise ValidationError(f"{board_profile}: hardware_revision.unsupported must be a list")
    if board_profile == "waveshare_s3_touch_lcd_1_85c_box_v2":
        supported_normalized = {str(item).lower() for item in supported}
        unsupported_normalized = {str(item).lower() for item in unsupported}
        if revisions.get("required") is not True or "v2" not in supported_normalized or "v1" not in unsupported_normalized:
            raise ValidationError(f"{board_profile}: profile must require V2 and reject V1")


def discover_profiles(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.glob("*/board.*")
        if path.suffix in {".yaml", ".yml", ".json"} and path.parent.name != "schema"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Hexe firmware board profile files.")
    parser.add_argument("paths", nargs="*", type=Path, help="Board profile files or directories to validate.")
    parser.add_argument("--root", type=Path, default=Path("firmware/boards"), help="Profile root used when no paths are given.")
    args = parser.parse_args()

    candidates: list[Path] = []
    if args.paths:
        for path in args.paths:
            if path.is_dir():
                candidates.extend(discover_profiles(path))
            else:
                candidates.append(path)
    else:
        candidates = discover_profiles(args.root)

    if not candidates:
        print("No board profiles found.", file=sys.stderr)
        return 1

    failures: list[str] = []
    for path in candidates:
        try:
            validate_profile(load_profile(path), path)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            failures.append(f"{path}: {exc}")

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    print(f"Validated {len(candidates)} board profile(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
