from __future__ import annotations

from collections import deque
from dataclasses import dataclass
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
    HexeMqttTimerCreateEventPublisher,
    TimerCreateEventPublisher,
    utc_event_timestamp,
)
from hexevoice.runtime.service import NodeRuntimeService


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
        return wake_words or ["Hexe"]

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

    def _dispatch_intent(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        intent,
        requested_at: datetime,
    ):
        if intent.command == "timer.create":
            return self._publish_timer_create_event(
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                slots=intent.slots,
                requested_at=requested_at,
            )
        return None

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
