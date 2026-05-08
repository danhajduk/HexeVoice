from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any

from hexevoice.assistant.intent_registry import VoiceIntentRegistry

@dataclass(frozen=True)
class LocalIntentMatch:
    intent: str
    command: str
    slots: dict[str, Any]
    reply_text: str
    provider_id: str = "local_pattern"
    intent_name: str | None = None
    service_id: str | None = None
    version: str | None = None
    definition: dict[str, Any] | None = None
    dispatch: dict[str, Any] | None = None
    reply: dict[str, Any] | None = None


class LocalIntentFinder:
    def __init__(self, *, registry: VoiceIntentRegistry | None = None) -> None:
        self._registry = registry

    def find(self, text: str, *, requested_at: datetime | None = None) -> LocalIntentMatch | None:
        normalized = _normalize_text(text)
        if not normalized:
            return None
        extraction_time = requested_at or datetime.now(UTC)
        for intent in self._candidate_intents():
            match = self._match_registered_intent(intent, normalized, requested_at=extraction_time)
            if match is not None:
                return match
        if self._registry is None:
            return self._find_timer_create(normalized, requested_at=extraction_time)
        return None

    def status(self) -> dict[str, Any]:
        if self._registry is not None:
            snapshot = self._registry.snapshot()
            return {
                "provider": "registered_intent",
                "healthy": True,
                "configured": True,
                "registered_count": snapshot["registered_count"],
                "active_count": snapshot["active_count"],
                "intents": [intent["intent_id"] for intent in snapshot["intents"] if intent.get("status") == "active"],
            }
        return {
            "provider": "local_pattern",
            "healthy": True,
            "configured": True,
            "intents": ["timer.create"],
        }

    def _candidate_intents(self) -> list[dict[str, Any]]:
        if self._registry is None:
            return []
        return self._registry.active_intents()

    def _match_registered_intent(
        self,
        intent: dict[str, Any],
        text: str,
        *,
        requested_at: datetime,
    ) -> LocalIntentMatch | None:
        definition = intent.get("definition") if isinstance(intent.get("definition"), dict) else {}
        dispatch = definition.get("dispatch") if isinstance(definition.get("dispatch"), dict) else {}
        matcher = definition.get("matcher") if isinstance(definition.get("matcher"), dict) else {}
        command = str(dispatch.get("command") or intent.get("intent_id") or "").strip()
        if not command:
            return None

        if matcher.get("type") == "builtin_timer" or command == "timer.create" or intent.get("intent_id") == "timer.create":
            match = self._find_timer_create(text, requested_at=requested_at)
            if match is not None:
                return self._build_registered_match(
                    intent=intent,
                    command=command,
                    slots=match.slots,
                    requested_at=requested_at,
                )
            return None

        slots: dict[str, Any] = {}
        if _matches_examples(text, definition.get("utterance_examples")):
            return self._build_registered_match(intent=intent, command=command, slots=slots, requested_at=requested_at)

        for pattern in definition.get("patterns") or []:
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            try:
                matched = re.match(pattern, text)
            except re.error:
                self._record_match(intent.get("intent_id"), status="invalid_pattern", reason=pattern)
                continue
            if matched:
                slots.update({key: value for key, value in matched.groupdict().items() if value is not None})
                return self._build_registered_match(intent=intent, command=command, slots=slots, requested_at=requested_at)

        return None

    def _build_registered_match(
        self,
        *,
        intent: dict[str, Any],
        command: str,
        slots: dict[str, Any],
        requested_at: datetime,
    ) -> LocalIntentMatch | None:
        definition = intent.get("definition") if isinstance(intent.get("definition"), dict) else {}
        try:
            extracted_slots = _validate_extracted_slots(definition=definition, slots=slots, requested_at=requested_at)
        except ValueError as exc:
            self._record_match(intent.get("intent_id"), status="invalid_extraction", reason=str(exc))
            return None
        self._record_match(intent.get("intent_id"), status="matched")
        return self._build_generic_match(intent=intent, command=command, slots=extracted_slots)

    def _build_generic_match(self, *, intent: dict[str, Any], command: str, slots: dict[str, Any]) -> LocalIntentMatch:
        definition = intent.get("definition") if isinstance(intent.get("definition"), dict) else {}
        response = definition.get("response") if isinstance(definition.get("response"), dict) else {}
        reply = definition.get("reply") if isinstance(definition.get("reply"), dict) else {}
        reply_text = str(reply.get("text") or reply.get("text_template") or response.get("reply_text") or response.get("reply_template") or "").strip()
        if reply_text:
            try:
                reply_text = reply_text.format(**slots)
            except (KeyError, ValueError):
                pass
        if not reply_text:
            name = str(intent.get("intent_name") or intent.get("intent_id") or command)
            reply_text = f"{name} accepted."
        return LocalIntentMatch(
            intent=str(intent.get("intent_id") or command),
            command=command,
            slots=slots,
            reply_text=reply_text,
            provider_id="registered_intent",
            intent_name=str(intent.get("intent_name")) if intent.get("intent_name") else None,
            service_id=str(intent.get("service_id")) if intent.get("service_id") else None,
            version=str(intent.get("version")) if intent.get("version") else None,
            definition=definition,
            dispatch=definition.get("dispatch") if isinstance(definition.get("dispatch"), dict) else None,
            reply=reply,
        )

    def _record_match(self, intent_id: object, *, status: str, reason: str | None = None) -> None:
        if self._registry is None or not isinstance(intent_id, str):
            return
        self._registry.record_usage(intent_id=intent_id, status=status, reason=reason)

    def _find_timer_create(self, text: str, *, requested_at: datetime | None = None) -> LocalIntentMatch | None:
        duration_text = _extract_timer_duration_text(text)
        if duration_text is None:
            return None

        duration_seconds = _parse_duration_seconds(duration_text)
        if duration_seconds is None:
            return None

        formatted_duration = _format_duration(duration_seconds)
        extraction_time = requested_at or datetime.now(UTC)
        return LocalIntentMatch(
            intent="timer.create",
            command="timer.create",
            slots={
                "duration_seconds": duration_seconds,
                "duration_hhmmss": _format_duration_hhmmss(duration_seconds),
                "duration_text": formatted_duration,
                "requested_at": extraction_time.isoformat(),
            },
            reply_text=f"Setting timer for {formatted_duration}.",
        )


