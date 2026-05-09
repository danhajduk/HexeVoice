from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


VOICE_INTENT_SCHEMA_VERSION = "1.0"
ACTIVE_INTENT_STATES = {"active"}
KNOWN_INTENT_STATES = {"active", "restricted", "review_due", "probation", "disabled", "retired", "expired"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def timer_intent_definition() -> dict[str, Any]:
    return {
        "utterance_examples": [
            "set a timer for 5 minutes",
            "start a 10 minute timer",
            "timer for one hour",
            "five minutes timer",
        ],
        "patterns": [
            r"^(?:please\s+)?(?:set|start|create|make)\s+(?:a\s+|an\s+)?timer\s+(?:for|of)\s+(?P<duration>.+)$",
            r"^(?:please\s+)?timer\s+(?:for|of)\s+(?P<duration>.+)$",
            r"^(?:please\s+)?(?:set|start|create|make)\s+(?:a\s+|an\s+)?(?P<duration>.+?)\s+timer$",
            r"^(?:please\s+)?(?P<duration>.+?)\s+timer$",
        ],
        "slots": {
            "duration_seconds": {"type": "integer", "minimum": 1},
            "duration_text": {"type": "string"},
            "duration_hhmmss": {"type": "string"},
            "requested_at": {"type": "datetime"},
        },
        "extraction": {
            "required": {
                "duration_seconds": {"type": "integer", "source": "duration_seconds", "minimum": 1},
                "duration_text": {"type": "string", "source": "duration_text"},
                "duration_hhmmss": {"type": "string", "source": "duration_hhmmss"},
                "requested_at": {"type": "datetime", "source": "system_time"},
            }
        },
        "dispatch": {
            "type": "domain_event",
            "command": "timer.create",
            "event_type": "timer.create_requested",
        },
        "response": {
            "reply_template": "Setting timer for {duration_text}.",
        },
        "reply": {
            "text_template": "Setting timer for {duration_text}.",
            "audio": {
                "mode": "none",
                "ttl_seconds": 3600,
            },
        },
        "matcher": {
            "type": "builtin_timer",
        },
    }


def built_in_timer_intent() -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "intent_id": "timer.create",
        "intent_name": "Create timer",
        "service_id": "voice.local_intents",
        "owner_service": "hexevoice",
        "owner_client_id": None,
        "version": "v1",
        "status": "active",
        "privacy_class": "internal",
        "access_scope": "service",
        "definition": timer_intent_definition(),
        "constraints": {
            "requires_operational_mqtt": True,
            "dispatch_side_effect": "timer.create_requested",
        },
        "metadata": {
            "builtin": True,
            "family": "timer",
        },
        "reviews": [],
        "usage": {},
        "created_at": now,
        "updated_at": now,
    }


def time_query_intent_definition() -> dict[str, Any]:
    return {
        "utterance_examples": [
            "what is the time",
            "what time is it",
            "tell me the time",
            "current time",
        ],
        "patterns": [
            r"^(?:please\s+)?(?:what\s+is\s+the\s+time|what\s+time\s+is\s+it|tell\s+me\s+the\s+time|current\s+time)$",
        ],
        "slots": {
            "time_text": {"type": "string"},
            "timezone": {"type": "string"},
            "requested_at": {"type": "datetime"},
        },
        "extraction": {
            "optional": {
                "requested_at": {"type": "datetime", "source": "system_time"},
            }
        },
        "dispatch": {
            "type": "local_response",
            "command": "voice.time.query",
        },
        "response": {
            "reply_template": "It is {time_text}.",
        },
        "reply": {
            "text_template": "It is {time_text}.",
            "audio": {
                "mode": "none",
                "ttl_seconds": 3600,
            },
        },
        "matcher": {
            "type": "builtin_time_query",
        },
    }


def built_in_time_query_intent() -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "intent_id": "voice.time.query",
        "intent_name": "What is the time",
        "service_id": "voice.local_intents",
        "owner_service": "hexevoice",
        "owner_client_id": None,
        "version": "v1",
        "status": "active",
        "privacy_class": "internal",
        "access_scope": "service",
        "definition": time_query_intent_definition(),
        "constraints": {
            "requires_operational_mqtt": False,
            "dispatch_side_effect": "none",
        },
        "metadata": {
            "builtin": True,
            "family": "voice_node",
            "owned_by": "voice_node",
        },
        "reviews": [],
        "usage": {},
        "created_at": now,
        "updated_at": now,
    }


