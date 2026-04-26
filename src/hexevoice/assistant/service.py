from __future__ import annotations

import re

from hexevoice.api.models import AssistantTurnRequest, AssistantTurnResponse
from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService


class AssistantTurnService:
    def __init__(self, *, settings: Settings, runtime_service: NodeRuntimeService) -> None:
        self._settings = settings
        self._runtime_service = runtime_service
        self._session_counter = 0

    def handle_turn(self, payload: AssistantTurnRequest) -> AssistantTurnResponse:
        heard_text = self._strip_wake_words(payload.text)
        session_id = payload.session_id or self._next_session_id(payload.endpoint_id)

        reply_text, handled_locally, device_state = self._build_reply(heard_text=heard_text)

        return AssistantTurnResponse(
            endpoint_id=payload.endpoint_id,
            session_id=session_id,
            heard_text=heard_text,
            reply_text=reply_text,
            spoken_text=reply_text,
            handled_locally=handled_locally,
            command=None,
            device_state=device_state,
        )

    def _next_session_id(self, endpoint_id: str) -> str:
        self._session_counter += 1
        return f"{endpoint_id}-session-{self._session_counter:04d}"

    def _build_reply(self, *, heard_text: str) -> tuple[str, bool, str]:
        heard_for_reply = heard_text or "nothing"
        reply = f"I heard {heard_for_reply}, no AI added yet."
        return reply, False, "speaking"

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
