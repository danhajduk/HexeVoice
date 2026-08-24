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
    conversation_followup: dict[str, Any] | None = None


class LocalIntentFinder:
    def __init__(self, *, registry: VoiceIntentRegistry | None = None) -> None:
        self._registry = registry

    def find(
        self,
        text: str,
        *,
        requested_at: datetime | None = None,
        pending_followup: dict[str, Any] | None = None,
    ) -> LocalIntentMatch | None:
        normalized = _normalize_text(text)
        if not normalized:
            return None
        extraction_time = requested_at or datetime.now(UTC)
        for intent in self._candidate_intents():
            match = self._match_registered_intent(
                intent,
                normalized,
                requested_at=extraction_time,
                pending_followup=pending_followup,
            )
            if match is not None:
                return match
        if self._registry is None:
            return (
                self._find_timer_create(normalized, requested_at=extraction_time)
                or self._find_timer_status(normalized, requested_at=extraction_time)
                or self._find_timer_control(normalized, action="stop", requested_at=extraction_time)
                or self._find_timer_control(normalized, action="cancel", requested_at=extraction_time)
                or self._find_timer_adjust_time(normalized, requested_at=extraction_time)
                or self._find_timer_snooze(normalized, requested_at=extraction_time)
                or self._find_endpoint_control(normalized, command="playback.stop", requested_at=extraction_time)
                or self._find_endpoint_control(normalized, command="playback.repeat", requested_at=extraction_time)
                or self._find_endpoint_control(normalized, command="endpoint.volume.set", requested_at=extraction_time)
                or self._find_endpoint_control(normalized, command="endpoint.volume.adjust", requested_at=extraction_time)
                or self._find_endpoint_control(normalized, command="endpoint.mute", requested_at=extraction_time)
                or self._find_endpoint_control(normalized, command="endpoint.unmute", requested_at=extraction_time)
                or self._find_endpoint_control(normalized, command="endpoint.identify", requested_at=extraction_time)
            )
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
            "intents": [
                "timer.create",
                "timer.status",
                "timer.stop",
                "timer.cancel",
                "timer.adjust_time",
                "timer.snooze",
                "playback.stop",
                "playback.repeat",
                "endpoint.volume.set",
                "endpoint.volume.adjust",
                "endpoint.mute",
                "endpoint.unmute",
                "endpoint.identify",
            ],
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
        pending_followup: dict[str, Any] | None = None,
    ) -> LocalIntentMatch | None:
        definition = intent.get("definition") if isinstance(intent.get("definition"), dict) else {}
        dispatch = definition.get("dispatch") if isinstance(definition.get("dispatch"), dict) else {}
        matcher = definition.get("matcher") if isinstance(definition.get("matcher"), dict) else {}
        command = str(dispatch.get("command") or intent.get("intent_id") or "").strip()
        if not command:
            return None

        if (
            matcher.get("type") == "builtin_confirmation"
            or command in {"voice.confirm.yes", "voice.confirm.no"}
            or intent.get("intent_id") in {"voice.confirm.yes", "voice.confirm.no"}
        ):
            response = str(matcher.get("response") or command.rsplit(".", 1)[-1]).strip().lower()
            if response not in {"yes", "no"}:
                return None
            if not _is_confirmation_response(text, response):
                return None
            if not pending_followup:
                self._record_match(intent.get("intent_id"), status="ignored", reason="missing_pending_followup")
                return None
            return self._build_confirmation_match(
                intent=intent,
                command=command,
                response=response,
                pending_followup=pending_followup,
                requested_at=requested_at,
            )

        if _is_short_intent_utterance(text) and not pending_followup and not _allows_global_short_intent(intent, definition):
            self._record_match(intent.get("intent_id"), status="ignored", reason="short_intent_requires_followup")
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

        if (
            matcher.get("type") == "builtin_time_query"
            or command == "voice.time.query"
            or intent.get("intent_id") == "voice.time.query"
        ):
            match = self._find_time_query(text, requested_at=requested_at)
            if match is not None:
                return self._build_registered_match(
                    intent=intent,
                    command=command,
                    slots=match.slots,
                    requested_at=requested_at,
                )
            return None

        if (
            matcher.get("type") == "builtin_timer_status"
            or command == "timer.status"
            or intent.get("intent_id") == "timer.status"
        ):
            match = self._find_timer_status(text, requested_at=requested_at)
            if match is not None:
                return self._build_registered_match(
                    intent=intent,
                    command=command,
                    slots=match.slots,
                    requested_at=requested_at,
                )
            return None

        if (
            matcher.get("type") == "builtin_timer_control"
            or command in {"timer.stop", "timer.cancel"}
            or intent.get("intent_id") in {"timer.stop", "timer.cancel"}
        ):
            action = str(matcher.get("action") or command.rsplit(".", 1)[-1]).strip().lower()
            match = self._find_timer_control(text, action=action, requested_at=requested_at)
            if match is not None:
                return self._build_registered_match(
                    intent=intent,
                    command=command,
                    slots=match.slots,
                    requested_at=requested_at,
                )
            return None

        if (
            matcher.get("type") == "builtin_timer_adjust_time"
            or command == "timer.adjust_time"
            or intent.get("intent_id") == "timer.adjust_time"
        ):
            match = self._find_timer_adjust_time(text, requested_at=requested_at)
            if match is not None:
                return self._build_registered_match(
                    intent=intent,
                    command=command,
                    slots=match.slots,
                    requested_at=requested_at,
                )
            return None

        if (
            matcher.get("type") == "builtin_timer_snooze"
            or command == "timer.snooze"
            or intent.get("intent_id") == "timer.snooze"
        ):
            match = self._find_timer_snooze(text, requested_at=requested_at)
            if match is not None:
                return self._build_registered_match(
                    intent=intent,
                    command=command,
                    slots=match.slots,
                    requested_at=requested_at,
                )
            return None

        if matcher.get("type") == "builtin_endpoint_control" or command in {
            "playback.stop",
            "playback.repeat",
            "endpoint.volume.set",
            "endpoint.volume.adjust",
            "endpoint.mute",
            "endpoint.unmute",
            "endpoint.identify",
        }:
            match = self._find_endpoint_control(text, command=command, requested_at=requested_at)
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

    def _build_confirmation_match(
        self,
        *,
        intent: dict[str, Any],
        command: str,
        response: str,
        pending_followup: dict[str, Any],
        requested_at: datetime,
    ) -> LocalIntentMatch:
        self._record_match(intent.get("intent_id"), status="matched")
        slots = {
            "response": response,
            "pending_intent_id": str(pending_followup.get("intent_id") or ""),
            "pending_command": str(pending_followup.get("command") or ""),
            "pending_prompt": str(pending_followup.get("prompt") or ""),
            "requested_at": requested_at.isoformat(),
        }
        reply_key = "yes_reply_text" if response == "yes" else "no_reply_text"
        default_reply = "Okay." if response == "yes" else "Okay, cancelled."
        reply_text = str(pending_followup.get(reply_key) or default_reply).strip()
        definition = intent.get("definition") if isinstance(intent.get("definition"), dict) else {}
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
            reply=definition.get("reply") if isinstance(definition.get("reply"), dict) else None,
        )

    def _build_generic_match(self, *, intent: dict[str, Any], command: str, slots: dict[str, Any]) -> LocalIntentMatch:
        definition = intent.get("definition") if isinstance(intent.get("definition"), dict) else {}
        response = definition.get("response") if isinstance(definition.get("response"), dict) else {}
        reply = definition.get("reply") if isinstance(definition.get("reply"), dict) else {}
        conversation_followup = _extract_conversation_followup(definition, slots)
        reply_text = str(reply.get("text") or reply.get("text_template") or response.get("reply_text") or response.get("reply_template") or "").strip()
        if reply_text:
            try:
                reply_text = reply_text.format(**slots)
            except (KeyError, ValueError):
                pass
        if not reply_text and conversation_followup and conversation_followup.get("prompt"):
            reply_text = str(conversation_followup["prompt"]).strip()
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
            conversation_followup=conversation_followup,
        )

    def _record_match(self, intent_id: object, *, status: str, reason: str | None = None) -> None:
        if self._registry is None or not isinstance(intent_id, str):
            return
        self._registry.record_usage(intent_id=intent_id, status=status, reason=reason)

    def _find_timer_create(self, text: str, *, requested_at: datetime | None = None) -> LocalIntentMatch | None:
        if _extract_timer_adjustment(text) is not None:
            return None
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

    def _find_time_query(self, text: str, *, requested_at: datetime | None = None) -> LocalIntentMatch | None:
        if not _is_time_query(text):
            return None
        extraction_time = requested_at or datetime.now(UTC)
        local_time = extraction_time.astimezone()
        time_text = _format_clock_time(local_time)
        return LocalIntentMatch(
            intent="voice.time.query",
            command="voice.time.query",
            slots={
                "time_text": time_text,
                "timezone": local_time.tzname() or "",
                "requested_at": extraction_time.isoformat(),
            },
            reply_text=f"It is {time_text}.",
        )

    def _find_timer_status(self, text: str, *, requested_at: datetime | None = None) -> LocalIntentMatch | None:
        if not _is_timer_status_query(text):
            return None
        extraction_time = requested_at or datetime.now(UTC)
        return LocalIntentMatch(
            intent="timer.status",
            command="timer.status",
            slots={
                "scope": "active_for_endpoint",
                "requested_at": extraction_time.isoformat(),
            },
            reply_text="Checking the timer.",
        )

    def _find_timer_control(self, text: str, *, action: str, requested_at: datetime | None = None) -> LocalIntentMatch | None:
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "stop":
            if not _is_timer_stop_request(text):
                return None
            reply_text = "Stopping the timer."
        elif normalized_action == "cancel":
            if not _is_timer_cancel_request(text):
                return None
            reply_text = "Cancelling the timer."
        else:
            return None
        extraction_time = requested_at or datetime.now(UTC)
        return LocalIntentMatch(
            intent=f"timer.{normalized_action}",
            command=f"timer.{normalized_action}",
            slots={
                "action": normalized_action,
                "scope": "active_for_endpoint",
                "requested_at": extraction_time.isoformat(),
            },
            reply_text=reply_text,
        )

    def _find_timer_adjust_time(self, text: str, *, requested_at: datetime | None = None) -> LocalIntentMatch | None:
        adjustment = _extract_timer_adjustment(text)
        if adjustment is None:
            return None
        direction, duration_text = adjustment
        duration_seconds = _parse_duration_seconds(duration_text)
        if duration_seconds is None:
            return None
        signed_seconds = duration_seconds if direction == "add" else -duration_seconds
        formatted_duration = _format_duration(duration_seconds)
        extraction_time = requested_at or datetime.now(UTC)
        return LocalIntentMatch(
            intent="timer.adjust_time",
            command="timer.adjust_time",
            slots={
                "delta_seconds": signed_seconds,
                "delta_hhmmss": _format_duration_hhmmss(duration_seconds),
                "delta_text": formatted_duration,
                "direction": direction,
                "scope": "active_for_endpoint",
                "requested_at": extraction_time.isoformat(),
            },
            reply_text="Updating the timer.",
        )

    def _find_timer_snooze(self, text: str, *, requested_at: datetime | None = None) -> LocalIntentMatch | None:
        duration_text = _extract_timer_snooze_duration_text(text)
        if duration_text is None:
            return None
        duration_seconds = _parse_duration_seconds(duration_text)
        if duration_seconds is None:
            return None
        formatted_duration = _format_duration(duration_seconds)
        extraction_time = requested_at or datetime.now(UTC)
        return LocalIntentMatch(
            intent="timer.snooze",
            command="timer.snooze",
            slots={
                "duration_seconds": duration_seconds,
                "duration_hhmmss": _format_duration_hhmmss(duration_seconds),
                "duration_text": formatted_duration,
                "scope": "active_for_endpoint",
                "requested_at": extraction_time.isoformat(),
            },
            reply_text=f"Snoozing timer for {formatted_duration}.",
        )

    def _find_endpoint_control(self, text: str, *, command: str, requested_at: datetime | None = None) -> LocalIntentMatch | None:
        extraction_time = requested_at or datetime.now(UTC)
        slots: dict[str, Any] = {"requested_at": extraction_time.isoformat()}
        reply_text = ""
        if command == "playback.stop":
            if not _is_playback_stop_request(text):
                return None
            reply_text = "Stopping playback."
        elif command == "playback.repeat":
            if not _is_playback_repeat_request(text):
                return None
            reply_text = "Repeating that."
        elif command == "endpoint.volume.set":
            volume = _extract_volume_percent(text)
            if volume is None:
                return None
            slots["volume_percent"] = volume
            reply_text = f"Setting volume to {volume} percent."
        elif command == "endpoint.volume.adjust":
            adjustment = _extract_volume_delta(text)
            if adjustment is None:
                return None
            direction, delta = adjustment
            slots["direction"] = direction
            slots["delta_percent"] = delta
            reply_text = "Adjusting volume."
        elif command == "endpoint.mute":
            if not _is_mute_request(text):
                return None
            reply_text = "Muting."
        elif command == "endpoint.unmute":
            if not _is_unmute_request(text):
                return None
            reply_text = "Unmuting."
        elif command == "endpoint.identify":
            if not _is_identify_request(text):
                return None
            reply_text = "Identifying this endpoint."
        else:
            return None
        return LocalIntentMatch(
            intent=command,
            command=command,
            slots=slots,
            reply_text=reply_text,
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


def _is_confirmation_response(text: str, response: str) -> bool:
    if response == "yes":
        return bool(re.match(r"^(?:yes|yeah|yep|correct|confirm|do\s+it)$", text))
    if response == "no":
        return bool(re.match(r"^(?:no|nope|cancel|do\s+not|don't)$", text))
    return False


_SHORT_INTENT_UTTERANCES = {
    "yes",
    "yeah",
    "yep",
    "correct",
    "confirm",
    "no",
    "nope",
    "cancel",
    "stop",
    "ok",
    "okay",
}


def _is_short_intent_utterance(text: str) -> bool:
    return text in _SHORT_INTENT_UTTERANCES


def _allows_global_short_intent(intent: dict[str, Any], definition: dict[str, Any]) -> bool:
    constraints = intent.get("constraints") if isinstance(intent.get("constraints"), dict) else {}
    metadata = intent.get("metadata") if isinstance(intent.get("metadata"), dict) else {}
    matcher = definition.get("matcher") if isinstance(definition.get("matcher"), dict) else {}
    for container in (constraints, metadata, definition, matcher):
        scope = str(container.get("short_intent_scope") or container.get("short_utterance_scope") or "").strip().lower()
        if scope == "global":
            return True
    return False


def _extract_conversation_followup(definition: dict[str, Any], slots: dict[str, Any]) -> dict[str, Any] | None:
    followup = definition.get("followup")
    conversation = definition.get("conversation") if isinstance(definition.get("conversation"), dict) else {}
    if not isinstance(followup, dict):
        followup = conversation.get("followup")
    if not isinstance(followup, dict):
        return None
    required = bool(followup.get("required", True))
    prompt = _format_optional_template(followup.get("prompt") or followup.get("prompt_template"), slots)
    yes_reply = _format_optional_template(followup.get("yes_reply_text") or followup.get("affirmative_reply_text"), slots)
    no_reply = _format_optional_template(followup.get("no_reply_text") or followup.get("negative_reply_text"), slots)
    ttl_seconds = followup.get("ttl_seconds", 30)
    try:
        ttl_seconds = int(ttl_seconds)
    except (TypeError, ValueError):
        ttl_seconds = 30
    ttl_seconds = max(5, min(ttl_seconds, 300))
    context = followup.get("context") if isinstance(followup.get("context"), dict) else {}
    return {
        "required": required,
        "prompt": prompt,
        "yes_reply_text": yes_reply or "Okay.",
        "no_reply_text": no_reply or "Okay, cancelled.",
        "ttl_seconds": ttl_seconds,
        "context": context,
    }


def _format_optional_template(value: object, slots: dict[str, Any]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    template = value.strip()
    try:
        return template.format(**slots)
    except (KeyError, ValueError):
        return template


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


def _extract_timer_adjustment(text: str) -> tuple[str, str] | None:
    patterns: list[tuple[str, str]] = [
        ("add", r"^(?:please\s+)?extend\s+(?:the\s+)?timer\s+by\s+(?P<duration>.+)$"),
        ("add", r"^(?:please\s+)?add\s+(?P<duration>.+?)(?:\s+(?:to|onto)\s+(?:the\s+)?timer)?$"),
        ("remove", r"^(?:please\s+)?(?:remove|subtract)\s+(?P<duration>.+?)(?:\s+from\s+(?:the\s+)?timer)?$"),
        ("remove", r"^(?:please\s+)?take\s+(?P<duration>.+?)\s+off\s+(?:the\s+)?timer$"),
    ]
    for direction, pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return direction, _trim_duration_tail(match.group("duration"))
    return None


def _extract_timer_snooze_duration_text(text: str) -> str | None:
    patterns = [
        r"^(?:please\s+)?snooze(?:\s+(?:the\s+)?(?:timer|alarm))?(?:\s+for)?\s+(?P<duration>.+)$",
        r"^(?:please\s+)?remind\s+me\s+again\s+in\s+(?P<duration>.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return _trim_duration_tail(match.group("duration"))
    return None


def _trim_duration_tail(text: str) -> str:
    trimmed = re.split(r"\s+(?:called|named|labelled|labeled)\s+", text, maxsplit=1)[0]
    return trimmed.strip(" .!?")


def _is_time_query(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:please\s+)?(?:what\s+is\s+the\s+time|what\s+time\s+is\s+it|tell\s+me\s+the\s+time|current\s+time)$",
            text,
        )
    )


def _is_timer_status_query(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:please\s+)?(?:(?:how\s+much\s+time|how\s+long)\s+(?:is\s+)?left\s+(?:on|for)\s+(?:the\s+)?timer|"
            r"(?:what(?:'s|\s+is)\s+)?(?:the\s+)?timer\s+status|"
            r"(?:time\s+left|remaining\s+time)\s+(?:on|for)\s+(?:the\s+)?timer)$",
            text,
        )
    )


