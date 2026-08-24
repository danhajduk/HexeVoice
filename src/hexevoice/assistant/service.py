from __future__ import annotations

from collections import deque
import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
import re
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol
from uuid import uuid4

import httpx

from hexevoice.api.models import AssistantTurnRequest, AssistantTurnResponse
from hexevoice.assistant.intents import LocalIntentFinder
from hexevoice.config.settings import Settings
from hexevoice.domain_events import (
    AsyncDomainEventPublisher,
    DomainEventPublishDecision,
    HexeMqttTimerCreateEventPublisher,
    TimerCreateEventPublisher,
    utc_event_timestamp,
)
from hexevoice.runtime.service import NodeRuntimeService
from hexevoice.timer_announcements import TimerOwnershipCache


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConversationTurn:
    endpoint_id: str
    session_id: str
    heard_text: str
    reply_text: str


@dataclass(frozen=True)
class PendingConversationFollowup:
    endpoint_id: str
    session_id: str
    intent_id: str
    command: str
    prompt: str
    yes_reply_text: str
    no_reply_text: str
    context: dict[str, Any]
    created_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "session_id": self.session_id,
            "intent_id": self.intent_id,
            "command": self.command,
            "prompt": self.prompt,
            "yes_reply_text": self.yes_reply_text,
            "no_reply_text": self.no_reply_text,
            "context": dict(self.context),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True)
class IntentInvocationResult:
    matched: bool
    endpoint_id: str
    session_id: str
    heard_text: str
    intent_id: str | None = None
    command: str | None = None
    slots: dict[str, Any] | None = None
    reply_text: str | None = None
    provider_id: str | None = None
    recognized_event_id: str | None = None
    recognition_event: dict[str, Any] | None = None
    dispatch_event: dict[str, Any] | None = None
    reply: dict[str, Any] | None = None
    reply_audio: dict[str, Any] | None = None
    conversation_followup: dict[str, Any] | None = None
    latency_ms: float | None = None


class EndpointCommandDispatcher(Protocol):
    def dispatch_endpoint_command(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        command: str,
        slots: dict[str, Any],
    ) -> DomainEventPublishDecision:
        ...


class QueuedEndpointCommandDispatcher:
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def dispatch_endpoint_command(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        command: str,
        slots: dict[str, Any],
    ) -> DomainEventPublishDecision:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return DomainEventPublishDecision(status="skipped", reason="endpoint_command_loop_unavailable", event_type=command)
        event_id = f"voice-endpoint-command-{uuid4().hex}"
        loop.create_task(self._run(endpoint_id=endpoint_id, session_id=session_id, command=command, slots=slots, event_id=event_id))
        return DomainEventPublishDecision(
            status="queued",
            reason="endpoint_command_queued",
            event_id=event_id,
            event_type=command,
            published_at=utc_event_timestamp().isoformat(),
        )

    async def _run(self, *, endpoint_id: str, session_id: str, command: str, slots: dict[str, Any], event_id: str) -> None:
        try:
            if command == "playback.stop":
                result = await self._manager.push_playback_stop_command(endpoint_id=endpoint_id, reason="voice_intent")
            elif command == "playback.repeat":
                result = await self._manager.push_replay_command(endpoint_id=endpoint_id)
            elif command == "endpoint.mute":
                result = await self._manager.push_mute_command(endpoint_id=endpoint_id, muted=True)
            elif command == "endpoint.unmute":
                result = await self._manager.push_mute_command(endpoint_id=endpoint_id, muted=False)
            elif command == "endpoint.volume.set":
                result = await self._manager.push_volume_command(endpoint_id=endpoint_id, volume_percent=int(slots["volume_percent"]))
            elif command == "endpoint.volume.adjust":
                current = self._manager.volume_status(endpoint_id).get("volume_percent")
                current_volume = int(current) if isinstance(current, int) else 70
                next_volume = max(0, min(100, current_volume + int(slots["delta_percent"])))
                result = await self._manager.push_volume_command(endpoint_id=endpoint_id, volume_percent=next_volume)
            elif command == "endpoint.identify":
                result = await self._manager.push_led_simulation_command(
                    endpoint_id=endpoint_id,
                    pattern="identify",
                    duration_ms=3000,
                )
            else:
                result = {"accepted": False, "reason": "unsupported_endpoint_intent", "status": "failed"}
            log.info(
                "Endpoint voice intent command dispatched: endpoint_id=%s session_id=%s command=%s event_id=%s accepted=%s status=%s reason=%s",
                endpoint_id,
                session_id,
                command,
                event_id,
                result.get("accepted"),
                result.get("status"),
                result.get("reason"),
            )
        except Exception:
            log.warning(
                "Endpoint voice intent command failed: endpoint_id=%s session_id=%s command=%s event_id=%s",
                endpoint_id,
                session_id,
                command,
                event_id,
                exc_info=True,
            )


