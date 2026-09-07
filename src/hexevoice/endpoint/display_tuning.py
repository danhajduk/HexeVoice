from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)

MIN_FLUSH_ROWS = 1
MAX_FLUSH_ROWS = 16
MIN_PIXEL_CLOCK_HZ = 3_000_000
MAX_PIXEL_CLOCK_HZ = 40_000_000


def _int_in_range(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


def load_endpoint_display_tuning(path: Path) -> dict[str, dict[str, int]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Endpoint display tuning config ignored: path=%s error=%s", path, exc)
        return {}
    endpoints = raw.get("endpoints") if isinstance(raw, dict) else None
    if not isinstance(endpoints, dict):
        log.warning("Endpoint display tuning config ignored: path=%s reason=endpoints_not_object", path)
        return {}

    tuning: dict[str, dict[str, int]] = {}
    for endpoint_id, values in endpoints.items():
        if not isinstance(endpoint_id, str) or not endpoint_id or not isinstance(values, dict):
            continue
        payload: dict[str, int] = {}
        flush_rows = _int_in_range(values.get("flush_rows"), minimum=MIN_FLUSH_ROWS, maximum=MAX_FLUSH_ROWS)
        pixel_clock_hz = _int_in_range(
            values.get("pixel_clock_hz"),
            minimum=MIN_PIXEL_CLOCK_HZ,
            maximum=MAX_PIXEL_CLOCK_HZ,
        )
        if flush_rows is not None:
            payload["flush_rows"] = flush_rows
        if pixel_clock_hz is not None:
            payload["pixel_clock_hz"] = pixel_clock_hz
        if payload:
            tuning[endpoint_id] = payload
    return tuning