def _is_timer_stop_request(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:please\s+)?(?:(?:stop|dismiss|silence)\s+(?:the\s+)?timer(?:\s+alarm)?|"
            r"turn\s+off\s+(?:the\s+)?timer(?:\s+alarm)?|stop)$",
            text,
        )
    )


def _is_timer_cancel_request(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:please\s+)?(?:cancel|delete|clear)\s+(?:(?:the|my)\s+)?timer$",
            text,
        )
    )


def _is_playback_stop_request(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:please\s+)?(?:(?:stop|silence)\s+(?:playback|talking|audio|sound)|be\s+quiet)$",
            text,
        )
    )


def _is_playback_repeat_request(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:please\s+)?(?:repeat\s+that|say\s+that\s+again|replay\s+(?:that|response))$",
            text,
        )
    )


def _extract_volume_percent(text: str) -> int | None:
    match = re.match(
        r"^(?:please\s+)?(?:set\s+)?(?:your\s+)?volume\s+(?:to\s+)?(?P<volume_text>.+?)(?:\s+percent)?$",
        text,
    )
    if not match:
        return None
    value = _parse_volume_number(match.group("volume_text"))
    if value is None:
        return None
    return max(0, min(100, value))


def _extract_volume_delta(text: str) -> tuple[str, int] | None:
    if re.match(r"^(?:please\s+)?(?:turn\s+it\s+up|turn\s+volume\s+up|volume\s+up)$", text):
        return "up", 10
    if re.match(r"^(?:please\s+)?(?:turn\s+it\s+down|turn\s+volume\s+down|volume\s+down)$", text):
        return "down", -10
    match = re.match(
        r"^(?:please\s+)?(?P<direction>raise|lower|increase|decrease)(?:\s+volume)?(?:\s+by)?(?:\s+(?P<delta_text>.+?))?$",
        text,
    )
    if not match:
        return None
    direction_text = match.group("direction")
    direction = "down" if direction_text in {"lower", "decrease"} else "up"
    delta = _parse_volume_number(match.group("delta_text") or "10") or 10
    return direction, delta if direction == "up" else -delta


