from __future__ import annotations

from collections.abc import Iterable

from hexevoice.api.models import AssistantTurnRequest, AssistantTurnResponse
from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService


class AssistantTurnService:
    def __init__(self, *, settings: Settings, runtime_service: NodeRuntimeService) -> None:
        self._settings = settings
        self._runtime_service = runtime_service
        self._session_counter = 0
        self._last_reply_by_endpoint: dict[str, str] = {}

    def handle_turn(self, payload: AssistantTurnRequest) -> AssistantTurnResponse:
        heard_text = payload.text.strip()
        normalized = heard_text.lower()
        session_id = payload.session_id or self._next_session_id(payload.endpoint_id)

        command = self._match_command(normalized)
        reply_text, handled_locally, device_state = self._build_reply(
            endpoint_id=payload.endpoint_id,
            heard_text=heard_text,
            normalized_text=normalized,
            command=command,
        )

        if command != "stop":
            self._last_reply_by_endpoint[payload.endpoint_id] = reply_text

        return AssistantTurnResponse(
            endpoint_id=payload.endpoint_id,
            session_id=session_id,
            heard_text=heard_text,
            reply_text=reply_text,
            spoken_text=reply_text,
            handled_locally=handled_locally,
            command=command,
            device_state=device_state,
        )

    def _next_session_id(self, endpoint_id: str) -> str:
        self._session_counter += 1
        return f"{endpoint_id}-session-{self._session_counter:04d}"

    def _match_command(self, normalized_text: str) -> str | None:
        commands: dict[str, Iterable[str]] = {
            "status": ("status", "what is your status", "are you ready"),
            "repeat": ("repeat", "say that again", "repeat that"),
            "stop": ("stop", "cancel", "never mind"),
        }
        for command, phrases in commands.items():
            if normalized_text in phrases:
                return command
        return None

    def _build_reply(
        self,
        *,
        endpoint_id: str,
        heard_text: str,
        normalized_text: str,
        command: str | None,
    ) -> tuple[str, bool, str]:
        if command == "status":
            status = self._runtime_service.status_payload()
            readiness = "ready" if status.operational_ready else "not ready"
            reply = (
                f"{self._settings.node_name} is {readiness}. "
                f"Current step is {status.current_step_label.lower()} and trust is {status.trust_state}."
            )
            return reply, True, "speaking"

        if command == "repeat":
            reply = self._last_reply_by_endpoint.get(
                endpoint_id,
                "I do not have anything to repeat yet.",
            )
            return reply, True, "speaking"

        if command == "stop":
            return "Stopping the current interaction.", True, "idle"

        if "hello" in normalized_text or "hi " in f"{normalized_text} ":
            reply = f"Hello from {self._settings.node_name}. I can report status, repeat, or stop."
            return reply, False, "speaking"

        reply = (
            f'I heard "{heard_text}". '
            "The full voice pipeline is not wired up yet, but the backend is responding."
        )
        return reply, False, "speaking"
