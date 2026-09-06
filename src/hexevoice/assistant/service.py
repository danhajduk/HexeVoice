from __future__ import annotations

from collections import deque
import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from hexevoice.api.models import AssistantTurnRequest, AssistantTurnResponse
from hexevoice.assistant.intents import LocalIntentFinder
from hexevoice.config.settings import Settings
from hexevoice.core.client import CoreOnboardingClient
from hexevoice.domain_events import (
    AsyncDomainEventPublisher,
    DomainEventPublishDecision,
    HexeMqttTimerCreateEventPublisher,
    TimerCreateEventPublisher,
    utc_event_timestamp,
)
from hexevoice.persistence import OnboardingStateStore
from hexevoice.persistence.voice_admin_maintenance import redact_spoken_passcodes
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
class AiNodeRequestTarget:
    url: str
    service_id: str | None = None
    provider: str | None = None
    model: str | None = None
    resolution_mode: str | None = None
    source: str = "static"


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
    TASK_FAMILY = "task.chat"
    EXECUTION_SERVICE_ID = "hexevoice"

    def __init__(
        self,
        *,
        base_url: str | None,
        turn_path: str,
        timeout_s: float,
        fallback: AssistantAdapter,
        prompt_id: str | None = None,
        prompt_version: str | None = None,
        onboarding_state_store: OnboardingStateStore | None = None,
        core_client: CoreOnboardingClient | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._turn_path = turn_path if turn_path.startswith("/") else f"/{turn_path}"
        self._timeout_s = timeout_s
        self._fallback = fallback
        self._prompt_id = prompt_id.strip() if prompt_id and prompt_id.strip() else None
        self._prompt_version = prompt_version.strip() if prompt_version and prompt_version.strip() else None
        self._onboarding_state_store = onboarding_state_store
        self._core_client = core_client or CoreOnboardingClient()
        self._http_client = http_client
        self._last_error: str | None = None
        self._last_error_code: str | None = None
        self._last_latency_ms: float | None = None
        self._last_resolved_url: str | None = None
        self._last_resolved_service_id: str | None = None
        self._last_resolution_source: str | None = None
        self._last_resolution_error: str | None = None

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

    def _metadata_from_response(self, data: dict[str, Any], target: AiNodeRequestTarget) -> dict[str, Any] | None:
        metadata: dict[str, Any] = {}
        raw_metadata = data.get("provider_metadata") or data.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata.update(raw_metadata)
        for key in (
            "provider_id",
            "provider",
            "provider_used",
            "model",
            "model_provider",
            "model_id",
            "model_used",
            "request_id",
            "task_id",
            "status",
        ):
            value = data.get(key)
            if value not in (None, ""):
                metadata[key] = value
        if isinstance(data.get("metrics"), dict):
            metadata["metrics"] = data["metrics"]
        if isinstance(data.get("resolution_metadata"), dict):
            metadata["resolution_metadata"] = data["resolution_metadata"]
        metadata["ai_node"] = {
            "url": target.url,
            "service_id": target.service_id,
            "provider": target.provider,
            "model": target.model,
            "resolution_mode": target.resolution_mode,
            "resolution_source": target.source,
            "contract_version": self._contract_version_for_url(target.url),
        }
        return metadata or None

    def _static_target(self) -> AiNodeRequestTarget | None:
        if not self._base_url:
            return None
        return AiNodeRequestTarget(url=self._join_url(self._base_url, self._turn_path), source="static")

    def _resolve_target(self, payload: AssistantTurnRequest, *, session_id: str) -> AiNodeRequestTarget | None:
        if not self._onboarding_state_store:
            return self._static_target()
        try:
            state = self._onboarding_state_store.load()
            node_id = state.trust_activation.node_id
            token = state.trust_activation.node_trust_token
            core_base_url = state.pre_trust.core_base_url
            if state.trust_activation.trust_status != "trusted" or not node_id or not token or not core_base_url:
                raise ValueError("core_trust_not_ready")
            resolved = self._core_client.resolve_node_service(
                core_base_url=core_base_url,
                node_trust_token=token,
                payload={
                    "node_id": node_id,
                    "task_family": self.TASK_FAMILY,
                    "type": "ai",
                    "task_context": {
                        "type": "ai",
                        "source": "hexevoice",
                        "endpoint_id": payload.endpoint_id,
                        "session_id": session_id,
                    },
                },
            )
            selected_service_id = str(resolved.get("selected_service_id") or "").strip()
            candidates = list(resolved.get("candidates") or [])
            selected = self._select_candidate(candidates, selected_service_id=selected_service_id)
            if not selected:
                raise ValueError("ai_node_resolve_no_candidate")
            target = self._target_from_candidate(selected)
            if not target:
                raise ValueError("ai_node_resolve_missing_execution_url")
            return target
        except Exception as exc:
            self._last_resolution_error = str(exc)
            log.warning("AI Node Core resolution failed; falling back to configured AI URL when available: %s", exc)
            return self._static_target()

    def _select_candidate(self, candidates: list[Any], *, selected_service_id: str) -> dict[str, Any] | None:
        dict_candidates = [candidate for candidate in candidates if isinstance(candidate, dict)]
        if selected_service_id:
            for candidate in dict_candidates:
                if str(candidate.get("service_id") or "") == selected_service_id:
                    return candidate
        return dict_candidates[0] if dict_candidates else None

    def _target_from_candidate(self, candidate: dict[str, Any]) -> AiNodeRequestTarget | None:
        endpoint = candidate.get("capability_endpoint")
        endpoint_url = None
        if isinstance(endpoint, dict):
            endpoint_url = endpoint.get("url") or endpoint.get("execution_endpoint_url")
        url = str(endpoint_url or candidate.get("execution_endpoint_url") or "").strip()
        if not url:
            base_url = str(candidate.get("provider_api_base_url") or "").strip()
            if not base_url:
                return None
            url = self._join_url(base_url, self._turn_path)
        elif self._url_needs_default_path(url):
            url = self._join_url(url, self._turn_path)
        return AiNodeRequestTarget(
            url=url,
            service_id=str(candidate.get("service_id") or "") or None,
            provider=str(candidate.get("provider") or "") or None,
            model=self._first_model(candidate.get("models_allowed")),
            resolution_mode=str(candidate.get("resolution_mode") or "") or None,
            source="core",
        )

    @staticmethod
    def _first_model(value: Any) -> str | None:
        if isinstance(value, list) and value:
            first = str(value[0] or "").strip()
            return first or None
        return None

    @staticmethod
    def _join_url(base_url: str, path: str) -> str:
        if base_url.rstrip("/").endswith("/api") and path.startswith("/api/"):
            path = path[4:]
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _url_needs_default_path(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.path in {"", "/"}

    @staticmethod
    def _contract_version_for_url(url: str) -> str:
        return "client-ai.execution.v2" if urlparse(url).path.rstrip("/") == "/api/execution/direct" else "voice.ai_node.turn.v1"

    def _request_json(
        self,
        *,
        target: AiNodeRequestTarget,
        payload: AssistantTurnRequest,
        session_id: str,
        context: Sequence[ConversationTurn],
    ) -> dict[str, Any]:
        runtime_context = self._runtime_context()
        node_clock = self._node_clock_context(runtime_context)
        node_clock_instruction = self._node_clock_instruction(node_clock)
        ai_text = self._ai_prompt_text(text=payload.text, node_clock_instruction=node_clock_instruction)
        if self._contract_version_for_url(target.url) == "client-ai.execution.v2":
            task_id = f"hexevoice-{uuid4().hex}"
            return {
                "task_id": task_id,
                "prompt_id": self._prompt_id,
                "prompt_version": self._prompt_version,
                "task_family": self.TASK_FAMILY,
                "requested_by": "hexevoice",
                "service_id": self.EXECUTION_SERVICE_ID,
                "inputs": {
                    "text": ai_text,
                    "user_text": payload.text,
                    "original_text": payload.text,
                    "endpoint_id": payload.endpoint_id,
                    "session_id": session_id,
                    "speaker_identity": payload.speaker_identity,
                    "speaker_identity_policy": payload.speaker_identity_policy,
                    "speaker_personalization_enabled": payload.speaker_personalization_enabled,
                    "runtime_context": runtime_context,
                    "node_clock": node_clock,
                    "system_context": node_clock_instruction,
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
                "constraints": {},
                "response_mode": "sync",
                "timeout_s": max(1, int(self._timeout_s)),
                "trace_id": f"{session_id}-{uuid4().hex[:8]}",
            }
        return {
            "contract_version": "voice.ai_node.turn.v1",
            "source_node_type": "voice-node",
            "endpoint_id": payload.endpoint_id,
            "session_id": session_id,
            "text": ai_text,
            "user_text": payload.text,
            "original_text": payload.text,
            "speaker_identity": payload.speaker_identity,
            "speaker_identity_policy": payload.speaker_identity_policy,
            "speaker_personalization_enabled": payload.speaker_personalization_enabled,
            "runtime_context": runtime_context,
            "node_clock": node_clock,
            "system_context": node_clock_instruction,
            "context": [
                {
                    "endpoint_id": turn.endpoint_id,
                    "session_id": turn.session_id,
                    "heard_text": turn.heard_text,
                    "reply_text": turn.reply_text,
                }
                for turn in context
            ],
        }

    @staticmethod
    def _runtime_context() -> dict[str, Any]:
        local_now = datetime.now().astimezone()
        utc_now = local_now.astimezone(UTC)
        timezone_name = local_now.tzname() or "local"
        return {
            "current_local_datetime": local_now.isoformat(),
            "current_utc_datetime": utc_now.isoformat(),
            "current_date": local_now.date().isoformat(),
            "timezone": timezone_name,
            "timezone_offset": local_now.strftime("%z"),
            "instruction": (
                "Use current_local_datetime as the current date and time for this voice node "
                "when answering date or time questions."
            ),
        }

    @staticmethod
    def _node_clock_context(runtime_context: dict[str, Any]) -> dict[str, Any]:
        local_datetime = str(runtime_context.get("current_local_datetime") or "")
        utc_datetime = str(runtime_context.get("current_utc_datetime") or "")
        timezone_name = str(runtime_context.get("timezone") or "local")
        timezone_offset = str(runtime_context.get("timezone_offset") or "")
        local_now = datetime.fromisoformat(local_datetime) if local_datetime else datetime.now().astimezone()
        return {
            "source": "voice_node_system_clock",
            "authoritative": True,
            "current_local_datetime": local_datetime or local_now.isoformat(),
            "current_utc_datetime": utc_datetime or local_now.astimezone(UTC).isoformat(),
            "current_date": local_now.date().isoformat(),
            "current_time": local_now.strftime("%H:%M:%S"),
            "current_date_text": f"{local_now:%A, %B} {local_now.day}, {local_now.year}",
            "current_time_text": local_now.strftime("%I:%M %p").lstrip("0"),
            "timezone": timezone_name,
            "timezone_offset": timezone_offset,
        }

    @staticmethod
    def _node_clock_instruction(node_clock: dict[str, Any]) -> str:
        return (
            "Authoritative voice node clock: "
            f"{node_clock['current_date_text']} at {node_clock['current_time_text']} "
            f"{node_clock['timezone']} ({node_clock['current_local_datetime']}). "
            "Use this date and time for any current date, current time, today, tomorrow, "
            "yesterday, schedule, reminder, timer, or time-sensitive answer."
        )

    @staticmethod
    def _ai_prompt_text(*, text: str, node_clock_instruction: str) -> str:
        return f"{node_clock_instruction}\n\nActual user request:\n{text}"

    @staticmethod
    def _response_text(data: dict[str, Any]) -> str:
        output = data.get("output")
        if isinstance(output, dict):
            output_text = output.get("reply_text") or output.get("spoken_text") or output.get("text")
            if output_text not in (None, ""):
                return str(output_text).strip()
        return str(data.get("reply_text") or data.get("spoken_text") or data.get("text") or "").strip()

    def _execute_target(
        self,
        *,
        client: httpx.Client,
        target: AiNodeRequestTarget,
        payload: AssistantTurnRequest,
        session_id: str,
        context: Sequence[ConversationTurn],
        started_at: float,
    ) -> AssistantTurnResponse:
        response = client.post(
            target.url,
            json=self._request_json(target=target, payload=payload, session_id=session_id, context=context),
        )
        response.raise_for_status()
        data = response.json()
        if str(data.get("status") or "").lower() in {"failed", "rejected", "error"}:
            raise ValueError(str(data.get("error_message") or data.get("error_code") or "ai_node_execution_failed"))
        text = self._response_text(data)
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
        self._last_resolved_url = target.url
        self._last_resolved_service_id = target.service_id
        self._last_resolution_source = target.source
        self._last_resolution_error = None if target.source == "core" else self._last_resolution_error
        provider_id = str(data.get("provider_id") or data.get("provider") or data.get("provider_used") or "ai_node")
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
            model=str(data.get("model") or data.get("model_used")) if data.get("model") or data.get("model_used") else None,
            error=None,
            provider_latency_ms=provider_latency_ms,
            provider_metadata=self._metadata_from_response(data, target),
        )

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
        target = self._resolve_target(payload, session_id=session_id)
        if not target:
            return self._fallback_response(
                payload,
                session_id=session_id,
                context=context,
                reason="missing_ai_node_base_url",
            )

        client = self._http_client or httpx.Client(timeout=self._timeout_s)
        started_at = time.perf_counter()
        try:
            return self._execute_target(
                client=client,
                target=target,
                payload=payload,
                session_id=session_id,
                context=context,
                started_at=started_at,
            )
        except Exception as exc:
            static_target = self._static_target()
            if target.source == "core" and static_target and static_target.url != target.url:
                try:
                    log.warning(
                        "Core-resolved AI Node URL failed; retrying configured AI URL: resolved_url=%s static_url=%s error=%s",
                        target.url,
                        static_target.url,
                        exc,
                    )
                    return self._execute_target(
                        client=client,
                        target=static_target,
                        payload=payload,
                        session_id=session_id,
                        context=context,
                        started_at=time.perf_counter(),
                    )
                except Exception as retry_exc:
                    exc = retry_exc
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
        static_target = self._static_target()
        return {
            "provider": "ai_node",
            "healthy": self._last_error is None,
            "configured": bool(self._base_url),
            "base_url": self._base_url,
            "turn_path": self._turn_path,
            "prompt_id": self._prompt_id,
            "prompt_version": self._prompt_version,
            "last_resolved_url": self._last_resolved_url,
            "last_resolved_service_id": self._last_resolved_service_id,
            "last_resolution_source": self._last_resolution_source,
            "last_resolution_error": self._last_resolution_error,
            "last_error": self._last_error,
            "last_error_code": self._last_error_code,
            "last_latency_ms": self._last_latency_ms,
            "contract_version": self._contract_version_for_url(static_target.url if static_target else self._turn_path),
            "fallback": self._fallback.status(),
        }


@dataclass(frozen=True)
class AiIntentClassifierMatch:
    kind: str
    intent: str | None = None
    confidence: float | None = None
    arguments: dict[str, Any] | None = None
    text: str | None = None
    provider_id: str = "ai_node_intent_classifier"
    provider_metadata: dict[str, Any] | None = None
    provider_latency_ms: float | None = None


class AiNodeIntentClassifier(AiNodeAssistantAdapter):
    PROMPT_ID = "prompt.hexevoice.intent_classifier"
    PROMPT_VERSION = "v1.1"
    PROVIDER_ID = "ai_node_intent_classifier"
    JSON_SCHEMA: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "type": {"type": "string", "enum": ["intent", "clarification", "chat"]},
            "intent": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "arguments": {"type": "object"},
            "text": {"type": "string"},
        },
        "required": ["type"],
    }

    def __init__(
        self,
        *,
        base_url: str | None,
        turn_path: str,
        timeout_s: float,
        fallback: AssistantAdapter,
        prompt_id: str | None = None,
        prompt_version: str | None = None,
        min_confidence: float = 0.65,
        onboarding_state_store: OnboardingStateStore | None = None,
        core_client: CoreOnboardingClient | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            turn_path=turn_path,
            timeout_s=timeout_s,
            fallback=fallback,
            prompt_id=prompt_id or self.PROMPT_ID,
            prompt_version=prompt_version or self.PROMPT_VERSION,
            onboarding_state_store=onboarding_state_store,
            core_client=core_client,
            http_client=http_client,
        )
        self._min_confidence = max(0.0, min(1.0, float(min_confidence)))

    def classify(
        self,
        *,
        payload: AssistantTurnRequest,
        session_id: str,
        context: Sequence[ConversationTurn],
        intent_catalog: str,
    ) -> AiIntentClassifierMatch | None:
        target = self._resolve_target(payload, session_id=session_id)
        if not target:
            self._last_error_code = "missing_ai_node_base_url"
            self._last_error = "missing_ai_node_base_url"
            return None

        client = self._http_client or httpx.Client(timeout=self._timeout_s)
        started_at = time.perf_counter()
        try:
            response = client.post(
                target.url,
                json=self._classifier_request_json(
                    target=target,
                    payload=payload,
                    session_id=session_id,
                    context=context,
                    intent_catalog=intent_catalog,
                ),
            )
            response.raise_for_status()
            data = response.json()
            if str(data.get("status") or "").lower() in {"failed", "rejected", "error"}:
                raise ValueError(str(data.get("error_message") or data.get("error_code") or "ai_node_classifier_failed"))
            classifier_output = self._classifier_output_from_response(data)
            if classifier_output is None:
                raise ValueError("empty_ai_node_classifier_reply")
            latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._last_error = None
            self._last_error_code = None
            self._last_latency_ms = latency_ms
            self._last_resolved_url = target.url
            self._last_resolved_service_id = target.service_id
            self._last_resolution_source = target.source
            self._last_resolution_error = None if target.source == "core" else self._last_resolution_error
            metadata = self._metadata_from_response(data, target) or {}
            metadata["intent_classifier"] = {
                "prompt_id": self._prompt_id,
                "prompt_version": self._prompt_version,
                "min_confidence": self._min_confidence,
            }
            return AiIntentClassifierMatch(
                kind=str(classifier_output.get("type") or "").strip().lower(),
                intent=str(classifier_output.get("intent") or "").strip() or None,
                confidence=self._confidence(classifier_output.get("confidence")),
                arguments=classifier_output.get("arguments") if isinstance(classifier_output.get("arguments"), dict) else {},
                text=str(classifier_output.get("text") or "").strip() or None,
                provider_metadata=metadata,
                provider_latency_ms=latency_ms,
            )
        except Exception as exc:
            self._last_latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._last_error_code = self._error_code(exc)
            self._last_error = str(exc)
            log.warning("AI Node intent classifier failed; continuing with assistant fallback: code=%s error=%s", self._last_error_code, exc)
            return None
        finally:
            if self._http_client is None:
                client.close()

    def _classifier_request_json(
        self,
        *,
        target: AiNodeRequestTarget,
        payload: AssistantTurnRequest,
        session_id: str,
        context: Sequence[ConversationTurn],
        intent_catalog: str,
    ) -> dict[str, Any]:
        runtime_context = self._runtime_context()
        node_clock = self._node_clock_context(runtime_context)
        conversation_context = [
            {
                "endpoint_id": turn.endpoint_id,
                "session_id": turn.session_id,
                "heard_text": turn.heard_text,
                "reply_text": turn.reply_text,
            }
            for turn in context
        ]
        prompt_text = self._classifier_prompt_text(
            user_text=payload.text,
            intent_catalog=intent_catalog,
            node_clock=node_clock,
            conversation_context=conversation_context,
        )
        if self._contract_version_for_url(target.url) == "client-ai.execution.v2":
            return {
                "task_id": f"hexevoice-intent-classifier-{uuid4().hex}",
                "prompt_id": self._prompt_id,
                "prompt_version": self._prompt_version,
                "task_family": self.TASK_FAMILY,
                "requested_by": "hexevoice",
                "service_id": self.EXECUTION_SERVICE_ID,
                "inputs": {
                    "text": prompt_text,
                    "user_text": payload.text,
                    "original_text": payload.text,
                    "intent_catalog": intent_catalog,
                    "json_schema": self.JSON_SCHEMA,
                    "conversation_context": conversation_context,
                    "endpoint_id": payload.endpoint_id,
                    "session_id": session_id,
                    "speaker_identity": payload.speaker_identity,
                    "speaker_identity_policy": payload.speaker_identity_policy,
                    "speaker_personalization_enabled": payload.speaker_personalization_enabled,
                    "runtime_context": runtime_context,
                    "node_clock": node_clock,
                    "system_context": self._node_clock_instruction(node_clock),
                },
                "constraints": {"structured_output_required": True},
                "response_mode": "sync",
                "timeout_s": max(1, int(self._timeout_s)),
                "trace_id": f"{session_id}-intent-{uuid4().hex[:8]}",
            }
        return {
            "contract_version": "voice.ai_node.intent_classifier.v1",
            "source_node_type": "voice-node",
            "endpoint_id": payload.endpoint_id,
            "session_id": session_id,
            "text": prompt_text,
            "user_text": payload.text,
            "original_text": payload.text,
            "intent_catalog": intent_catalog,
            "json_schema": self.JSON_SCHEMA,
            "conversation_context": conversation_context,
            "speaker_identity": payload.speaker_identity,
            "speaker_identity_policy": payload.speaker_identity_policy,
            "speaker_personalization_enabled": payload.speaker_personalization_enabled,
            "runtime_context": runtime_context,
            "node_clock": node_clock,
            "system_context": self._node_clock_instruction(node_clock),
        }

    @staticmethod
    def _classifier_prompt_text(
        *,
        user_text: str,
        intent_catalog: str,
        node_clock: dict[str, Any],
        conversation_context: list[dict[str, Any]],
    ) -> str:
        return (
            "You are HexeVoice, a concise household voice assistant and intent router.\n\n"
            "Your job is to decide whether the user's message matches one of the recognized intents below.\n\n"
            f"Recognized intents:\n{intent_catalog}\n\n"
            "The only valid intent names are the literal IDs after '- ' in the catalog. "
            "Return those IDs exactly, such as voice.date.query; do not convert them to function-style aliases.\n\n"
            "Return exactly one valid JSON object following the registered classifier prompt rules.\n\n"
            f"Authoritative node clock: {json.dumps(node_clock, sort_keys=True)}\n"
            f"Conversation context: {json.dumps(conversation_context, sort_keys=True)}\n"
            f"User message: {user_text}"
        )

    def _classifier_output_from_response(self, data: dict[str, Any]) -> dict[str, Any] | None:
        for value in (data.get("output"), data.get("result"), data):
            parsed = self._json_object_from_value(value)
            if parsed is not None and str(parsed.get("type") or "").strip().lower() in {"intent", "clarification", "chat"}:
                return parsed
        text = self._response_text(data)
        return self._json_object_from_value(text)

    @staticmethod
    def _json_object_from_value(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if isinstance(value.get("text"), str) and len(value) <= 3:
                return AiNodeIntentClassifier._json_object_from_value(value.get("text"))
            return value
        if not isinstance(value, str):
            return None
        text = value.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
            text = re.sub(r"\s*```$", "", text).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _confidence(value: object) -> float | None:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, confidence))

    def is_confident_intent(self, match: AiIntentClassifierMatch) -> bool:
        return match.kind == "intent" and match.intent is not None and (match.confidence or 0.0) >= self._min_confidence

    def status(self) -> dict:
        status = super().status()
        status.update(
            {
                "provider": self.PROVIDER_ID,
                "min_confidence": self._min_confidence,
            }
        )
        return status