def _parse_volume_number(text: str) -> int | None:
    cleaned = re.sub(r"\bpercent\b", "", str(text or "").strip().lower()).strip()
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned)
    return _parse_number_words(cleaned)


def _parse_number_words(text: str) -> int | None:
    total = 0.0
    consumed = False
    for token in re.split(r"\s+", text.strip()):
        if token in {"and", "a"}:
            continue
        value = _NUMBER_WORDS.get(token)
        if value is None:
            return None
        total += value
        consumed = True
    return int(total) if consumed else None


def _is_mute_request(text: str) -> bool:
    return bool(re.match(r"^(?:please\s+)?mute(?:\s+(?:yourself|endpoint|speaker))?$", text))


def _is_unmute_request(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:please\s+)?(?:unmute(?:\s+(?:yourself|endpoint|speaker))?|turn\s+(?:the\s+)?sound\s+back\s+on)$",
            text,
        )
    )


def _is_identify_request(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:please\s+)?(?:identify(?:\s+(?:yourself|this\s+device|endpoint))?|flash\s+(?:the\s+)?(?:device|endpoint))$",
            text,
        )
    )


def _format_clock_time(value: datetime) -> str:
    hour = value.hour % 12 or 12
    minute = value.minute
    period = value.strftime("%p")
    hour_text = _format_clock_number(hour)
    if minute == 0:
        return f"{hour_text} {period}"
    if minute < 10:
        return f"{hour_text} oh {_format_clock_number(minute)} {period}"
    return f"{hour_text} {_format_clock_number(minute)} {period}"


def _format_clock_number(value: int) -> str:
    words = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        7: "seven",
        8: "eight",
        9: "nine",
        10: "ten",
        11: "eleven",
        12: "twelve",
        13: "thirteen",
        14: "fourteen",
        15: "fifteen",
        16: "sixteen",
        17: "seventeen",
        18: "eighteen",
        19: "nineteen",
        20: "twenty",
        30: "thirty",
        40: "forty",
        50: "fifty",
    }
    if value in words:
        return words[value]
    tens = (value // 10) * 10
    ones = value % 10
    if tens in words and ones in words:
        return f"{words[tens]} {words[ones]}"
    return str(value)


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
