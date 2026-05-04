from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from typing import Any


log = logging.getLogger("hexevoice.voice.records")


def record_voice_event(event_type: str, **fields: Any) -> None:
    payload = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "event_type": event_type,
        **fields,
    }
    log.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