def _normalize_text(text: str) -> str:
    normalized = text.strip().lower()
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip(" .!?")


def _matches_examples(text: str, examples: object) -> bool:
    if not isinstance(examples, list):
        return False
    normalized_examples = {_normalize_text(example) for example in examples if isinstance(example, str)}
    return text in normalized_examples


def _extract_timer_duration_text(text: str) -> str | None:
    patterns = [
        r"^(?:please\s+)?(?:set|start|create|make)\s+(?:a\s+|an\s+)?timer\s+(?:for|of)\s+(?P<duration>.+)$",
        r"^(?:please\s+)?timer\s+(?:for|of)\s+(?P<duration>.+)$",
        r"^(?:please\s+)?(?:set|start|create|make)\s+(?:a\s+|an\s+)?(?P<duration>.+?)\s+timer$",
        r"^(?:please\s+)?(?P<duration>.+?)\s+timer$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return _trim_duration_tail(match.group("duration"))
    return None


def _trim_duration_tail(text: str) -> str:
    trimmed = re.split(r"\s+(?:called|named|labelled|labeled)\s+", text, maxsplit=1)[0]
    return trimmed.strip(" .!?")


_NUMBER_WORDS: dict[str, float] = {
    "zero": 0,
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "half": 0.5,
}

_NUMBER_PATTERN = (
    r"\d+(?:\.\d+)?"
    r"|a"
    r"|an"
    r"|half"
    r"|one|two|three|four|five|six|seven|eight|nine|ten"
    r"|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
    r"|twenty(?:\s+(?:one|two|three|four|five|six|seven|eight|nine))?"
    r"|thirty(?:\s+(?:one|two|three|four|five|six|seven|eight|nine))?"
    r"|forty(?:\s+(?:one|two|three|four|five|six|seven|eight|nine))?"
    r"|fifty(?:\s+(?:one|two|three|four|five|six|seven|eight|nine))?"
    r"|sixty"
)
_DURATION_PART_RE = re.compile(
    rf"\b(?P<number>{_NUMBER_PATTERN})\s*(?P<unit>hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s)\b"
)
_UNIT_SECONDS = {
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
}


def _parse_duration_seconds(text: str) -> int | None:
    total_seconds = 0.0
    for match in _DURATION_PART_RE.finditer(text):
        amount = _parse_number(match.group("number"))
        unit_seconds = _UNIT_SECONDS[match.group("unit")]
        total_seconds += amount * unit_seconds

    if total_seconds <= 0:
        return None
    return int(round(total_seconds))


def _format_duration_hhmmss(duration_seconds: int) -> str:
    seconds = max(0, int(duration_seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _validate_extracted_slots(
    *,
    definition: dict[str, Any],
    slots: dict[str, Any],
    requested_at: datetime,
) -> dict[str, Any]:
    extraction = definition.get("extraction") if isinstance(definition.get("extraction"), dict) else {}
    if not extraction:
        return dict(slots)

    extracted = dict(slots)
    for required, section in ((True, extraction.get("required")), (False, extraction.get("optional"))):
        if section is None:
            continue
        if not isinstance(section, dict):
            raise ValueError("extraction_section_must_be_object")
        for field_name, field_schema in section.items():
            if not isinstance(field_schema, dict):
                raise ValueError(f"{field_name}:schema_must_be_object")
            value = _extract_field_value(field_name=field_name, field_schema=field_schema, slots=extracted, requested_at=requested_at)
            if _is_missing(value):
                if "default" in field_schema:
                    value = field_schema.get("default")
                elif required or field_schema.get("required") is True:
                    raise ValueError(f"{field_name}:required")
                else:
                    continue
            extracted[field_name] = _coerce_and_validate_field(field_name, value, field_schema)
    return extracted


def _extract_field_value(
    *,
    field_name: str,
    field_schema: dict[str, Any],
    slots: dict[str, Any],
    requested_at: datetime,
) -> Any:
    source = str(field_schema.get("source") or field_name).strip()
    if source in {"system_time", "requested_at", "now"}:
        return requested_at.isoformat()
    if source.startswith("slot:"):
        return slots.get(source.split(":", 1)[1])
    if source == "duration_hhmmss":
        duration_seconds = slots.get("duration_seconds")
        return _format_duration_hhmmss(int(duration_seconds)) if isinstance(duration_seconds, int) else None
    if source == "value":
        return field_schema.get("value")
    return slots.get(source)


def _coerce_and_validate_field(field_name: str, value: Any, field_schema: dict[str, Any]) -> Any:
    field_type = str(field_schema.get("type") or "").strip().lower()
    if field_type in {"integer", "int"}:
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}:invalid_integer") from exc
        minimum = field_schema.get("minimum")
        if minimum is not None and value < int(minimum):
            raise ValueError(f"{field_name}:below_minimum")
    elif field_type in {"number", "float"}:
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}:invalid_number") from exc
        minimum = field_schema.get("minimum")
        if minimum is not None and value < float(minimum):
            raise ValueError(f"{field_name}:below_minimum")
    elif field_type in {"string", "datetime"}:
        value = str(value)
        if field_schema.get("min_length") is not None and len(value) < int(field_schema["min_length"]):
            raise ValueError(f"{field_name}:too_short")
        if field_type == "datetime":
            try:
                datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{field_name}:invalid_datetime") from exc
    elif field_type == "boolean":
        if isinstance(value, bool):
            pass
        elif str(value).strip().lower() in {"1", "true", "yes", "on"}:
            value = True
        elif str(value).strip().lower() in {"0", "false", "no", "off"}:
            value = False
        else:
            raise ValueError(f"{field_name}:invalid_boolean")
    enum = field_schema.get("enum")
    if isinstance(enum, list) and enum and value not in enum:
        raise ValueError(f"{field_name}:not_in_enum")
    return value


def _is_missing(value: Any) -> bool:
    return value is None or value == ""


def _parse_number(text: str) -> float:
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)

    words = text.split()
    return sum(_NUMBER_WORDS[word] for word in words)


def _format_duration(total_seconds: int) -> str:
    remaining = total_seconds
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)
    parts: list[str] = []
    if hours:
        parts.append(_format_unit(hours, "hour"))
    if minutes:
        parts.append(_format_unit(minutes, "minute"))
    if seconds or not parts:
        parts.append(_format_unit(seconds, "second"))
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _format_unit(value: int, unit: str) -> str:
    suffix = "" if value == 1 else "s"
    return f"{value} {unit}{suffix}"