class VoiceIntentRecord(BaseModel):
    intent_id: str = Field(min_length=1, max_length=120)
    intent_name: str | None = Field(default=None, max_length=160)
    service_id: str = Field(default="voice.local_intents", min_length=1, max_length=160)
    owner_service: str | None = Field(default=None, max_length=160)
    owner_client_id: str | None = Field(default=None, max_length=160)
    version: str = Field(default="v1", min_length=1, max_length=80)
    status: str = "active"
    privacy_class: str = "internal"
    access_scope: str = "service"
    definition: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("intent_id", "service_id", "version", "status", "privacy_class", "access_scope")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("value_required")
        return stripped

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in KNOWN_INTENT_STATES:
            raise ValueError(f"unsupported_intent_status: {normalized}")
        return normalized

    @field_validator("definition")
    @classmethod
    def _validate_definition(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("intent_definition_must_be_object")
        dispatch = value.get("dispatch")
        if dispatch is not None and not isinstance(dispatch, dict):
            raise ValueError("intent_dispatch_must_be_object")
        patterns = value.get("patterns")
        if patterns is not None and not isinstance(patterns, list):
            raise ValueError("intent_patterns_must_be_list")
        examples = value.get("utterance_examples")
        if examples is not None and not isinstance(examples, list):
            raise ValueError("intent_utterance_examples_must_be_list")
        extraction = value.get("extraction")
        if extraction is not None:
            if not isinstance(extraction, dict):
                raise ValueError("intent_extraction_must_be_object")
            for section_name in ("required", "optional"):
                section = extraction.get(section_name)
                if section is not None and not isinstance(section, dict):
                    raise ValueError(f"intent_extraction_{section_name}_must_be_object")
                for field_name, field_schema in (section or {}).items():
                    if not isinstance(field_name, str) or not field_name.strip():
                        raise ValueError("intent_extraction_field_name_required")
                    if not isinstance(field_schema, dict):
                        raise ValueError("intent_extraction_field_schema_must_be_object")
        reply = value.get("reply")
        if reply is not None and not isinstance(reply, dict):
            raise ValueError("intent_reply_must_be_object")
        return value


class VoiceIntentState(BaseModel):
    schema_version: str = VOICE_INTENT_SCHEMA_VERSION
    intents: list[VoiceIntentRecord] = Field(default_factory=list)
    updated_at: str | None = None


class VoiceIntentStateStore:
    def __init__(self, *, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load_or_create(self) -> VoiceIntentState:
        if not self._path.exists():
            state = VoiceIntentState(
                intents=[
                    VoiceIntentRecord.model_validate(built_in_timer_intent()),
                    VoiceIntentRecord.model_validate(built_in_time_query_intent()),
                ],
                updated_at=utc_now_iso(),
            )
            return self.save(state)
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        state = VoiceIntentState.model_validate(payload)
        seeded = self._seed_builtin_intents(state)
        if seeded:
            return self.save(state)
        return state

    def save(self, state: VoiceIntentState) -> VoiceIntentState:
        updated = state.model_copy(update={"updated_at": utc_now_iso()})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
        temp_path.replace(self._path)
        return updated

    def _seed_builtin_intents(self, state: VoiceIntentState) -> bool:
        existing_ids = {intent.intent_id for intent in state.intents}
        seeded = False
        if "timer.create" not in existing_ids:
            state.intents.append(VoiceIntentRecord.model_validate(built_in_timer_intent()))
            seeded = True
        if "voice.time.query" not in existing_ids:
            state.intents.append(VoiceIntentRecord.model_validate(built_in_time_query_intent()))
            seeded = True
        return seeded


class VoiceIntentRegistry:
    def __init__(self, *, store: VoiceIntentStateStore) -> None:
        self._store = store
        self._state = store.load_or_create()

    def snapshot(self) -> dict[str, Any]:
        state = self._state.model_dump(mode="json")
        intents = state.get("intents") if isinstance(state, dict) else []
        active_count = len([intent for intent in intents if isinstance(intent, dict) and intent.get("status") in ACTIVE_INTENT_STATES])
        return {
            "configured": True,
            "schema_version": self._state.schema_version,
            "registered_count": len(intents),
            "active_count": active_count,
            "updated_at": self._state.updated_at,
            "intents": deepcopy(intents),
        }

    def list_intents(self) -> list[dict[str, Any]]:
        return deepcopy(self.snapshot()["intents"])

    def active_intents(self) -> list[dict[str, Any]]:
        return [intent for intent in self.list_intents() if intent.get("status") in ACTIVE_INTENT_STATES]

    def get_intent(self, *, intent_id: str) -> dict[str, Any]:
        record = self._find(intent_id)
        if record is None:
            raise ValueError("intent_id_not_registered")
        return record.model_dump(mode="json")

    def register_intent(
        self,
        *,
        intent_id: str,
        service_id: str = "voice.local_intents",
        intent_name: str | None = None,
        owner_service: str | None = None,
        owner_client_id: str | None = None,
        version: str | None = None,
        status: str = "active",
        privacy_class: str = "internal",
        access_scope: str = "service",
        definition: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self._find(intent_id)
        if existing is not None and existing.status != "retired":
            raise ValueError("duplicate_intent_id")
        now = utc_now_iso()
        payload = {
            "intent_id": intent_id,
            "intent_name": intent_name,
            "service_id": service_id,
            "owner_service": owner_service,
            "owner_client_id": owner_client_id,
            "version": version or "v1",
            "status": status,
            "privacy_class": privacy_class,
            "access_scope": access_scope,
            "definition": definition or {},
            "constraints": constraints or {},
            "metadata": metadata or {},
            "reviews": [],
            "usage": {},
            "created_at": now,
            "updated_at": now,
        }
        record = VoiceIntentRecord.model_validate(payload)
        if existing is None:
            self._state.intents.append(record)
        else:
            self._state.intents = [record if intent.intent_id == intent_id else intent for intent in self._state.intents]
        self._save()
        return self.snapshot()

    def update_intent(
        self,
        *,
        intent_id: str,
        intent_name: str | None = None,
        service_id: str | None = None,
        owner_service: str | None = None,
        owner_client_id: str | None = None,
        version: str | None = None,
        privacy_class: str | None = None,
        access_scope: str | None = None,
        definition: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self._find(intent_id)
        if record is None:
            raise ValueError("intent_id_not_registered")
        update: dict[str, Any] = {"updated_at": utc_now_iso()}
        for key, value in {
            "intent_name": intent_name,
            "service_id": service_id,
            "owner_service": owner_service,
            "owner_client_id": owner_client_id,
            "version": version,
            "privacy_class": privacy_class,
            "access_scope": access_scope,
            "definition": definition,
            "constraints": constraints,
            "metadata": metadata,
        }.items():
            if value is not None:
                update[key] = value
        replacement = record.model_copy(update=update)
        VoiceIntentRecord.model_validate(replacement.model_dump(mode="json"))
        self._replace(replacement)
        self._save()
        return self.snapshot()

    def transition_intent(self, *, intent_id: str, status: str, reason: str | None = None) -> dict[str, Any]:
        record = self._find(intent_id)
        if record is None:
            raise ValueError("intent_id_not_registered")
        normalized_status = status.strip().lower()
        if normalized_status not in KNOWN_INTENT_STATES:
            raise ValueError(f"unsupported_intent_status: {normalized_status}")
        metadata = dict(record.metadata)
        transitions = list(metadata.get("lifecycle_transitions") or [])
        transitions.append(
            {
                "status": normalized_status,
                "reason": reason,
                "changed_at": utc_now_iso(),
            }
        )
        metadata["lifecycle_transitions"] = transitions[-20:]
        self._replace(record.model_copy(update={"status": normalized_status, "metadata": metadata, "updated_at": utc_now_iso()}))
        self._save()
        return self.snapshot()

    def review_intent(
        self,
        *,
        intent_id: str,
        reviewed_by: str | None = None,
        review_reason: str | None = None,
        status: str | None = "active",
    ) -> dict[str, Any]:
        record = self._find(intent_id)
        if record is None:
            raise ValueError("intent_id_not_registered")
        reviews = list(record.reviews or [])
        reviews.append(
            {
                "reviewed_by": reviewed_by,
                "review_reason": review_reason,
                "status": status,
                "reviewed_at": utc_now_iso(),
            }
        )
        update = {"reviews": reviews[-20:], "updated_at": utc_now_iso()}
        if status:
            normalized_status = status.strip().lower()
            if normalized_status not in KNOWN_INTENT_STATES:
                raise ValueError(f"unsupported_intent_status: {normalized_status}")
            update["status"] = normalized_status
        self._replace(record.model_copy(update=update))
        self._save()
        return self.snapshot()

    def record_usage(self, *, intent_id: str, status: str, reason: str | None = None) -> None:
        record = self._find(intent_id)
        if record is None:
            return
        now = utc_now_iso()
        usage = dict(record.usage or {})
        usage["match_count"] = max(int(usage.get("match_count") or 0), 0) + 1
        usage["last_match_status"] = status
        usage["last_matched_at"] = now
        if reason:
            usage["last_reason"] = reason
        self._replace(record.model_copy(update={"usage": usage, "updated_at": now}))
        self._save()

    def _find(self, intent_id: str) -> VoiceIntentRecord | None:
        wanted = str(intent_id or "").strip()
        for record in self._state.intents:
            if record.intent_id == wanted:
                return record
        return None

    def _replace(self, replacement: VoiceIntentRecord) -> None:
        self._state.intents = [
            replacement if record.intent_id == replacement.intent_id else record for record in self._state.intents
        ]

    def _save(self) -> None:
        try:
            self._state = self._store.save(VoiceIntentState.model_validate(self._state.model_dump(mode="json")))
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
