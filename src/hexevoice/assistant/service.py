from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
import logging
import re
from collections.abc import Sequence
from typing import Protocol

import httpx

from hexevoice.api.models import AssistantTurnRequest, AssistantTurnResponse
from hexevoice.assistant.intents import LocalIntentFinder
from hexevoice.config.settings import Settings
from hexevoice.domain_events import HexeMqttTimerCreateEventPublisher, TimerCreateEventPublisher, utc_event_timestamp
from hexevoice.runtime.service import NodeRuntimeService


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConversationTurn:
    endpoint_id: str
    session_id: str
    heard_text: str
    reply_text: str


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

    def handle_turn(
        self,
        payload: AssistantTurnRequest,
        *,
        session_id: str,
        context: Sequence[ConversationTurn] = (),
    ) -> AssistantTurnResponse:
        if not self._base_url:
            self._last_error = "missing_ai_node_base_url"
            return self._fallback.handle_turn(payload, session_id=session_id, context=context)

        client = self._http_client or httpx.Client(timeout=self._timeout_s)
        try:
            response = client.post(
                f"{self._base_url}{self._turn_path}",
                json={
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
            heard_text = str(data.get("heard_text") or payload.text).strip()
            device_state = (
                data.get("device_state")
                if data.get("device_state") in {"idle", "listening", "thinking", "speaking"}
                else "speaking"
            )
            self._last_error = None
            return AssistantTurnResponse(
                endpoint_id=str(data.get("endpoint_id") or payload.endpoint_id),
                session_id=str(data.get("session_id") or session_id),
                heard_text=heard_text,
                reply_text=text,
                spoken_text=str(data.get("spoken_text") or text),
                handled_locally=bool(data.get("handled_locally", False)),
                command=data.get("command") if isinstance(data.get("command"), str) else None,
                device_state=device_state,
                provider_id="ai_node",
                model=str(data.get("model")) if data.get("model") else None,
                error=None,
            )
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("AI Node assistant turn failed; using local echo fallback: error=%s", self._last_error)
            return self._fallback.handle_turn(payload, session_id=session_id, context=context)
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
        self._timer_event_publisher = timer_event_publisher or HexeMqttTimerCreateEventPublisher(settings=settings)
        self._context_limit = settings.voice_conversation_context_turns
        self._context_by_endpoint: dict[str, deque[ConversationTurn]] = {}
        self._context_by_session: dict[str, deque[ConversationTurn]] = {}

    def handle_turn(self, payload: AssistantTurnRequest) -> AssistantTurnResponse:
        heard_text = self._strip_wake_words(payload.text)
        session_id = payload.session_id or self._next_session_id(payload.endpoint_id)
        intent = self._intent_finder.find(heard_text)
        if intent is not None:
            requested_at = utc_event_timestamp()
            if intent.command == "timer.create":
                self._publish_timer_create_event(
                    endpoint_id=payload.endpoint_id,
                    session_id=session_id,
                    heard_text=heard_text,
                    slots=intent.slots,
                    requested_at=requested_at,
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
                provider_id="local_pattern",
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
            "context_turn_limit": self._context_limit,
            "endpoint_contexts": {endpoint_id: len(turns) for endpoint_id, turns in self._context_by_endpoint.items()},
            "session_contexts": {session_id: len(turns) for session_id, turns in self._context_by_session.items()},
        }

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
        return wake_words or ["Hexa"]

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
    ) -> None:
        duration_seconds = slots.get("duration_seconds")
        duration_text = slots.get("duration_text")
        if not isinstance(duration_seconds, int) or not isinstance(duration_text, str):
            return
        self._timer_event_publisher.publish_timer_create(
            endpoint_id=endpoint_id,
            session_id=session_id,
            heard_text=heard_text,
            duration_seconds=duration_seconds,
            duration_text=duration_text,
            requested_at=requested_at,
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