class AssistantTurnService:
    def __init__(
        self,
        *,
        settings: Settings,
        runtime_service: NodeRuntimeService,
        adapter: AssistantAdapter | None = None,
        intent_finder: LocalIntentFinder | None = None,
        intent_classifier: AiNodeIntentClassifier | None = None,
        timer_event_publisher: TimerCreateEventPublisher | None = None,
        timer_ownership_cache: TimerOwnershipCache | None = None,
        endpoint_command_dispatcher: EndpointCommandDispatcher | None = None,
        onboarding_state_store: OnboardingStateStore | None = None,
        core_client: CoreOnboardingClient | None = None,
    ) -> None:
        self._settings = settings
        self._runtime_service = runtime_service
        self._session_counter = 0
        self._onboarding_state_store = onboarding_state_store
        self._core_client = core_client or CoreOnboardingClient()
        self._adapter = adapter or self._build_adapter()
        self._intent_finder = intent_finder or LocalIntentFinder()
        self._intent_classifier = intent_classifier or self._build_intent_classifier()
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
        if not heard_text:
            return self.no_speech_response(payload.endpoint_id, session_id=session_id)
        requested_at = utc_event_timestamp()
        intent_started_at = time.perf_counter()
        pending_followup = self._pending_followup(endpoint_id=payload.endpoint_id, session_id=session_id, now=requested_at)
        intent = self._intent_finder.find(
            heard_text,
            requested_at=requested_at,
            pending_followup=pending_followup.as_dict() if pending_followup else None,
        )
        if intent is not None:
            return self._handle_matched_intent(
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                intent=intent,
                requested_at=requested_at,
                intent_started_at=intent_started_at,
            )

        context = self._conversation_context(endpoint_id=payload.endpoint_id, session_id=session_id)
        classifier_response = self._classify_missed_intent(
            payload=payload,
            heard_text=heard_text,
            session_id=session_id,
            context=context,
            requested_at=requested_at,
            intent_started_at=intent_started_at,
        )
        if classifier_response is not None:
            return classifier_response

        response = self._adapter.handle_turn(
            AssistantTurnRequest(
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                text=heard_text or " ",
                speaker_identity=payload.speaker_identity,
                speaker_identity_policy=payload.speaker_identity_policy,
                speaker_personalization_enabled=payload.speaker_personalization_enabled,
            ),
            session_id=session_id,
            context=context,
        )
        self._record_turn(response)
        return response

    def no_speech_response(
        self,
        endpoint_id: str,
        *,
        session_id: str,
        reason: str = "empty_transcript",
    ) -> AssistantTurnResponse:
        reply_text = "I didn't catch that."
        return AssistantTurnResponse(
            endpoint_id=endpoint_id,
            session_id=session_id,
            heard_text="",
            reply_text=reply_text,
            spoken_text=reply_text,
            handled_locally=True,
            command=None,
            device_state="speaking",
            provider_id="no_speech",
            provider_metadata={"reason": reason},
        )

    def status(self) -> dict:
        return {
            **self._adapter.status(),
            "local_intents": self._intent_finder.status(),
            "intent_classifier": self._intent_classifier.status() if self._intent_classifier else None,
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
        recorded_heard_text = _recorded_heard_text_for_intent(intent, heard_text)
        reply_audio = self._synthesize_intent_reply_audio(
            endpoint_id=endpoint_id,
            session_id=resolved_session_id,
            intent=intent,
            event_id=recognized_event_id,
            heard_text=recorded_heard_text,
            reply_audio_factory=reply_audio_factory,
        )
        recognition_decision = self._publish_intent_recognized_event(
            endpoint_id=endpoint_id,
            session_id=resolved_session_id,
            heard_text=recorded_heard_text,
            intent=intent,
            requested_at=requested_at,
            event_id=recognized_event_id,
            reply_audio=reply_audio,
            intent_latency_ms=self._elapsed_ms(intent_started_at),
        )
        dispatch_decision = self._dispatch_intent(
            endpoint_id=endpoint_id,
            session_id=resolved_session_id,
            heard_text=recorded_heard_text,
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
            heard_text=recorded_heard_text,
            reply_text=intent.reply_text,
            spoken_text=intent.reply_text,
            handled_locally=True,
            command=intent.command,
            device_state="speaking",
            provider_id=intent.provider_id,
            provider_metadata=self._intent_provider_metadata(intent),
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
            heard_text=recorded_heard_text,
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
                prompt_id=self._settings.voice_assistant_ai_node_prompt_id,
                prompt_version=self._settings.voice_assistant_ai_node_prompt_version,
                onboarding_state_store=self._onboarding_state_store,
                core_client=self._core_client,
                fallback=fallback,
            )
        return fallback

    def _build_intent_classifier(self) -> AiNodeIntentClassifier | None:
        if not self._settings.voice_intent_ai_classifier_enabled:
            return None
        if self._settings.voice_assistant_provider != "ai_node":
            return None
        return AiNodeIntentClassifier(
            base_url=self._settings.voice_assistant_ai_node_base_url,
            turn_path=self._settings.voice_assistant_ai_node_turn_path,
            timeout_s=self._settings.voice_intent_ai_classifier_timeout_s,
            prompt_id=self._settings.voice_intent_ai_classifier_prompt_id,
            prompt_version=self._settings.voice_intent_ai_classifier_prompt_version,
            min_confidence=self._settings.voice_intent_ai_classifier_min_confidence,
            onboarding_state_store=self._onboarding_state_store,
            core_client=self._core_client,
            fallback=LocalEchoAssistantAdapter(),
        )

    def _classify_missed_intent(
        self,
        *,
        payload: AssistantTurnRequest,
        heard_text: str,
        session_id: str,
        context: Sequence[ConversationTurn],
        requested_at: datetime,
        intent_started_at: float,
    ) -> AssistantTurnResponse | None:
        if self._intent_classifier is None:
            return None
        classification = self._intent_classifier.classify(
            payload=AssistantTurnRequest(
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                text=heard_text or " ",
                speaker_identity=payload.speaker_identity,
                speaker_identity_policy=payload.speaker_identity_policy,
                speaker_personalization_enabled=payload.speaker_personalization_enabled,
            ),
            session_id=session_id,
            context=context,
            intent_catalog=self._intent_finder.ai_intent_catalog(),
        )
        if classification is None:
            return None
        if self._intent_classifier.is_confident_intent(classification):
            intent = self._intent_finder.match_ai_classification(
                {
                    "type": classification.kind,
                    "intent": classification.intent,
                    "confidence": classification.confidence,
                    "arguments": classification.arguments or {},
                },
                requested_at=requested_at,
                provider_id=classification.provider_id,
            )
            if intent is not None:
                response = self._handle_matched_intent(
                    endpoint_id=payload.endpoint_id,
                    session_id=session_id,
                    heard_text=heard_text,
                    intent=intent,
                    requested_at=requested_at,
                    intent_started_at=intent_started_at,
                )
                provider_metadata = response.provider_metadata or {}
                provider_metadata["ai_intent_classifier"] = classification.provider_metadata or {}
                return response.model_copy(
                    update={
                        "provider_latency_ms": classification.provider_latency_ms,
                        "provider_metadata": provider_metadata,
                    }
                )
        if classification.kind in {"clarification", "chat"} and classification.text:
            intent_latency_ms = self._elapsed_ms(intent_started_at)
            self._record_intent_latency(
                matched=False,
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                intent_id=classification.intent,
                command=None,
                provider_id=classification.provider_id,
                latency_ms=intent_latency_ms,
            )
            response = AssistantTurnResponse(
                endpoint_id=payload.endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                reply_text=classification.text,
                spoken_text=classification.text,
                handled_locally=False,
                command=None,
                device_state="speaking",
                provider_id=classification.provider_id,
                provider_latency_ms=classification.provider_latency_ms,
                provider_metadata=classification.provider_metadata,
                intent_latency_ms=intent_latency_ms,
            )
            self._record_turn(response)
            return response
        return None

    def _handle_matched_intent(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        intent,
        requested_at: datetime,
        intent_started_at: float,
    ) -> AssistantTurnResponse:
        intent = self._resolve_timer_context(intent, endpoint_id=endpoint_id)
        recorded_heard_text = _recorded_heard_text_for_intent(intent, heard_text)
        self._publish_intent_recognized_event(
            endpoint_id=endpoint_id,
            session_id=session_id,
            heard_text=recorded_heard_text,
            intent=intent,
            requested_at=requested_at,
            intent_latency_ms=self._elapsed_ms(intent_started_at),
        )
        self._dispatch_intent(
            endpoint_id=endpoint_id,
            session_id=session_id,
            heard_text=recorded_heard_text,
            intent=intent,
            requested_at=requested_at,
        )
        intent_latency_ms = self._elapsed_ms(intent_started_at)
        conversation_followup = self._apply_followup_transition(
            endpoint_id=endpoint_id,
            session_id=session_id,
            intent=intent,
            now=requested_at,
        )
        response = AssistantTurnResponse(
            endpoint_id=endpoint_id,
            session_id=session_id,
            heard_text=recorded_heard_text,
            reply_text=intent.reply_text,
            spoken_text=intent.reply_text,
            handled_locally=True,
            command=intent.command,
            device_state="speaking",
            provider_id=intent.provider_id,
            provider_metadata=self._intent_provider_metadata(intent),
            intent_latency_ms=intent_latency_ms,
            conversation_followup=conversation_followup,
        )
        self._record_intent_latency(
            matched=True,
            endpoint_id=endpoint_id,
            session_id=session_id,
            intent_id=intent.intent,
            command=intent.command,
            provider_id=intent.provider_id,
            latency_ms=intent_latency_ms,
        )
        self._record_turn(response)
        return response

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

    def _intent_provider_metadata(self, intent) -> dict[str, Any]:
        metadata = {
            "voice_intent": {
                "intent_id": intent.intent,
                "intent_name": intent.intent_name,
                "service_id": intent.service_id,
                "version": intent.version,
                "provider_id": intent.provider_id,
                "metadata": dict(intent.metadata or {}),
                "constraints": dict(intent.constraints or {}),
            },
            "speaker_identity_policy": intent.speaker_identity_policy or "use_if_ready",
        }
        return metadata

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


def _recorded_heard_text_for_intent(intent, heard_text: str) -> str:
    command = str(getattr(intent, "command", "") or "").strip()
    metadata = getattr(intent, "metadata", None)
    constraints = getattr(intent, "constraints", None)
    if command.startswith("admin."):
        return redact_spoken_passcodes(heard_text)
    if isinstance(metadata, dict) and metadata.get("admin_maintenance_action"):
        return redact_spoken_passcodes(heard_text)
    if isinstance(constraints, dict) and constraints.get("admin_maintenance_action"):
        return redact_spoken_passcodes(heard_text)
    return heard_text
