from __future__ import annotations

from dataclasses import dataclass
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


class LocalIntentFinder:
    def __init__(self, *, registry: VoiceIntentRegistry | None = None) -> None:
        self._registry = registry

    def find(self, text: str) -> LocalIntentMatch | None:
        normalized = _normalize_text(text)
        if not normalized:
            return None
        for intent in self._candidate_intents():
            match = self._match_registered_intent(intent, normalized)
            if match is not None:
                return match
        if self._registry is None:
            return self._find_timer_create(normalized)
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

    def _match_registered_intent(self, intent: dict[str, Any], text: str) -> LocalIntentMatch | None:
        definition = intent.get("definition") if isinstance(intent.get("definition"), dict) else {}
        dispatch = definition.get("dispatch") if isinstance(definition.get("dispatch"), dict) else {}
        matcher = definition.get("matcher") if isinstance(definition.get("matcher"), dict) else {}
        command = str(dispatch.get("command") or intent.get("intent_id") or "").strip()
        if not command:
            return None

        if matcher.get("type") == "builtin_timer" or command == "timer.create" or intent.get("intent_id") == "timer.create":
            match = self._find_timer_create(text)
            if match is not None:
                self._record_match(intent.get("intent_id"), status="matched")
                return LocalIntentMatch(
                    intent=str(intent.get("intent_id") or match.intent),
                    command=command,
                    slots=match.slots,
                    reply_text=match.reply_text,
                    provider_id="registered_intent",
                )
            return None

        slots: dict[str, Any] = {}
        if _matches_examples(text, definition.get("utterance_examples")):
            self._record_match(intent.get("intent_id"), status="matched")
            return self._build_generic_match(intent=intent, command=command, slots=slots)

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
                self._record_match(intent.get("intent_id"), status="matched")
                return self._build_generic_match(intent=intent, command=command, slots=slots)

        return None

    def _build_generic_match(self, *, intent: dict[str, Any], command: str, slots: dict[str, Any]) -> LocalIntentMatch:
        definition = intent.get("definition") if isinstance(intent.get("definition"), dict) else {}
        response = definition.get("response") if isinstance(definition.get("response"), dict) else {}
        reply_text = str(response.get("reply_text") or response.get("reply_template") or "").strip()
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
        )

    def _record_match(self, intent_id: object, *, status: str, reason: str | None = None) -> None:
        if self._registry is None or not isinstance(intent_id, str):
            return
        self._registry.record_usage(intent_id=intent_id, status=status, reason=reason)

    def _find_timer_create(self, text: str) -> LocalIntentMatch | None:
        duration_text = _extract_timer_duration_text(text)
        if duration_text is None:
            return None

        duration_seconds = _parse_duration_seconds(duration_text)
        if duration_seconds is None:
            return None

        formatted_duration = _format_duration(duration_seconds)
        return LocalIntentMatch(
            intent="timer.create",
            command="timer.create",
            slots={
                "duration_seconds": duration_seconds,
                "duration_text": formatted_duration,
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
