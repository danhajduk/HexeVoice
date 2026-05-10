from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_piper_model_config(model_path: Path) -> dict[str, Any]:
    config_path = model_path.with_suffix(model_path.suffix + ".json")
    if not config_path.exists():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def piper_model_display_name(config: dict[str, Any], *, fallback: str) -> str:
    dataset = str(config.get("dataset") or "").strip()
    if not dataset:
        return fallback
    words = [word for word in dataset.replace("-", "_").split("_") if word]
    return " ".join(word.capitalize() for word in words) or fallback
