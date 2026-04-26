from __future__ import annotations

import logging
import re
from typing import Protocol

import httpx

from hexevoice.api.models import AssistantTurnRequest, AssistantTurnResponse
from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService


log = logging.getLogger(__name__)


class AssistantAdapter(Protocol):
    def handle_turn(self, payload: AssistantTurnRequest, *, session_id: str) -> AssistantTurnResponse:
        ...

    def status(self) -> dict:
        ...


class LocalEchoAssistantAdapter:
    def handle_turn(self, payload: AssistantTurnRequest, *, session_id: str) -> AssistantTurnResponse:
        heard_text = payload.text.strip()
        heard_for_reply = heard_text or "nothing"
        reply_text = f"I heard {heard_for_reply}, no AI added yet."
        return AssistantTurnResponse(
            endpoint_id=payload.endpoint_id,
            session_id=session_id,
            heard_text=heard_text,
            reply_text=reply_text,
            spoken_text=reply_text,
            handled_locally=False,
            command=None,
            device_state="speaking",
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

    def handle_turn(self, payload: AssistantTurnRequest, *, session_id: str) -> AssistantTurnResponse:
        if not self._base_url:
            self._last_error = "missing_ai_node_base_url"
            return self._fallback.handle_turn(payload, session_id=session_id)

        client = self._http_client or httpx.Client(timeout=self._timeout_s)
        try:
            response = client.post(
                f"{self._base_url}{self._turn_path}",
                json={
                    "endpoint_id": payload.endpoint_id,
                    "session_id": session_id,
                    "text": payload.text,
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
            )
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("AI Node assistant turn failed; using local echo fallback: error=%s", self._last_error)
            return self._fallback.handle_turn(payload, session_id=session_id)
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
    ) -> None:
        self._settings = settings
        self._runtime_service = runtime_service
        self._session_counter = 0
        self._adapter = adapter or self._build_adapter()

    def handle_turn(self, payload: AssistantTurnRequest) -> AssistantTurnResponse:
        heard_text = self._strip_wake_words(payload.text)
        session_id = payload.session_id or self._next_session_id(payload.endpoint_id)
        return self._adapter.handle_turn(
            AssistantTurnRequest(
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                text=heard_text or " ",
            ),
            session_id=session_id,
        )

    def status(self) -> dict:
        return self._adapter.status()

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
