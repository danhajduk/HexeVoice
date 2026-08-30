#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

from validate_board_profiles import ValidationError, validate_profile


def yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:+ -]+", text) and text not in {"true", "false", "null"}:
        return text
    return json.dumps(text)


def render_yaml(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            if isinstance(child, list) and not child:
                lines.append(f"{prefix}{key}: []")
                continue
            if isinstance(child, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(render_yaml(child, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {yaml_scalar(child)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}[]"]
        lines = []
        for item in value:
            if isinstance(item, dict):
                item_lines = render_yaml(item, indent + 2)
                first = item_lines[0].lstrip()
                lines.append(f"{prefix}- {first}")
                lines.extend(item_lines[1:])
            elif isinstance(item, list):
                lines.append(f"{prefix}-")
                lines.extend(render_yaml(item, indent + 2))
            else:
                lines.append(f"{prefix}- {yaml_scalar(item)}")
        return lines
    return [f"{prefix}{yaml_scalar(value)}"]


def upper_definition(profile: str) -> str:
    return f"HEXE_BOARD_PROFILE_{profile.upper()}=1"


def partition_schema_for_soc(soc: str) -> str:
    if soc == "esp32p4":
        return "p4-32m-v1"
    return "s3-16m-v1"


def app_slot_size_for_soc(soc: str) -> str:
    if soc == "esp32p4":
        return "8MiB"
    return "4MiB"


def cpu_for_soc(soc: str) -> str:
    if soc == "esp32p4":
        return "dual-core HP RISC-V up to 360MHz plus LP RISC-V up to 40MHz"
    return "dual-core Xtensa LX7 up to 240MHz"


def flash_size_for_soc(soc: str) -> str:
    if soc == "esp32p4":
        return "32MiB"
    return "16MiB"


def psram_size_for_soc(soc: str) -> str:
    if soc == "esp32p4":
        return "32MiB"
    return "8MiB"


def wireless_defaults(soc: str, coprocessor: str | None) -> dict[str, object]:
    if soc == "esp32p4":
        return {
            "wifi": "Wi-Fi through coprocessor" if coprocessor else "external Wi-Fi required",
            "bluetooth": "Bluetooth LE through coprocessor" if coprocessor else "external Bluetooth required",
            "coprocessor": coprocessor,
            "transport": "sdio" if coprocessor else None,
        }
    return {
        "wifi": "2.4GHz 802.11 b/g/n",
        "bluetooth": "Bluetooth 5 LE",
        "coprocessor": coprocessor,
        "transport": "native" if coprocessor is None else "external",
    }


def display_section(args: argparse.Namespace) -> dict[str, object]:
    available = bool(args.with_display)
    return {
        "available": available,
        "kind": args.display_kind if available else None,
        "driver": args.display_driver if available else None,
        "interface": args.display_interface if available else None,
        "size_inches": args.display_size_inches if available else None,
        "shape": args.display_shape if available else None,
        "width_px": args.display_width_px if available else None,
        "height_px": args.display_height_px if available else None,
        "color_depth": args.display_color_depth if available else None,
        "touch": bool(args.with_touch),
    }


def controls(args: argparse.Namespace) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    if args.with_buttons:
        entries.append({"name": "boot", "kind": "button", "function": "download_mode", "gpio": None})
        entries.append({"name": "reset", "kind": "button", "function": "reset", "gpio": None})
    if args.with_touch:
        entries.append({"name": "touchscreen", "kind": "touch", "function": "local_ui_controls", "gpio": None})
    if args.with_rotary_encoder:
        entries.append({"name": "rotary_encoder", "kind": "rotary_encoder", "function": "volume_control", "gpio": None})
    if args.with_hardware_mute:
        entries.append({"name": "hardware_mute", "kind": "switch", "function": "physical_microphone_power_cut", "gpio": None})
    return entries


def indicators(args: argparse.Namespace) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    if args.with_display:
        entries.append({"name": "display", "kind": "display", "driver": args.display_driver, "gpio": None})
    if args.with_status_led:
        entries.append({"name": "status_led", "kind": "status_led", "driver": "board_led", "gpio": None})
    if args.with_led_ring:
        entries.append({
            "name": "led_ring",
            "kind": "led_ring",
            "driver": "ws2812_rmt",
            "gpio": None,
            "pixel_count": None,
            "color_order": None,
        })
    return entries


def build_profile(args: argparse.Namespace) -> dict[str, object]:
    microphone = bool(args.with_microphone)
    speaker = bool(args.with_speaker)
    support_status = "planned"
    firmware_vad_status = "planned" if microphone else "unsupported"
    return {
        "schema_version": 1,
        "board_profile": args.profile,
        "display_name": args.display_name,
        "vendor": args.vendor,
        "model": args.model,
        "hardware_revision": {
            "required": bool(args.revision_required),
            "supported": args.supported_revision,
            "unsupported": args.unsupported_revision,
        },
        "support_status": support_status,
        "sources": [
            {
                "label": args.source_label,
                "url": args.source_url,
            }
        ],
        "build": {
            "idf_target": args.soc,
            "partition_schema": args.partition_schema or partition_schema_for_soc(args.soc),
            "app_slot_size": args.app_slot_size or app_slot_size_for_soc(args.soc),
            "recovery_app": True,
            "compile_definitions": [upper_definition(args.profile)],
        },
        "hardware": {
            "soc": args.soc,
            "cpu": args.cpu or cpu_for_soc(args.soc),
            "flash_size": args.flash_size or flash_size_for_soc(args.soc),
            "psram_size": args.psram_size or psram_size_for_soc(args.soc),
            "wireless": wireless_defaults(args.soc, args.coprocessor),
        },
        "features": {
            "display": bool(args.with_display),
            "touch": bool(args.with_touch),
            "sd_card": bool(args.with_sd_card),
            "speaker": speaker,
            "microphone": microphone,
            "led_ring": bool(args.with_led_ring),
            "status_led": bool(args.with_status_led),
            "rotary_encoder": bool(args.with_rotary_encoder),
            "buttons": bool(args.with_buttons),
            "hardware_mute": bool(args.with_hardware_mute),
            "battery": bool(args.with_battery),
            "camera": bool(args.with_camera),
            "usb_otg": bool(args.with_usb_otg),
        },
        "display": display_section(args),
        "audio": {
            "input": {
                "available": microphone,
                "frontend": args.audio_input_frontend if microphone else None,
                "sample_rate_hz": args.audio_input_sample_rate_hz if microphone else None,
                "channels": args.audio_input_channels if microphone else None,
                "transport": args.audio_input_transport if microphone else None,
                "codec": args.audio_input_codec if microphone else None,
                "microphones": args.microphone_count if microphone else None,
                "dsp": {
                    "aec": bool(args.audio_dsp_aec),
                    "noise_suppression": bool(args.audio_dsp_noise_suppression),
                    "agc": bool(args.audio_dsp_agc),
                    "vad": bool(args.audio_dsp_vad),
                },
            },
            "output": {
                "available": speaker,
                "codec": args.audio_output_codec if speaker else None,
                "sample_rate_hz": args.audio_output_sample_rate_hz if speaker else None,
                "transport": args.audio_output_transport if speaker else None,
                "speaker": speaker,
                "line_out": bool(args.with_line_out),
            },
        },
        "wiring": {
            "status": "partial",
            "notes": "Generated scaffold; transcribe the dev-board schematic before marking complete.",
            "gpios": [],
            "i2c_buses": [],
            "i2s_buses": [],
            "spi_buses": [],
            "led_strips": [],
        },
        "wake": {
            "local_micro_wake_word": microphone,
            "primary_model": "alexa",
            "alias": "Hexe",
            "backend_fallback": True,
            "stop_model": "stop",
        },
        "vad": {
            "firmware": {
                "available": microphone,
                "status": firmware_vad_status,
                "algorithm": "energy_threshold",
                "input_source": "pcm_audio_frames",
                "configurable": microphone,
                "adaptive_noise_floor": False,
                "frame_ms": 20,
                "default_energy_threshold": 900,
                "default_pause_ms": 190,
                "silence_hold_ms": 1200,
            }
        },
        "adapters": {
            "buildable": False,
            "source_files": [],
            "notes": "Generated scaffold; add or select adapters after wiring is complete.",
        },
        "storage": {
            "internal": "nvs_and_internal_flash",
            "sd_card": {
                "available": bool(args.with_sd_card),
                "driver": "sdmmc_or_spi_tbd" if args.with_sd_card else None,
                "interface": "tbd" if args.with_sd_card else None,
                "gpio": None,
            },
            "config": "encrypted_nvs",
            "calibration": "encrypted_nvs_or_internal_metrics_store",
            "media": "sd_preferred_with_embedded_fallback" if args.with_sd_card else "embedded_minimal_tones_only",
            "models": "embedded_fallback_then_internal_or_sd_bundle" if args.with_sd_card else "embedded_fallback_then_internal_bundle_bank",
        },
        "controls": controls(args),
        "indicators": indicators(args),
        "capability_overrides": {
            "display": bool(args.with_display),
            "touchscreen": bool(args.with_touch),
            "storage": bool(args.with_sd_card),
            "audio_input": microphone,
            "audio_output": speaker,
        },
        "notes": [
            "Generated by firmware/tools/create_board_profile.py.",
            "Do not mark buildable until wiring.status is complete and adapter source files compile.",
        ],
    }


def add_bool_flag(parser: argparse.ArgumentParser, name: str, default: bool = False) -> None:
    parser.add_argument(f"--with-{name}", action="store_true", default=default)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold a planned Hexe firmware board profile.")
    parser.add_argument("--profile", required=True, help="Lower-snake-case board profile id.")
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--vendor", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--source-label", default="Board documentation")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("firmware/boards"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--soc", choices=["esp32s3", "esp32p4"], default="esp32s3")
    parser.add_argument("--cpu")
    parser.add_argument("--flash-size")
    parser.add_argument("--psram-size")
    parser.add_argument("--partition-schema", choices=["s3-8m-v1", "s3-16m-v1", "p4-32m-v1"])
    parser.add_argument("--app-slot-size")
    parser.add_argument("--coprocessor")
    parser.add_argument("--revision-required", action="store_true")
    parser.add_argument("--supported-revision", action="append", default=["prototype"])
    parser.add_argument("--unsupported-revision", action="append", default=[])
    add_bool_flag(parser, "display")
    add_bool_flag(parser, "touch")
    add_bool_flag(parser, "sd-card")
    add_bool_flag(parser, "speaker", default=True)
    add_bool_flag(parser, "microphone", default=True)
    add_bool_flag(parser, "led-ring")
    add_bool_flag(parser, "status-led", default=True)
    add_bool_flag(parser, "buttons", default=True)
    add_bool_flag(parser, "rotary-encoder")
    add_bool_flag(parser, "hardware-mute")
    add_bool_flag(parser, "battery")
    add_bool_flag(parser, "camera")
    add_bool_flag(parser, "usb-otg")
    add_bool_flag(parser, "line-out")
    parser.add_argument("--display-kind", default="lcd")
    parser.add_argument("--display-driver", default="tbd")
    parser.add_argument("--display-interface", default="tbd")
    parser.add_argument("--display-size-inches", type=float)
    parser.add_argument("--display-shape", choices=["rectangular", "round"], default="rectangular")
    parser.add_argument("--display-width-px", type=int)
    parser.add_argument("--display-height-px", type=int)
    parser.add_argument("--display-color-depth", default="rgb565")
    parser.add_argument("--audio-input-frontend", default="tbd")
    parser.add_argument("--audio-input-codec", default="tbd")
    parser.add_argument("--audio-input-transport", default="i2s")
    parser.add_argument("--audio-input-sample-rate-hz", type=int, default=16000)
    parser.add_argument("--audio-input-channels", type=int, default=1)
    parser.add_argument("--microphone-count", type=int, default=1)
    parser.add_argument("--audio-output-codec", default="tbd")
    parser.add_argument("--audio-output-transport", default="i2s")
    parser.add_argument("--audio-output-sample-rate-hz", type=int, default=48000)
    parser.add_argument("--audio-dsp-aec", action="store_true")
    parser.add_argument("--audio-dsp-noise-suppression", action="store_true")
    parser.add_argument("--audio-dsp-agc", action="store_true")
    parser.add_argument("--audio-dsp-vad", action="store_true")
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", args.profile):
        print("Board profile must be lower_snake_case.", file=sys.stderr)
        return 1
    if args.with_touch and not args.with_display:
        print("--with-touch requires --with-display.", file=sys.stderr)
        return 1
    if args.with_display and (args.display_size_inches is None or args.display_width_px is None or args.display_height_px is None):
        print("--with-display requires --display-size-inches, --display-width-px, and --display-height-px.", file=sys.stderr)
        return 1

    output_path = args.output_root / args.profile / "board.yaml"
    profile = build_profile(args)
    try:
        validate_profile(profile, output_path)
    except ValidationError as exc:
        print(f"Generated profile did not validate: {exc}", file=sys.stderr)
        return 1
    text = "\n".join(render_yaml(profile)) + "\n"
    if args.dry_run:
        print(text, end="")
        return 0
    if output_path.exists() and not args.force:
        print(f"Refusing to overwrite existing board profile: {output_path}", file=sys.stderr)
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
