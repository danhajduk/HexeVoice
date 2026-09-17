#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from yaml_to_json import load_yaml_with_includes


OUTPUT_FILES = (
    "chrome_layout.json",
    "status_icons.json",
    "activity_layout.json",
    "idle_layout.json",
    "screens_layout.json",
)


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def load_config(path: Path) -> dict[str, Any]:
    return require_mapping(load_yaml_with_includes(path), str(path))


def without(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in source.items() if key not in keys}


def expand_animations(value: Any, presets: dict[str, Any], label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    expanded: list[dict[str, Any]] = []
    for index, entry in enumerate(require_list(value, label)):
        if isinstance(entry, str):
            preset_name = entry
            overrides: dict[str, Any] = {}
        else:
            overrides = require_mapping(entry, f"{label}[{index}]")
            preset_name = overrides.get("preset")
        if not isinstance(preset_name, str) or not preset_name:
            raise ValueError(f"{label}[{index}] requires a preset")
        preset = require_mapping(presets.get(preset_name), f"animation preset {preset_name!r}")
        animation = {**preset, **without(overrides, "preset")}
        expanded.append(animation)
    return expanded


def expand_owner_animations(owner: Any, presets: dict[str, Any], label: str) -> None:
    if not isinstance(owner, dict) or "animations" not in owner:
        return
    owner["animations"] = expand_animations(owner["animations"], presets, f"{label} animations")


def index_items(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(require_list(payload.get("items"), "items.items")):
        item = require_mapping(value, f"items.items[{index}]")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(f"items.items[{index}] requires an id")
        if item_id in indexed:
            raise ValueError(f"duplicate item id: {item_id}")
        indexed[item_id] = item
    return indexed


def compile_items(items: dict[str, dict[str, Any]], presets: dict[str, Any]) -> dict[str, dict[str, Any]]:
    chrome: dict[str, Any] = {"schema_version": 1, "sidebars": {}}
    icons: dict[str, Any] = {"schema_version": 1, "icons": {"items": []}}
    activity: dict[str, Any] = {"schema_version": 1, "activity_sprites": {"items": []}}
    idle: dict[str, Any] = {
        "schema_version": 1,
        "idle_clock": {"enabled": True},
        "timer_screen": {"enabled": True},
    }

    for item_id, item in items.items():
        item_type = item.get("type")
        config = without(item, "id", "type", "data", "side", "sprite", "placement")
        if "animations" in config:
            config["animations"] = expand_animations(
                config["animations"], presets, f"item {item_id!r} animations"
            )
        if item_type == "header_clock":
            chrome["clock"] = config
        elif item_type == "firmware_version":
            chrome["version"] = config
        elif item_type == "sidebar":
            side = item.get("side")
            if side not in {"left", "right"}:
                raise ValueError(f"sidebar item {item_id!r} requires side: left or right")
            animations = config.pop("animations", [])
            if len(animations) != 1:
                raise ValueError(f"sidebar item {item_id!r} requires exactly one animation")
            chrome["sidebars"][side] = animations[0]
        elif item_type == "button_stack":
            chrome["sidebar_buttons"] = config
        elif item_type == "status_icon_group":
            icons["icons"].update(config)
        elif item_type == "status_icon":
            icon = {
                "id": item.get("sprite", item_id),
                "placement": item.get("placement", "floating"),
                **config,
            }
            icons["icons"]["items"].append(icon)
        elif item_type == "activity_sprite":
            activity["activity_sprites"]["items"].append(
                {"id": item.get("data", item_id), **config}
            )
        elif item_type == "big_clock":
            if "frame" in config:
                config["sprite"] = config.pop("frame")
            for part in ("sprite", "hours", "separator", "minutes"):
                expand_owner_animations(config.get(part), presets, f"item {item_id!r}.{part}")
            idle["idle_clock"].update(config)
        elif item_type == "big_date":
            idle["idle_clock"]["date_format"] = item.get("format", "%A, %B %d %Y.")
            idle["idle_clock"]["date"] = without(config, "format")
        elif item_type == "timer_primary":
            idle["timer_screen"]["primary_countdown"] = require_mapping(
                config.get("countdown"), f"item {item_id!r}.countdown"
            )
            idle["timer_screen"]["primary_label"] = require_mapping(
                config.get("label"), f"item {item_id!r}.label"
            )
        elif item_type == "timer_upcoming":
            idle["timer_screen"]["upcoming"] = config
        elif item_type in {"progress_bar", "button"}:
            continue
        else:
            raise ValueError(f"item {item_id!r} has unsupported type {item_type!r}")

    return {
        "chrome_layout.json": chrome,
        "status_icons.json": icons,
        "activity_layout.json": activity,
        "idle_layout.json": idle,
    }


def screen_element(
    placement: dict[str, Any],
    item: dict[str, Any],
    presets: dict[str, Any],
) -> dict[str, Any] | None:
    item_id = str(item["id"])
    item_type = item.get("type")
    type_map = {
        "header_clock": "clock",
        "big_clock": "big_clock",
        "big_date": "big_date",
        "activity_sprite": "activity",
        "timer_primary": "timer_primary",
        "timer_upcoming": "timer_upcoming",
        "progress_bar": "progress_bar",
    }
    element_type = type_map.get(str(item_type))
    if element_type is None:
        return None
    element: dict[str, Any] = {"type": element_type, "item": item_id}
    if item_type == "activity_sprite":
        element["sprite"] = item.get("data", item_id)
    for key in ("x", "y", "width", "height", "color", "track_color"):
        if key in placement:
            element[key] = placement[key]
        elif key in item:
            element[key] = item[key]
    animations = placement.get("animations")
    if animations is not None:
        element["animations"] = expand_animations(
            animations, presets, f"screen item {item_id!r} animations"
        )
    return element


def compile_screens(
    payload: dict[str, Any],
    items: dict[str, dict[str, Any]],
    animation_presets: dict[str, Any],
    button_presets: dict[str, Any],
) -> dict[str, Any]:
    compiled: list[dict[str, Any]] = []
    for screen_index, value in enumerate(require_list(payload.get("screens"), "screens.screens")):
        screen = require_mapping(value, f"screens.screens[{screen_index}]")
        screen_id = screen.get("id")
        if not isinstance(screen_id, str) or not screen_id:
            raise ValueError(f"screens.screens[{screen_index}] requires an id")
        output: dict[str, Any] = {
            "id": screen_id,
            "sidebars": bool(screen.get("sidebars", False)),
            "elements": [],
        }
        if "conditions" in screen:
            output["conditions"] = screen["conditions"]
        for placement_index, placement_value in enumerate(
            require_list(screen.get("items"), f"screen {screen_id!r}.items")
        ):
            placement = require_mapping(
                placement_value, f"screen {screen_id!r}.items[{placement_index}]"
            )
            item_id = placement.get("item")
            if not isinstance(item_id, str) or item_id not in items:
                raise ValueError(f"screen {screen_id!r} references unknown item {item_id!r}")
            element = screen_element(placement, items[item_id], animation_presets)
            if element is not None:
                output["elements"].append(element)

        buttons = require_mapping(screen.get("buttons"), f"screen {screen_id!r}.buttons")
        if "preset" in buttons:
            preset_name = buttons["preset"]
            if not isinstance(preset_name, str) or preset_name not in button_presets:
                raise ValueError(f"screen {screen_id!r} references unknown button preset {preset_name!r}")
            button_ids = require_list(button_presets[preset_name], f"button preset {preset_name!r}")
        else:
            button_ids = require_list(buttons.get("items"), f"screen {screen_id!r}.buttons.items")
        for button_id in button_ids:
            if not isinstance(button_id, str) or items.get(button_id, {}).get("type") != "button":
                raise ValueError(f"screen {screen_id!r} references unknown button {button_id!r}")
        output["buttons"] = button_ids
        compiled.append(output)
    return {"schema_version": 2, "screens": compiled}


def compile_config(config_dir: Path) -> dict[str, dict[str, Any]]:
    animation_payload = load_config(config_dir / "animation_presets.yaml")
    item_payload = load_config(config_dir / "items.yaml")
    button_payload = load_config(config_dir / "button_presets.yaml")
    screen_payload = load_config(config_dir / "screens_layout.yaml")
    animation_presets = require_mapping(animation_payload.get("presets"), "animation presets")
    button_presets = require_mapping(button_payload.get("presets"), "button presets")
    items = index_items(item_payload)
    outputs = compile_items(items, animation_presets)
    outputs["screens_layout.json"] = compile_screens(
        screen_payload, items, animation_presets, button_presets
    )
    outputs["status_layout.json"] = {
        "schema_version": 3,
        "files": list(OUTPUT_FILES),
    }
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile the P4 UI YAML schema into firmware JSON.")
    parser.add_argument("config_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    try:
        outputs = compile_config(args.config_dir)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"P4 UI configuration error: {exc}") from exc
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in outputs.items():
        output = args.output_dir / filename
        output.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
