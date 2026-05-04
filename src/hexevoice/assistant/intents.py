from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class LocalIntentMatch:
    intent: str
    command: str
    slots: dict[str, Any]
    reply_text: str


class LocalIntentFinder:
    def find(self, text: str) -> LocalIntentMatch | None:
        normalized = _normalize_text(text)
        if not normalized:
            return None
        return self._find_timer_create(normalized)

    def status(self) -> dict[str, Any]:
        return {
            "provider": "local_pattern",
            "healthy": True,
            "configured": True,
            "intents": ["timer.create"],
        }

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