class AssistantAdapter(Protocol):
    def handle_turn(
        self,
        payload: AssistantTurnRequest,
        *,
        session_id: str,
        context: Sequence[ConversationTurn] = (),
    ) -> AssistantTurnResponse:
        ...

    def status(self) -> dict:
        ...


class LocalEchoAssistantAdapter:
    def handle_turn(
        self,
        payload: AssistantTurnRequest,
        *,
        session_id: str,
        context: Sequence[ConversationTurn] = (),
    ) -> AssistantTurnResponse:
        heard_text = payload.text.strip()
        heard_for_reply = heard_text or "nothing"
        reply_text = f"I heard {heard_for_reply}"
        return AssistantTurnResponse(
            endpoint_id=payload.endpoint_id,
            session_id=session_id,
            heard_text=heard_text,
            reply_text=reply_text,
            spoken_text=reply_text,
            handled_locally=False,
            command=None,
            device_state="speaking",
            provider_id="local_echo",
        )

    def status(self) -> dict:
        return {"provider": "local_echo", "healthy": True, "configured": True}


class AiNodeAssistantAdapter:
    def __init__(
        self,
        *,
        base_url: str | None,
        turn_path: str,
        timeout_s: float,
        fallback: AssistantAdapter,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._turn_path = turn_path if turn_path.startswith("/") else f"/{turn_path}"
        self._timeout_s = timeout_s
        self._fallback = fallback
        self._http_client = http_client
        self._last_error: str | None = None
        self._last_error_code: str | None = None
        self._last_latency_ms: float | None = None

    def _fallback_response(
        self,
        payload: AssistantTurnRequest,
        *,
        session_id: str,
        context: Sequence[ConversationTurn],
        reason: str,
        detail: str | None = None,
    ) -> AssistantTurnResponse:
        self._last_error_code = reason
        self._last_error = detail or reason
        fallback = self._fallback.handle_turn(payload, session_id=session_id, context=context)
        return fallback.model_copy(
            update={
                "fallback_used": True,
                "fallback_reason": reason,
                "error": reason,
                "provider_metadata": {
                    "primary_provider": "ai_node",
                    "fallback_provider": fallback.provider_id,
                    "error": {"code": reason, "message": self._last_error},
                },
            }
        )

    def _metadata_from_response(self, data: dict[str, Any]) -> dict[str, Any] | None:
        metadata: dict[str, Any] = {}
        raw_metadata = data.get("provider_metadata") or data.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata.update(raw_metadata)
        for key in ("provider_id", "provider", "model", "model_provider", "model_id", "request_id"):
            value = data.get(key)
            if value not in (None, ""):
                metadata[key] = value
        metadata["ai_node"] = {
            "turn_path": self._turn_path,
            "contract_version": "voice.ai_node.turn.v1",
        }
        return metadata or None

    def _error_code(self, exc: Exception) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "ai_node_timeout"
        if isinstance(exc, httpx.HTTPStatusError):
            return "ai_node_http_error"
        if isinstance(exc, ValueError):
            return "ai_node_invalid_response"
        if isinstance(exc, httpx.HTTPError):
            return "ai_node_request_failed"
        return "ai_node_error"

    def handle_turn(
        self,
        payload: AssistantTurnRequest,
        *,
        session_id: str,
        context: Sequence[ConversationTurn] = (),
    ) -> AssistantTurnResponse:
        if not self._base_url:
            return self._fallback_response(
                payload,
                session_id=session_id,
                context=context,
                reason="missing_ai_node_base_url",
            )

        client = self._http_client or httpx.Client(timeout=self._timeout_s)
        started_at = time.perf_counter()
        try:
            response = client.post(
                f"{self._base_url}{self._turn_path}",
                json={
                    "contract_version": "voice.ai_node.turn.v1",
                    "source_node_type": "voice-node",
                    "endpoint_id": payload.endpoint_id,
                    "session_id": session_id,
                    "text": payload.text,
                    "speaker_identity": payload.speaker_identity,
                    "speaker_identity_policy": payload.speaker_identity_policy,
                    "speaker_personalization_enabled": payload.speaker_personalization_enabled,
                    "context": [
                        {
                            "endpoint_id": turn.endpoint_id,
                            "session_id": turn.session_id,
                            "heard_text": turn.heard_text,
                            "reply_text": turn.reply_text,
                        }
                        for turn in context
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
            text = str(data.get("reply_text") or data.get("spoken_text") or data.get("text") or "").strip()
            if not text:
                raise ValueError("empty_ai_node_reply")
            provider_latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            heard_text = str(data.get("heard_text") or payload.text).strip()
            device_state = (
                data.get("device_state")
                if data.get("device_state") in {"idle", "listening", "thinking", "speaking"}
                else "speaking"
            )
            self._last_error = None
            self._last_error_code = None
            self._last_latency_ms = provider_latency_ms
            provider_id = str(data.get("provider_id") or data.get("provider") or "ai_node")
            return AssistantTurnResponse(
                endpoint_id=str(data.get("endpoint_id") or payload.endpoint_id),
                session_id=str(data.get("session_id") or session_id),
                heard_text=heard_text,
                reply_text=text,
                spoken_text=str(data.get("spoken_text") or text),
                handled_locally=bool(data.get("handled_locally", False)),
                command=data.get("command") if isinstance(data.get("command"), str) else None,
                device_state=device_state,
                provider_id=provider_id,
                model=str(data.get("model")) if data.get("model") else None,
                error=None,
                provider_latency_ms=provider_latency_ms,
                provider_metadata=self._metadata_from_response(data),
            )
        except Exception as exc:
            error_code = self._error_code(exc)
            self._last_latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            log.warning("AI Node assistant turn failed; using local echo fallback: code=%s error=%s", error_code, exc)
            return self._fallback_response(
                payload,
                session_id=session_id,
                context=context,
                reason=error_code,
                detail=str(exc),
            )
        finally:
            if self._http_client is None:
                client.close()

    def status(self) -> dict:
        return {
            "provider": "ai_node",
            "healthy": self._last_error is None,
            "configured": bool(self._base_url),
            "base_url": self._base_url,
            "turn_path": self._turn_path,
            "last_error": self._last_error,
            "last_error_code": self._last_error_code,
            "last_latency_ms": self._last_latency_ms,
            "contract_version": "voice.ai_node.turn.v1",
            "fallback": self._fallback.status(),
        }


class AssistantTurnService:
    def __init__(
        self,
        *,
        settings: Settings,
        runtime_service: NodeRuntimeService,
        adapter: AssistantAdapter | None = None,
        intent_finder: LocalIntentFinder | None = None,
        timer_event_publisher: TimerCreateEventPublisher | None = None,
        timer_ownership_cache: TimerOwnershipCache | None = None,
        endpoint_command_dispatcher: EndpointCommandDispatcher | None = None,
    ) -> None:
        self._settings = settings
        self._runtime_service = runtime_service
        self._session_counter = 0
        self._adapter = adapter or self._build_adapter()
        self._intent_finder = intent_finder or LocalIntentFinder()
        self._timer_event_publisher = timer_event_publisher or AsyncDomainEventPublisher(
            HexeMqttTimerCreateEventPublisher(settings=settings)
        )
        self._context_limit = settings.voice_conversation_context_turns
        self._context_by_endpoint: dict[str, deque[ConversationTurn]] = {}
        self._context_by_session: dict[str, deque[ConversationTurn]] = {}
        self._pending_followups_by_endpoint: dict[str, PendingConversationFollowup] = {}
        self._pending_followups_by_session: dict[str, PendingConversationFollowup] = {}
        self._last_intent_latency: dict[str, Any] | None = None
        self._timer_ownership_cache = timer_ownership_cache
        self._endpoint_command_dispatcher = endpoint_command_dispatcher

    def set_endpoint_command_dispatcher(self, dispatcher: EndpointCommandDispatcher | None) -> None:
        self._endpoint_command_dispatcher = dispatcher

    def handle_turn(self, payload: AssistantTurnRequest) -> AssistantTurnResponse:
        heard_text = self._strip_wake_words(payload.text)
        session_id = payload.session_id or self._next_session_id(payload.endpoint_id)
        requested_at = utc_event_timestamp()
        intent_started_at = time.perf_counter()
        pending_followup = self._pending_followup(endpoint_id=payload.endpoint_id, session_id=session_id, now=requested_at)
        intent = self._intent_finder.find(
            heard_text,
            requested_at=requested_at,
            pending_followup=pending_followup.as_dict() if pending_followup else None,
        )
        if intent is not None:
            intent = self._resolve_timer_context(intent, endpoint_id=payload.endpoint_id)
            self._publish_intent_recognized_event(
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                intent=intent,
                requested_at=requested_at,
                intent_latency_ms=self._elapsed_ms(intent_started_at),
            )
            self._dispatch_intent(
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                intent=intent,
                requested_at=requested_at,
            )
            intent_latency_ms = self._elapsed_ms(intent_started_at)
            conversation_followup = self._apply_followup_transition(
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                intent=intent,
                now=requested_at,
            )
            response = AssistantTurnResponse(
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                reply_text=intent.reply_text,
                spoken_text=intent.reply_text,
                handled_locally=True,
                command=intent.command,
                device_state="speaking",
                provider_id=intent.provider_id,
                intent_latency_ms=intent_latency_ms,
                conversation_followup=conversation_followup,
            )
            self._record_intent_latency(
                matched=True,
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                intent_id=intent.intent,
                command=intent.command,
                provider_id=intent.provider_id,
                latency_ms=intent_latency_ms,
            )
            self._record_turn(response)
            return response

        context = self._conversation_context(endpoint_id=payload.endpoint_id, session_id=session_id)
        response = self._adapter.handle_turn(
            AssistantTurnRequest(
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                text=heard_text or " ",
            ),
            session_id=session_id,
            context=context,
        )
        self._record_turn(response)
        return response

    def status(self) -> dict:
        return {
            **self._adapter.status(),
            "local_intents": self._intent_finder.status(),
            "domain_events": self._timer_event_publisher.status(),
            "timer_ownership": self._timer_ownership_cache.status() if self._timer_ownership_cache else None,
            "last_intent_latency": self._last_intent_latency,
            "context_turn_limit": self._context_limit,
            "endpoint_contexts": {endpoint_id: len(turns) for endpoint_id, turns in self._context_by_endpoint.items()},
            "session_contexts": {session_id: len(turns) for session_id, turns in self._context_by_session.items()},
            "pending_followups": {
                endpoint_id: followup.as_dict()
                for endpoint_id, followup in self._pending_followups_by_endpoint.items()
                if not followup.is_expired(utc_event_timestamp())
            },
        }

    def match_intent(self, text: str, *, endpoint_id: str = "intent-test", session_id: str | None = None):
        requested_at = utc_event_timestamp()
        pending_followup = self._pending_followup(endpoint_id=endpoint_id, session_id=session_id, now=requested_at)
        return self._intent_finder.find(
            self._strip_wake_words(text),
            requested_at=requested_at,
            pending_followup=pending_followup.as_dict() if pending_followup else None,
        )

    def invoke_intent(
        self,
        *,
        endpoint_id: str,
        text: str,
        session_id: str | None = None,
        reply_audio_factory: Callable[..., dict[str, Any] | None] | None = None,
    ) -> IntentInvocationResult:
        heard_text = self._strip_wake_words(text)
        resolved_session_id = session_id or self._next_session_id(endpoint_id)
        requested_at = utc_event_timestamp()
        intent_started_at = time.perf_counter()
        pending_followup = self._pending_followup(endpoint_id=endpoint_id, session_id=resolved_session_id, now=requested_at)
        intent = self._intent_finder.find(
            heard_text,
            requested_at=requested_at,
            pending_followup=pending_followup.as_dict() if pending_followup else None,
        )
        if intent is None:
            intent_latency_ms = self._elapsed_ms(intent_started_at)
            self._record_intent_latency(
                matched=False,
                endpoint_id=endpoint_id,
                session_id=resolved_session_id,
                intent_id=None,
                command=None,
                provider_id=None,
                latency_ms=intent_latency_ms,
            )
            return IntentInvocationResult(
                matched=False,
                endpoint_id=endpoint_id,
                session_id=resolved_session_id,
                heard_text=heard_text,
                slots={},
                latency_ms=intent_latency_ms,
            )
        recognized_event_id = f"voice-intent-{uuid4().hex}"
        intent = self._resolve_timer_context(intent, endpoint_id=endpoint_id)
        reply_audio = self._synthesize_intent_reply_audio(
            endpoint_id=endpoint_id,
            session_id=resolved_session_id,
            intent=intent,
            event_id=recognized_event_id,
            heard_text=heard_text,
            reply_audio_factory=reply_audio_factory,
        )
        recognition_decision = self._publish_intent_recognized_event(
            endpoint_id=endpoint_id,
            session_id=resolved_session_id,
            heard_text=heard_text,
            intent=intent,
            requested_at=requested_at,
            event_id=recognized_event_id,
            reply_audio=reply_audio,
            intent_latency_ms=self._elapsed_ms(intent_started_at),
        )
        dispatch_decision = self._dispatch_intent(
            endpoint_id=endpoint_id,
            session_id=resolved_session_id,
            heard_text=heard_text,
            intent=intent,
            requested_at=requested_at,
        )
        intent_latency_ms = self._elapsed_ms(intent_started_at)
        conversation_followup = self._apply_followup_transition(
            endpoint_id=endpoint_id,
            session_id=resolved_session_id,
            intent=intent,
            now=requested_at,
        )
        response = AssistantTurnResponse(
            endpoint_id=endpoint_id,
            session_id=resolved_session_id,
            heard_text=heard_text,
            reply_text=intent.reply_text,
            spoken_text=intent.reply_text,
            handled_locally=True,
            command=intent.command,
            device_state="speaking",
            provider_id=intent.provider_id,
            intent_latency_ms=intent_latency_ms,
            conversation_followup=conversation_followup,
        )
        self._record_intent_latency(
            matched=True,
            endpoint_id=endpoint_id,
            session_id=resolved_session_id,
            intent_id=intent.intent,
            command=intent.command,
            provider_id=intent.provider_id,
            latency_ms=intent_latency_ms,
        )
        self._record_turn(response)
        return IntentInvocationResult(
            matched=True,
            endpoint_id=endpoint_id,
            session_id=resolved_session_id,
            heard_text=heard_text,
            intent_id=intent.intent,
            command=intent.command,
            slots=dict(intent.slots),
            reply_text=intent.reply_text,
            provider_id=intent.provider_id,
            recognized_event_id=recognized_event_id,
            recognition_event=recognition_decision.as_dict(),
            dispatch_event=dispatch_decision.as_dict() if dispatch_decision else None,
            reply=intent.reply,
            reply_audio=reply_audio,
            conversation_followup=conversation_followup,
            latency_ms=intent_latency_ms,
        )

    def context_for_endpoint(self, endpoint_id: str) -> list[ConversationTurn]:
        return list(self._context_by_endpoint.get(endpoint_id, ()))

    def context_for_session(self, session_id: str) -> list[ConversationTurn]:
        return list(self._context_by_session.get(session_id, ()))

    def _next_session_id(self, endpoint_id: str) -> str:
        self._session_counter += 1
        return f"{endpoint_id}-session-{self._session_counter:04d}"

    def _strip_wake_words(self, text: str) -> str:
        cleaned = text.strip()
        wake_words = self._wake_words()
        for wake_word in wake_words:
            cleaned = re.sub(
                rf"^\s*{re.escape(wake_word)}\b[\s,.:;!?-]*",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()
        return cleaned

    def _wake_words(self) -> list[str]:
        configured = self._settings.voice_wake_models or ""
        wake_words = [item.strip() for item in configured.split(",") if item.strip()]
        aliases = ["Hexe", "Hexa"]
        normalized: list[str] = []
        seen: set[str] = set()
        for wake_word in [*wake_words, *aliases]:
            key = wake_word.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(wake_word)
        return normalized

    def _build_adapter(self) -> AssistantAdapter:
        fallback = LocalEchoAssistantAdapter()
        if self._settings.voice_assistant_provider == "ai_node":
            return AiNodeAssistantAdapter(
                base_url=self._settings.voice_assistant_ai_node_base_url,
                turn_path=self._settings.voice_assistant_ai_node_turn_path,
                timeout_s=self._settings.voice_assistant_timeout_s,
                fallback=fallback,
            )
        return fallback

    def _publish_timer_create_event(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        slots: dict,
        requested_at: datetime,
    ):
        duration_seconds = slots.get("duration_seconds")
        duration_text = slots.get("duration_text")
        if not isinstance(duration_seconds, int) or not isinstance(duration_text, str):
            return None
        return self._timer_event_publisher.publish_timer_create(
            endpoint_id=endpoint_id,
            session_id=session_id,
            heard_text=heard_text,
            duration_seconds=duration_seconds,
            duration_text=duration_text,
            requested_at=requested_at,
        )

    def _publish_timer_status_request_event(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        slots: dict,
        requested_at: datetime,
    ):
        scope = str(slots.get("scope") or "active_for_endpoint").strip() or "active_for_endpoint"
        timer_id = str(slots.get("timer_id") or "").strip() or None
        return self._timer_event_publisher.publish_timer_status_request(
            endpoint_id=endpoint_id,
            session_id=session_id,
            heard_text=heard_text,
            requested_at=requested_at,
            scope=scope,
            timer_id=timer_id,
        )

    def _publish_timer_control_request_event(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        slots: dict,
        requested_at: datetime,
    ):
        action = str(slots.get("action") or "").strip().lower()
        scope = str(slots.get("scope") or "active_for_endpoint").strip() or "active_for_endpoint"
        timer_id = str(slots.get("timer_id") or "").strip() or None
        if action not in {"stop", "cancel"}:
            return None
        return self._timer_event_publisher.publish_timer_control_request(
            action=action,
            endpoint_id=endpoint_id,
            session_id=session_id,
            heard_text=heard_text,
            requested_at=requested_at,
            scope=scope,
            timer_id=timer_id,
        )

    def _publish_timer_adjust_request_event(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        slots: dict,
        requested_at: datetime,
    ):
        delta_seconds = slots.get("delta_seconds")
        delta_text = slots.get("delta_text")
        if not isinstance(delta_seconds, int) or not isinstance(delta_text, str):
            return None
        scope = str(slots.get("scope") or "active_for_endpoint").strip() or "active_for_endpoint"
        timer_id = str(slots.get("timer_id") or "").strip() or None
        return self._timer_event_publisher.publish_timer_adjust_request(
            endpoint_id=endpoint_id,
            session_id=session_id,
            heard_text=heard_text,
            delta_seconds=delta_seconds,
            delta_text=delta_text,
            requested_at=requested_at,
            scope=scope,
            timer_id=timer_id,
        )

    def _publish_timer_snooze_request_event(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        slots: dict,
        requested_at: datetime,
    ):
        duration_seconds = slots.get("duration_seconds")
        duration_text = slots.get("duration_text")
        if not isinstance(duration_seconds, int) or not isinstance(duration_text, str):
            return None
        scope = str(slots.get("scope") or "active_for_endpoint").strip() or "active_for_endpoint"
        timer_id = str(slots.get("timer_id") or "").strip() or None
        return self._timer_event_publisher.publish_timer_snooze_request(
            endpoint_id=endpoint_id,
            session_id=session_id,
            heard_text=heard_text,
            duration_seconds=duration_seconds,
            duration_text=duration_text,
            requested_at=requested_at,
            scope=scope,
            timer_id=timer_id,
        )

    def _dispatch_intent(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        intent,
        requested_at: datetime,
    ):
        if self._timer_selection_blocks_dispatch(intent):
            return DomainEventPublishDecision(
                status="skipped",
                reason="ambiguous_timer_selection",
                event_type=f"{intent.command}_requested",
            )
        if intent.command == "timer.create":
            return self._publish_timer_create_event(
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                slots=intent.slots,
                requested_at=requested_at,
            )
        if intent.command == "timer.status":
            return self._publish_timer_status_request_event(
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                slots=intent.slots,
                requested_at=requested_at,
            )
        if intent.command in {"timer.stop", "timer.cancel"}:
            return self._publish_timer_control_request_event(
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                slots=intent.slots,
                requested_at=requested_at,
            )
        if intent.command == "timer.adjust_time":
            return self._publish_timer_adjust_request_event(
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                slots=intent.slots,
                requested_at=requested_at,
            )
        if intent.command == "timer.snooze":
            return self._publish_timer_snooze_request_event(
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                slots=intent.slots,
                requested_at=requested_at,
            )
        if intent.command in {
            "playback.stop",
            "playback.repeat",
            "endpoint.volume.set",
            "endpoint.volume.adjust",
            "endpoint.mute",
            "endpoint.unmute",
            "endpoint.identify",
        }:
            if self._endpoint_command_dispatcher is None:
                return DomainEventPublishDecision(status="skipped", reason="endpoint_dispatcher_unavailable", event_type=intent.command)
            return self._endpoint_command_dispatcher.dispatch_endpoint_command(
                endpoint_id=endpoint_id,
                session_id=session_id,
                command=intent.command,
                slots=intent.slots,
            )
        return None

    def _resolve_timer_context(self, intent, *, endpoint_id: str):
        if intent.command not in {"timer.status", "timer.stop", "timer.cancel", "timer.adjust_time", "timer.snooze"}:
            return intent
        if self._timer_ownership_cache is None:
            return intent
        selection = self._timer_ownership_cache.select_timer(endpoint_id)
        slots = dict(intent.slots)
        slots["timer_selection_status"] = selection.get("status")
        if selection.get("status") == "selected":
            timer = selection.get("timer") if isinstance(selection.get("timer"), dict) else {}
            timer_id = str(timer.get("timer_id") or "").strip()
            if timer_id:
                slots["timer_id"] = timer_id
            for source_key, slot_key in (
                ("owner_node_id", "timer_owner_node_id"),
                ("title", "timer_title"),
                ("due_at", "timer_due_at"),
            ):
                if timer.get(source_key):
                    slots[slot_key] = timer.get(source_key)
            slots["timer_selection_strategy"] = selection.get("strategy")
            return replace(intent, slots=slots)
        if selection.get("status") == "ambiguous":
            candidates = selection.get("candidates") if isinstance(selection.get("candidates"), list) else []
            slots["timer_candidates"] = candidates
            slots["timer_candidate_count"] = len(candidates)
            return replace(intent, slots=slots, reply_text=_timer_ambiguity_reply(candidates))
        return replace(intent, slots=slots)

    def _timer_selection_blocks_dispatch(self, intent) -> bool:
        return intent.slots.get("timer_selection_status") == "ambiguous"

    def _pending_followup(
        self,
        *,
        endpoint_id: str,
        session_id: str | None,
        now: datetime,
    ) -> PendingConversationFollowup | None:
        followup = self._pending_followups_by_endpoint.get(endpoint_id)
        if followup is None and session_id:
            followup = self._pending_followups_by_session.get(session_id)
        if followup is None:
            return None
        if followup.is_expired(now):
            self._clear_pending_followup(followup)
            return None
        return followup

    def _apply_followup_transition(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        intent,
        now: datetime,
    ) -> dict[str, Any] | None:
        if intent.command in {"voice.confirm.yes", "voice.confirm.no"}:
            pending = self._pending_followup(endpoint_id=endpoint_id, session_id=session_id, now=now)
            if pending is not None:
                self._clear_pending_followup(pending)
            return None
        if intent.conversation_followup:
            followup = self._store_pending_followup(
                endpoint_id=endpoint_id,
                session_id=session_id,
                intent_id=intent.intent,
                command=intent.command,
                followup=intent.conversation_followup,
                now=now,
            )
            return followup.as_dict()
        existing = self._pending_followup(endpoint_id=endpoint_id, session_id=session_id, now=now)
        if existing is not None:
            self._clear_pending_followup(existing)
        return None

    def _store_pending_followup(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        intent_id: str,
        command: str,
        followup: dict[str, Any],
        now: datetime,
    ) -> PendingConversationFollowup:
        ttl_seconds = max(5, min(int(followup.get("ttl_seconds") or 30), 300))
        pending = PendingConversationFollowup(
            endpoint_id=endpoint_id,
            session_id=session_id,
            intent_id=intent_id,
            command=command,
            prompt=str(followup.get("prompt") or "").strip(),
            yes_reply_text=str(followup.get("yes_reply_text") or "Okay.").strip(),
            no_reply_text=str(followup.get("no_reply_text") or "Okay, cancelled.").strip(),
            context=followup.get("context") if isinstance(followup.get("context"), dict) else {},
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._pending_followups_by_endpoint[endpoint_id] = pending
        self._pending_followups_by_session[session_id] = pending
        return pending

    def _clear_pending_followup(self, followup: PendingConversationFollowup) -> None:
        self._pending_followups_by_endpoint.pop(followup.endpoint_id, None)
        self._pending_followups_by_session.pop(followup.session_id, None)

    def _publish_intent_recognized_event(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        intent,
        requested_at: datetime,
        event_id: str | None = None,
        reply_audio: dict[str, Any] | None = None,
        intent_latency_ms: float | None = None,
    ):
        recognized_event_id = event_id or f"voice-intent-{uuid4().hex}"
        publisher = getattr(self._timer_event_publisher, "publish_voice_intent_recognized", None)
        if not callable(publisher):
            return None
        return publisher(
            event_id=recognized_event_id,
            endpoint_id=endpoint_id,
            session_id=session_id,
            intent_id=intent.intent,
            intent_name=intent.intent_name,
            service_id=intent.service_id,
            version=intent.version,
            command=intent.command,
            provider_id=intent.provider_id,
            recognized_text=heard_text,
            slots=dict(intent.slots),
            reply_text=intent.reply_text,
            requested_at=requested_at,
            dispatch=intent.dispatch,
            reply_audio=reply_audio,
            intent_latency_ms=intent_latency_ms,
        )

    def _synthesize_intent_reply_audio(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        intent,
        event_id: str,
        heard_text: str,
        reply_audio_factory: Callable[..., dict[str, Any] | None] | None,
    ) -> dict[str, Any] | None:
        if not reply_audio_factory or not intent.reply_text:
            return None
        reply = intent.reply or {}
        audio_options = reply.get("audio") if isinstance(reply.get("audio"), dict) else {}
        mode = str((audio_options or {}).get("mode") or "none").strip().lower()
        if not mode or mode == "none":
            return None
        return reply_audio_factory(
            event_id=event_id,
            endpoint_id=endpoint_id,
            session_id=session_id,
            text=intent.reply_text,
            audio_options=audio_options,
            transcript={"text": heard_text},
        )

    def _conversation_context(self, *, endpoint_id: str, session_id: str) -> list[ConversationTurn]:
        seen: set[tuple[str, str]] = set()
        context: list[ConversationTurn] = []
        for turn in [
            *self._context_by_endpoint.get(endpoint_id, ()),
            *self._context_by_session.get(session_id, ()),
        ]:
            key = (turn.session_id, turn.heard_text)
            if key in seen:
                continue
            seen.add(key)
            context.append(turn)
        return context[-self._context_limit :] if self._context_limit else []

    def _record_turn(self, response: AssistantTurnResponse) -> None:
        if self._context_limit <= 0:
            return
        turn = ConversationTurn(
            endpoint_id=response.endpoint_id,
            session_id=response.session_id,
            heard_text=response.heard_text,
            reply_text=response.reply_text,
        )
        endpoint_context = self._context_by_endpoint.setdefault(
            response.endpoint_id,
            deque(maxlen=self._context_limit),
        )
        session_context = self._context_by_session.setdefault(
            response.session_id,
            deque(maxlen=self._context_limit),
        )
        endpoint_context.append(turn)
        session_context.append(turn)

    @staticmethod
    def _elapsed_ms(started_at: float) -> float:
        return round((time.perf_counter() - started_at) * 1000, 3)

    def _record_intent_latency(
        self,
        *,
        matched: bool,
        endpoint_id: str,
        session_id: str,
        intent_id: str | None,
        command: str | None,
        provider_id: str | None,
        latency_ms: float,
    ) -> None:
        self._last_intent_latency = {
            "matched": matched,
            "endpoint_id": endpoint_id,
            "session_id": session_id,
            "intent_id": intent_id,
            "command": command,
            "provider_id": provider_id,
            "latency_ms": latency_ms,
            "recorded_at": utc_event_timestamp().isoformat(),
        }
        log.info(
            "Intent latency recorded: matched=%s endpoint_id=%s session_id=%s intent_id=%s command=%s latency_ms=%s",
            matched,
            endpoint_id,
            session_id,
            intent_id,
            command,
            latency_ms,
        )


def _timer_ambiguity_reply(candidates: list[dict[str, Any]]) -> str:
    labels: list[str] = []
    for candidate in candidates[:3]:
        title = str(candidate.get("title") or "").strip()
        remaining = str(candidate.get("remaining_text") or "").strip()
        timer_id = str(candidate.get("timer_id") or "").strip()
        label = title or remaining or timer_id
        if label:
            labels.append(label)
    if labels:
        return f"I found multiple active timers: {', '.join(labels)}. Please say which timer."
    return "I found multiple active timers. Please say which timer."
