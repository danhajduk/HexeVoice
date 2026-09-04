from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


VOICE_SCHEMA_FAMILY = "voice"
VOICE_SCHEMA_VERSION = "v1"


@dataclass(frozen=True)
class SchemaCatalogEntry:
    name: str
    relative_path: str
    category: str
    status: str = "Available"

    @property
    def api_path(self) -> str:
        return f"/api/schemas/{VOICE_SCHEMA_FAMILY}/{VOICE_SCHEMA_VERSION}/{self.name}"


VOICE_SCHEMA_ENTRIES: tuple[SchemaCatalogEntry, ...] = (
    SchemaCatalogEntry(
        name="schema-catalog.v1.response.schema.json",
        relative_path="docs/json-schemas/voice/schema-catalog.v1.response.schema.json",
        category="catalog",
    ),
    SchemaCatalogEntry(
        name="intent-registration.schema.json",
        relative_path="docs/json-schemas/intents/intent-registration.schema.json",
        category="intent",
    ),
    SchemaCatalogEntry(
        name="intent-reply.schema.json",
        relative_path="docs/json-schemas/intents/intent-reply.schema.json",
        category="intent",
    ),
    SchemaCatalogEntry(
        name="voice-intent-recognized-event.schema.json",
        relative_path="docs/json-schemas/intents/voice-intent-recognized-event.schema.json",
        category="intent",
    ),
    SchemaCatalogEntry(
        name="reply-audio-sidecar.schema.json",
        relative_path="docs/json-schemas/intents/reply-audio-sidecar.schema.json",
        category="intent",
    ),
    SchemaCatalogEntry(
        name="voice-event-envelope.schema.json",
        relative_path="docs/voice-event-envelope/envelope.schema.json",
        category="voice-event",
    ),
)


class VoiceSchemaCatalog:
    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path(__file__).resolve().parents[2]
        self._entries = {entry.name: entry for entry in VOICE_SCHEMA_ENTRIES}

    def catalog(self) -> dict[str, Any]:
        return {
            "schema_family": VOICE_SCHEMA_FAMILY,
            "version": VOICE_SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "schemas": [self._catalog_item(entry) for entry in VOICE_SCHEMA_ENTRIES],
        }

    def schema(self, name: str) -> dict[str, Any] | None:
        entry = self._entries.get(Path(name).name)
        if entry is None or Path(name).name != name:
            return None
        path = self._project_root / entry.relative_path
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _catalog_item(self, entry: SchemaCatalogEntry) -> dict[str, Any]:
        schema = self.schema(entry.name) or {}
        return {
            "name": entry.name,
            "schema_id": schema.get("$id"),
            "title": schema.get("title"),
            "category": entry.category,
            "status": entry.status,
            "api_path": entry.api_path,
        }
