from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import queue
import re
import threading
import uuid
from typing import Any, Callable, Protocol

from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore


log = logging.getLogger(__name__)


DOMAIN_EVENT_MAX_PAYLOAD_BYTES = 65_536
DOMAIN_EVENT_TYPE_RE = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$")
DOMAIN_EVENT_LEGACY_FIELDS = {
    "promotion_id", "promoted_event_type", "received_at", "promoted_at", "core", "routing", "policy"
}
DOMAIN_EVENT_PRIVATE_FIELDS = {
    "access_token", "api_key", "apikey", "attachment", "attachments", "authorization",
    "body_html", "client_secret", "cookie", "credentials", "email_body", "full_address",
    "heard_text", "html", "oauth_token", "password", "raw_body", "raw_email_body",
    "recognized_text", "refresh_token", "reply_audio", "reply_text", "secret",
    "session_cookie", "token",
}


def domain_event_topic(event_type: str) -> str:
    normalized = str(event_type or "").strip().lower()
    if not DOMAIN_EVENT_TYPE_RE.fullmatch(normalized):
        raise ValueError("event_type must use dotted lower-case domain event form")
    return f"hexe/events/{normalized.replace('.', '/')}"


def validate_domain_event_publish(topic: str, payload: dict[str, Any], *, trusted_node_id: str) -> bytes:
    if not isinstance(payload, dict):
        raise ValueError("domain_event_payload_must_be_object")
    legacy_fields = DOMAIN_EVENT_LEGACY_FIELDS.intersection(payload)
    if legacy_fields:
        raise ValueError(f"legacy_domain_event_fields:{','.join(sorted(legacy_fields))}")
    event_id = str(payload.get("event_id") or "").strip()
    event_type = str(payload.get("event_type") or "").strip()
    source = payload.get("source")
    if not event_id or not DOMAIN_EVENT_TYPE_RE.fullmatch(event_type):
        raise ValueError("invalid_domain_event_identity")
    schema_version = payload.get("schema_version", 1)
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise ValueError("invalid_domain_event_schema_version")
    if topic != domain_event_topic(event_type):
        raise ValueError("domain_event_topic_type_mismatch")
    if not isinstance(source, dict) or str(source.get("node_id") or "").strip() != str(trusted_node_id or "").strip():
        raise ValueError("domain_event_source_identity_mismatch")
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("domain_event_data_must_be_object")
    subject = payload.get("subject", {})
    if not isinstance(subject, dict):
        raise ValueError("domain_event_subject_must_be_object")
    _reject_private_domain_event_content(data)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(encoded) > DOMAIN_EVENT_MAX_PAYLOAD_BYTES:
        raise ValueError("domain_event_payload_too_large")
    return encoded


def _reject_private_domain_event_content(value: Any, path: str = "data") -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            child_path = f"{path}.{raw_key}"
            if key in DOMAIN_EVENT_PRIVATE_FIELDS:
                raise ValueError(f"private_domain_event_field:{child_path}")
            _reject_private_domain_event_content(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_private_domain_event_content(child, f"{path}[{index}]")


def format_duration_hhmmss(duration_seconds: int) -> str:
    seconds = max(0, int(duration_seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def utc_event_timestamp() -> datetime:
    return datetime.now(UTC)


def format_event_timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat()


def event_latency_ms(started_at: datetime, ended_at: datetime) -> float:
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=UTC)
    started_at = started_at.astimezone(UTC)
    ended_at = ended_at.astimezone(UTC)
    return round((ended_at - started_at).total_seconds() * 1000, 3)


@dataclass(frozen=True)
class DomainEventPublishDecision:
    status: str
    reason: str
    event_id: str | None = None
    event_type: str | None = None
    topic: str | None = None
    published_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "topic": self.topic,
            "published_at": self.published_at,
        }


class TimerCreateEventPublisher(Protocol):
    def publish_timer_create(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        duration_seconds: int,
        duration_text: str,
        requested_at: datetime,
    ) -> DomainEventPublishDecision:
        ...

    def publish_timer_status_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        ...

    def publish_timer_control_request(
        self,
        *,
        action: str,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        ...

    def publish_timer_adjust_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        delta_seconds: int,
        delta_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        ...

    def publish_timer_snooze_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        duration_seconds: int,
        duration_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        ...

    def status(self) -> dict[str, Any]:
        ...

    def publish_voice_intent_recognized(
        self,
        *,
        event_id: str,
        endpoint_id: str,
        session_id: str,
        intent_id: str,
        intent_name: str | None,
        command: str,
        provider_id: str,
        recognized_text: str,
        slots: dict[str, Any],
        reply_text: str | None,
        requested_at: datetime,
        service_id: str | None = None,
        version: str | None = None,
        dispatch: dict[str, Any] | None = None,
        reply_audio: dict[str, Any] | None = None,
        intent_latency_ms: float | None = None,
    ) -> DomainEventPublishDecision:
        ...


class NoopTimerCreateEventPublisher:
    def publish_timer_create(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        duration_seconds: int,
        duration_text: str,
        requested_at: datetime,
    ) -> DomainEventPublishDecision:
        return DomainEventPublishDecision(status="skipped", reason="domain_events_disabled")

    def publish_timer_status_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        return DomainEventPublishDecision(status="skipped", reason="domain_events_disabled")

    def publish_timer_control_request(
        self,
        *,
        action: str,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        return DomainEventPublishDecision(status="skipped", reason="domain_events_disabled")

    def publish_timer_adjust_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        delta_seconds: int,
        delta_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        return DomainEventPublishDecision(status="skipped", reason="domain_events_disabled")

    def publish_timer_snooze_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        duration_seconds: int,
        duration_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        return DomainEventPublishDecision(status="skipped", reason="domain_events_disabled")

    def status(self) -> dict[str, Any]:
        return {"provider": "noop", "enabled": False, "last_decision": None}

    def publish_voice_intent_recognized(
        self,
        *,
        event_id: str,
        endpoint_id: str,
        session_id: str,
        intent_id: str,
        intent_name: str | None,
        command: str,
        provider_id: str,
        recognized_text: str,
        slots: dict[str, Any],
        reply_text: str | None,
        requested_at: datetime,
        service_id: str | None = None,
        version: str | None = None,
        dispatch: dict[str, Any] | None = None,
        reply_audio: dict[str, Any] | None = None,
        intent_latency_ms: float | None = None,
    ) -> DomainEventPublishDecision:
        return DomainEventPublishDecision(status="skipped", reason="domain_events_disabled", event_id=event_id, event_type="voice.intent.recognized")


class AsyncDomainEventPublisher:
    def __init__(self, delegate: TimerCreateEventPublisher, *, queue_size: int = 200) -> None:
        self._delegate = delegate
        self._queue: queue.Queue[tuple[Callable[[], DomainEventPublishDecision], DomainEventPublishDecision]] = (
            queue.Queue(maxsize=max(1, queue_size))
        )
        self._last_queued_decision: DomainEventPublishDecision | None = None
        self._last_worker_decision: DomainEventPublishDecision | None = None
        self._worker = threading.Thread(target=self._run, name="hexe-domain-events", daemon=True)
        self._worker.start()

    def publish_timer_create(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        duration_seconds: int,
        duration_text: str,
        requested_at: datetime,
    ) -> DomainEventPublishDecision:
        event_id = f"voice-timer-{uuid.uuid4().hex}"
        decision = DomainEventPublishDecision(
            status="queued",
            reason="queued_for_async_publish",
            event_id=event_id,
            event_type="timer.create_requested",
        )
        return self._enqueue(
            lambda: self._delegate.publish_timer_create(
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                duration_seconds=duration_seconds,
                duration_text=duration_text,
                requested_at=requested_at,
            ),
            decision,
        )

    def publish_timer_status_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        event_id = f"voice-timer-status-{uuid.uuid4().hex}"
        decision = DomainEventPublishDecision(
            status="queued",
            reason="queued_for_async_publish",
            event_id=event_id,
            event_type="timer.status_requested",
        )
        return self._enqueue(
            lambda: self._delegate.publish_timer_status_request(
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                requested_at=requested_at,
                scope=scope,
                timer_id=timer_id,
            ),
            decision,
        )

    def publish_timer_control_request(
        self,
        *,
        action: str,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        normalized_action = str(action or "").strip().lower()
        event_type = f"timer.{normalized_action}_requested" if normalized_action else "timer.control_requested"
        event_id = f"voice-timer-{normalized_action or 'control'}-{uuid.uuid4().hex}"
        decision = DomainEventPublishDecision(
            status="queued",
            reason="queued_for_async_publish",
            event_id=event_id,
            event_type=event_type,
        )
        return self._enqueue(
            lambda: self._delegate.publish_timer_control_request(
                action=normalized_action,
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                requested_at=requested_at,
                scope=scope,
                timer_id=timer_id,
            ),
            decision,
        )

    def publish_timer_adjust_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        delta_seconds: int,
        delta_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        event_id = f"voice-timer-adjust-{uuid.uuid4().hex}"
        decision = DomainEventPublishDecision(
            status="queued",
            reason="queued_for_async_publish",
            event_id=event_id,
            event_type="timer.adjust_time_requested",
        )
        return self._enqueue(
            lambda: self._delegate.publish_timer_adjust_request(
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                delta_seconds=delta_seconds,
                delta_text=delta_text,
                requested_at=requested_at,
                scope=scope,
                timer_id=timer_id,
            ),
            decision,
        )

    def publish_timer_snooze_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        duration_seconds: int,
        duration_text: str,
        requested_at: datetime,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        event_id = f"voice-timer-snooze-{uuid.uuid4().hex}"
        decision = DomainEventPublishDecision(
            status="queued",
            reason="queued_for_async_publish",
            event_id=event_id,
            event_type="timer.snooze_requested",
        )
        return self._enqueue(
            lambda: self._delegate.publish_timer_snooze_request(
                endpoint_id=endpoint_id,
                session_id=session_id,
                heard_text=heard_text,
                duration_seconds=duration_seconds,
                duration_text=duration_text,
                requested_at=requested_at,
                scope=scope,
                timer_id=timer_id,
            ),
            decision,
        )

    def publish_voice_intent_recognized(
        self,
        *,
        event_id: str,
        endpoint_id: str,
        session_id: str,
        intent_id: str,
        intent_name: str | None,
        command: str,
        provider_id: str,
        recognized_text: str,
        slots: dict[str, Any],
        reply_text: str | None,
        requested_at: datetime,
        service_id: str | None = None,
        version: str | None = None,
        dispatch: dict[str, Any] | None = None,
        reply_audio: dict[str, Any] | None = None,
        intent_latency_ms: float | None = None,
    ) -> DomainEventPublishDecision:
        decision = DomainEventPublishDecision(
            status="queued",
            reason="queued_for_async_publish",
            event_id=event_id,
            event_type="voice.intent.recognized",
        )
        return self._enqueue(
            lambda: self._delegate.publish_voice_intent_recognized(
                event_id=event_id,
                endpoint_id=endpoint_id,
                session_id=session_id,
                intent_id=intent_id,
                intent_name=intent_name,
                service_id=service_id,
                version=version,
                command=command,
                provider_id=provider_id,
                recognized_text=recognized_text,
                slots=slots,
                reply_text=reply_text,
                requested_at=requested_at,
                dispatch=dispatch,
                reply_audio=reply_audio,
                intent_latency_ms=intent_latency_ms,
            ),
            decision,
        )

    def status(self) -> dict[str, Any]:
        delegate_status = self._delegate.status()
        return {
            **delegate_status,
            "async_publish": True,
            "queue_depth": self._queue.qsize(),
            "last_queued_decision": self._last_queued_decision.as_dict() if self._last_queued_decision else None,
            "last_worker_decision": self._last_worker_decision.as_dict() if self._last_worker_decision else None,
        }

    def _enqueue(
        self,
        job: Callable[[], DomainEventPublishDecision],
        queued_decision: DomainEventPublishDecision,
    ) -> DomainEventPublishDecision:
        try:
            self._queue.put_nowait((job, queued_decision))
        except queue.Full:
            dropped = DomainEventPublishDecision(
                status="failed",
                reason="async_publish_queue_full",
                event_id=queued_decision.event_id,
                event_type=queued_decision.event_type,
                topic=queued_decision.topic,
            )
            self._last_queued_decision = dropped
            log.warning(
                "Domain event async publish queue full: event_type=%s event_id=%s",
                dropped.event_type,
                dropped.event_id,
            )
            return dropped
        self._last_queued_decision = queued_decision
        log.info(
            "Domain event queued for async publish: event_type=%s event_id=%s queue_depth=%s",
            queued_decision.event_type,
            queued_decision.event_id,
            self._queue.qsize(),
        )
        return queued_decision

    def _run(self) -> None:
        while True:
            job, queued_decision = self._queue.get()
            try:
                self._last_worker_decision = job()
            except Exception:
                log.exception(
                    "Domain event async publish failed unexpectedly: event_type=%s event_id=%s",
                    queued_decision.event_type,
                    queued_decision.event_id,
                )
                self._last_worker_decision = DomainEventPublishDecision(
                    status="failed",
                    reason="async_publish_worker_exception",
                    event_id=queued_decision.event_id,
                    event_type=queued_decision.event_type,
                    topic=queued_decision.topic,
                )
            finally:
                self._queue.task_done()


class HexeMqttTimerCreateEventPublisher:
    def __init__(self, *, settings: Settings, onboarding_state_store: OnboardingStateStore | None = None) -> None:
        self._settings = settings
        self._store = onboarding_state_store or OnboardingStateStore(path=settings.resolved_onboarding_state_path())
        self._last_decision: DomainEventPublishDecision | None = None
        self._last_recognition_decision: DomainEventPublishDecision | None = None

    def publish_timer_create(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        duration_seconds: int,
        duration_text: str,
        requested_at: datetime | None = None,
    ) -> DomainEventPublishDecision:
        event_type = "timer.create_requested"
        event_id = f"voice-timer-{uuid.uuid4().hex}"
        state = self._store.load()
        trust = state.trust_activation
        node_id = str(trust.node_id or "").strip()
        topic = domain_event_topic(event_type) if node_id else None

        if not self._settings.voice_domain_events_enabled:
            return self._record(DomainEventPublishDecision("skipped", "domain_events_disabled", event_id, event_type, topic))
        if state.operational_status.operational_ready is not True:
            return self._record(DomainEventPublishDecision("skipped", "operational_readiness_required", event_id, event_type, topic))
        if trust.trust_status != "trusted":
            return self._record(DomainEventPublishDecision("skipped", "trusted_node_required", event_id, event_type, topic))
        if not node_id or not trust.operational_mqtt_identity or not trust.operational_mqtt_token:
            return self._record(DomainEventPublishDecision("skipped", "missing_operational_mqtt_credentials", event_id, event_type, topic))
        if not trust.operational_mqtt_host or not trust.operational_mqtt_port:
            return self._record(DomainEventPublishDecision("skipped", "missing_operational_mqtt_endpoint", event_id, event_type, topic))

        request_timestamp = requested_at or utc_event_timestamp()
        requested_at_text = format_event_timestamp(request_timestamp)
        payload = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": requested_at_text,
            "source": {
                "node_id": node_id,
                "component": "hexevoice.assistant.local_intents",
                "node_type": trust.node_type or self._settings.node_type,
            },
            "subject": {
                "family": "timer",
                "record_id": session_id,
            },
            "data": {
                "intent": "timer.create",
                "endpoint_id": endpoint_id,
                "session_id": session_id,
                "duration_seconds": duration_seconds,
                "duration_hhmmss": format_duration_hhmmss(duration_seconds),
                "duration_text": duration_text,
                "requested_at": requested_at_text,
            },
            "severity": "info",
            "priority": "normal",
            "safety_critical": False,
        }
        try:
            self._publish(
                host=trust.operational_mqtt_host,
                port=int(trust.operational_mqtt_port),
                identity=trust.operational_mqtt_identity,
                token=trust.operational_mqtt_token,
                trusted_node_id=node_id,
                topic=topic or "",
                payload=payload,
                request_timestamp=request_timestamp,
            )
        except ModuleNotFoundError:
            return self._record(DomainEventPublishDecision("failed", "missing_paho_mqtt_dependency", event_id, event_type, topic))
        except Exception as exc:
            log.warning("Timer create domain event publish failed: error=%s", exc)
            return self._record(DomainEventPublishDecision("failed", "mqtt_publish_failed", event_id, event_type, topic))

        return self._record(
            DomainEventPublishDecision(
                status="published",
                reason="published",
                event_id=event_id,
                event_type=event_type,
                topic=topic,
                published_at=datetime.now(UTC).isoformat(),
            )
        )

    def publish_timer_status_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        requested_at: datetime | None = None,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        event_type = "timer.status_requested"
        event_id = f"voice-timer-status-{uuid.uuid4().hex}"
        state = self._store.load()
        trust = state.trust_activation
        node_id = str(trust.node_id or "").strip()
        topic = domain_event_topic(event_type) if node_id else None

        if not self._settings.voice_domain_events_enabled:
            return self._record(DomainEventPublishDecision("skipped", "domain_events_disabled", event_id, event_type, topic))
        if state.operational_status.operational_ready is not True:
            return self._record(DomainEventPublishDecision("skipped", "operational_readiness_required", event_id, event_type, topic))
        if trust.trust_status != "trusted":
            return self._record(DomainEventPublishDecision("skipped", "trusted_node_required", event_id, event_type, topic))
        if not node_id or not trust.operational_mqtt_identity or not trust.operational_mqtt_token:
            return self._record(DomainEventPublishDecision("skipped", "missing_operational_mqtt_credentials", event_id, event_type, topic))
        if not trust.operational_mqtt_host or not trust.operational_mqtt_port:
            return self._record(DomainEventPublishDecision("skipped", "missing_operational_mqtt_endpoint", event_id, event_type, topic))

        request_timestamp = requested_at or utc_event_timestamp()
        requested_at_text = format_event_timestamp(request_timestamp)
        correlation_id = f"timer-status-{uuid.uuid4().hex}"
        payload = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": requested_at_text,
            "source": {
                "node_id": node_id,
                "component": "hexevoice.assistant.local_intents",
                "node_type": trust.node_type or self._settings.node_type,
            },
            "subject": {
                "family": "timer",
                "record_id": timer_id or endpoint_id,
            },
            "data": {
                "intent": "timer.status",
                "endpoint_id": endpoint_id,
                "session_id": session_id,
                "timer_id": timer_id,
                "scope": scope,
                "correlation_id": correlation_id,
                "requested_at": requested_at_text,
            },
            "severity": "info",
            "priority": "normal",
            "safety_critical": False,
        }
        try:
            self._publish(
                host=trust.operational_mqtt_host,
                port=int(trust.operational_mqtt_port),
                identity=trust.operational_mqtt_identity,
                token=trust.operational_mqtt_token,
                trusted_node_id=node_id,
                topic=topic or "",
                payload=payload,
                request_timestamp=request_timestamp,
            )
        except ModuleNotFoundError:
            return self._record(DomainEventPublishDecision("failed", "missing_paho_mqtt_dependency", event_id, event_type, topic))
        except Exception as exc:
            log.warning("Timer status domain event publish failed: error=%s", exc)
            return self._record(DomainEventPublishDecision("failed", "mqtt_publish_failed", event_id, event_type, topic))

        return self._record(
            DomainEventPublishDecision(
                status="published",
                reason="published",
                event_id=event_id,
                event_type=event_type,
                topic=topic,
                published_at=datetime.now(UTC).isoformat(),
            )
        )

    def publish_timer_control_request(
        self,
        *,
        action: str,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        requested_at: datetime | None = None,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"stop", "cancel"}:
            return self._record(DomainEventPublishDecision("failed", "unsupported_timer_control", None, "timer.control_requested", None))
        event_type = f"timer.{normalized_action}_requested"
        event_id = f"voice-timer-{normalized_action}-{uuid.uuid4().hex}"
        state = self._store.load()
        trust = state.trust_activation
        node_id = str(trust.node_id or "").strip()
        topic = domain_event_topic(event_type) if node_id else None

        if not self._settings.voice_domain_events_enabled:
            return self._record(DomainEventPublishDecision("skipped", "domain_events_disabled", event_id, event_type, topic))
        if state.operational_status.operational_ready is not True:
            return self._record(DomainEventPublishDecision("skipped", "operational_readiness_required", event_id, event_type, topic))
        if trust.trust_status != "trusted":
            return self._record(DomainEventPublishDecision("skipped", "trusted_node_required", event_id, event_type, topic))
        if not node_id or not trust.operational_mqtt_identity or not trust.operational_mqtt_token:
            return self._record(DomainEventPublishDecision("skipped", "missing_operational_mqtt_credentials", event_id, event_type, topic))
        if not trust.operational_mqtt_host or not trust.operational_mqtt_port:
            return self._record(DomainEventPublishDecision("skipped", "missing_operational_mqtt_endpoint", event_id, event_type, topic))

        request_timestamp = requested_at or utc_event_timestamp()
        requested_at_text = format_event_timestamp(request_timestamp)
        correlation_id = f"timer-{normalized_action}-{uuid.uuid4().hex}"
        payload = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": requested_at_text,
            "source": {
                "node_id": node_id,
                "component": "hexevoice.assistant.local_intents",
                "node_type": trust.node_type or self._settings.node_type,
            },
            "subject": {
                "family": "timer",
                "record_id": timer_id or endpoint_id,
            },
            "data": {
                "intent": f"timer.{normalized_action}",
                "action": normalized_action,
                "endpoint_id": endpoint_id,
                "session_id": session_id,
                "timer_id": timer_id,
                "scope": scope,
                "correlation_id": correlation_id,
                "requested_at": requested_at_text,
            },
            "severity": "info",
            "priority": "normal",
            "safety_critical": False,
        }
        try:
            self._publish(
                host=trust.operational_mqtt_host,
                port=int(trust.operational_mqtt_port),
                identity=trust.operational_mqtt_identity,
                token=trust.operational_mqtt_token,
                trusted_node_id=node_id,
                topic=topic or "",
                payload=payload,
                request_timestamp=request_timestamp,
            )
        except ModuleNotFoundError:
            return self._record(DomainEventPublishDecision("failed", "missing_paho_mqtt_dependency", event_id, event_type, topic))
        except Exception as exc:
            log.warning("Timer %s domain event publish failed: error=%s", normalized_action, exc)
            return self._record(DomainEventPublishDecision("failed", "mqtt_publish_failed", event_id, event_type, topic))

        return self._record(
            DomainEventPublishDecision(
                status="published",
                reason="published",
                event_id=event_id,
                event_type=event_type,
                topic=topic,
                published_at=datetime.now(UTC).isoformat(),
            )
        )

    def publish_timer_adjust_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        delta_seconds: int,
        delta_text: str,
        requested_at: datetime | None = None,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        event_type = "timer.adjust_time_requested"
        event_id = f"voice-timer-adjust-{uuid.uuid4().hex}"
        state = self._store.load()
        trust = state.trust_activation
        node_id = str(trust.node_id or "").strip()
        topic = domain_event_topic(event_type) if node_id else None

        if not self._settings.voice_domain_events_enabled:
            return self._record(DomainEventPublishDecision("skipped", "domain_events_disabled", event_id, event_type, topic))
        if state.operational_status.operational_ready is not True:
            return self._record(DomainEventPublishDecision("skipped", "operational_readiness_required", event_id, event_type, topic))
        if trust.trust_status != "trusted":
            return self._record(DomainEventPublishDecision("skipped", "trusted_node_required", event_id, event_type, topic))
        if not node_id or not trust.operational_mqtt_identity or not trust.operational_mqtt_token:
            return self._record(DomainEventPublishDecision("skipped", "missing_operational_mqtt_credentials", event_id, event_type, topic))
        if not trust.operational_mqtt_host or not trust.operational_mqtt_port:
            return self._record(DomainEventPublishDecision("skipped", "missing_operational_mqtt_endpoint", event_id, event_type, topic))

        request_timestamp = requested_at or utc_event_timestamp()
        requested_at_text = format_event_timestamp(request_timestamp)
        correlation_id = f"timer-adjust-{uuid.uuid4().hex}"
        payload = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": requested_at_text,
            "source": {
                "node_id": node_id,
                "component": "hexevoice.assistant.local_intents",
                "node_type": trust.node_type or self._settings.node_type,
            },
            "subject": {
                "family": "timer",
                "record_id": timer_id or endpoint_id,
            },
            "data": {
                "intent": "timer.adjust_time",
                "endpoint_id": endpoint_id,
                "session_id": session_id,
                "timer_id": timer_id,
                "scope": scope,
                "delta_seconds": int(delta_seconds),
                "delta_hhmmss": format_duration_hhmmss(abs(int(delta_seconds))),
                "delta_text": delta_text,
                "direction": "add" if int(delta_seconds) >= 0 else "remove",
                "correlation_id": correlation_id,
                "requested_at": requested_at_text,
            },
            "severity": "info",
            "priority": "normal",
            "safety_critical": False,
        }
        try:
            self._publish(
                host=trust.operational_mqtt_host,
                port=int(trust.operational_mqtt_port),
                identity=trust.operational_mqtt_identity,
                token=trust.operational_mqtt_token,
                trusted_node_id=node_id,
                topic=topic or "",
                payload=payload,
                request_timestamp=request_timestamp,
            )
        except ModuleNotFoundError:
            return self._record(DomainEventPublishDecision("failed", "missing_paho_mqtt_dependency", event_id, event_type, topic))
        except Exception as exc:
            log.warning("Timer adjust domain event publish failed: error=%s", exc)
            return self._record(DomainEventPublishDecision("failed", "mqtt_publish_failed", event_id, event_type, topic))

        return self._record(
            DomainEventPublishDecision(
                status="published",
                reason="published",
                event_id=event_id,
                event_type=event_type,
                topic=topic,
                published_at=datetime.now(UTC).isoformat(),
            )
        )

    def publish_timer_snooze_request(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        heard_text: str,
        duration_seconds: int,
        duration_text: str,
        requested_at: datetime | None = None,
        scope: str = "active_for_endpoint",
        timer_id: str | None = None,
    ) -> DomainEventPublishDecision:
        event_type = "timer.snooze_requested"
        event_id = f"voice-timer-snooze-{uuid.uuid4().hex}"
        state = self._store.load()
        trust = state.trust_activation
        node_id = str(trust.node_id or "").strip()
        topic = domain_event_topic(event_type) if node_id else None

        if not self._settings.voice_domain_events_enabled:
            return self._record(DomainEventPublishDecision("skipped", "domain_events_disabled", event_id, event_type, topic))
        if state.operational_status.operational_ready is not True:
            return self._record(DomainEventPublishDecision("skipped", "operational_readiness_required", event_id, event_type, topic))
        if trust.trust_status != "trusted":
            return self._record(DomainEventPublishDecision("skipped", "trusted_node_required", event_id, event_type, topic))
        if not node_id or not trust.operational_mqtt_identity or not trust.operational_mqtt_token:
            return self._record(DomainEventPublishDecision("skipped", "missing_operational_mqtt_credentials", event_id, event_type, topic))
        if not trust.operational_mqtt_host or not trust.operational_mqtt_port:
            return self._record(DomainEventPublishDecision("skipped", "missing_operational_mqtt_endpoint", event_id, event_type, topic))

        request_timestamp = requested_at or utc_event_timestamp()
        requested_at_text = format_event_timestamp(request_timestamp)
        correlation_id = f"timer-snooze-{uuid.uuid4().hex}"
        payload = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": requested_at_text,
            "source": {
                "node_id": node_id,
                "component": "hexevoice.assistant.local_intents",
                "node_type": trust.node_type or self._settings.node_type,
            },
            "subject": {
                "family": "timer",
                "record_id": timer_id or endpoint_id,
            },
            "data": {
                "intent": "timer.snooze",
                "endpoint_id": endpoint_id,
                "session_id": session_id,
                "timer_id": timer_id,
                "scope": scope,
                "duration_seconds": int(duration_seconds),
                "duration_hhmmss": format_duration_hhmmss(abs(int(duration_seconds))),
                "duration_text": duration_text,
                "correlation_id": correlation_id,
                "requested_at": requested_at_text,
            },
            "severity": "info",
            "priority": "normal",
            "safety_critical": False,
        }
        try:
            self._publish(
                host=trust.operational_mqtt_host,
                port=int(trust.operational_mqtt_port),
                identity=trust.operational_mqtt_identity,
                token=trust.operational_mqtt_token,
                trusted_node_id=node_id,
                topic=topic or "",
                payload=payload,
                request_timestamp=request_timestamp,
            )
        except ModuleNotFoundError:
            return self._record(DomainEventPublishDecision("failed", "missing_paho_mqtt_dependency", event_id, event_type, topic))
        except Exception as exc:
            log.warning("Timer snooze domain event publish failed: error=%s", exc)
            return self._record(DomainEventPublishDecision("failed", "mqtt_publish_failed", event_id, event_type, topic))

        return self._record(
            DomainEventPublishDecision(
                status="published",
                reason="published",
                event_id=event_id,
                event_type=event_type,
                topic=topic,
                published_at=datetime.now(UTC).isoformat(),
            )
        )

    def status(self) -> dict[str, Any]:
        return {
            "provider": "hexe_mqtt",
            "enabled": self._settings.voice_domain_events_enabled,
            "event_types": [
                "timer.create_requested",
                "timer.status_requested",
                "timer.stop_requested",
                "timer.cancel_requested",
                "timer.adjust_time_requested",
                "timer.snooze_requested",
            ],
            "last_decision": self._last_decision.as_dict() if self._last_decision else None,
            "recognition_event_type": "voice.intent.recognized",
            "last_recognition_decision": self._last_recognition_decision.as_dict() if self._last_recognition_decision else None,
        }

    def publish_voice_intent_recognized(
        self,
        *,
        event_id: str,
        endpoint_id: str,
        session_id: str,
        intent_id: str,
        intent_name: str | None,
        command: str,
        provider_id: str,
        recognized_text: str,
        slots: dict[str, Any],
        reply_text: str | None,
        requested_at: datetime,
        service_id: str | None = None,
        version: str | None = None,
        dispatch: dict[str, Any] | None = None,
        reply_audio: dict[str, Any] | None = None,
        intent_latency_ms: float | None = None,
    ) -> DomainEventPublishDecision:
        event_type = "voice.intent.recognized"
        state = self._store.load()
        trust = state.trust_activation
        node_id = str(trust.node_id or "").strip()
        topic = domain_event_topic(event_type) if node_id else None

        if not self._settings.voice_domain_events_enabled:
            return self._record_recognition(DomainEventPublishDecision("skipped", "domain_events_disabled", event_id, event_type, topic))
        if state.operational_status.operational_ready is not True:
            return self._record_recognition(DomainEventPublishDecision("skipped", "operational_readiness_required", event_id, event_type, topic))
        if trust.trust_status != "trusted":
            return self._record_recognition(DomainEventPublishDecision("skipped", "trusted_node_required", event_id, event_type, topic))
        if not node_id or not trust.operational_mqtt_identity or not trust.operational_mqtt_token:
            return self._record_recognition(DomainEventPublishDecision("skipped", "missing_operational_mqtt_credentials", event_id, event_type, topic))
        if not trust.operational_mqtt_host or not trust.operational_mqtt_port:
            return self._record_recognition(DomainEventPublishDecision("skipped", "missing_operational_mqtt_endpoint", event_id, event_type, topic))

        requested_at_text = format_event_timestamp(requested_at)
        payload = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": requested_at_text,
            "source": {
                "node_id": node_id,
                "component": "hexevoice.assistant.local_intents",
                "node_type": trust.node_type or self._settings.node_type,
            },
            "subject": {
                "family": "voice_intent",
                "record_id": session_id,
            },
            "data": {
                "endpoint_id": endpoint_id,
                "session_id": session_id,
                "intent_id": intent_id,
                "intent_name": intent_name,
                "service_id": service_id,
                "version": version,
                "command": command,
                "provider_id": provider_id,
                "slots": slots,
                "parameters": slots,
                "recognized_at": requested_at_text,
                "intent_latency_ms": intent_latency_ms,
            },
            "severity": "info",
            "priority": "normal",
            "safety_critical": False,
        }
        try:
            self._publish(
                host=trust.operational_mqtt_host,
                port=int(trust.operational_mqtt_port),
                identity=trust.operational_mqtt_identity,
                token=trust.operational_mqtt_token,
                trusted_node_id=node_id,
                topic=topic or "",
                payload=payload,
                request_timestamp=requested_at,
            )
        except ModuleNotFoundError:
            return self._record_recognition(DomainEventPublishDecision("failed", "missing_paho_mqtt_dependency", event_id, event_type, topic))
        except Exception as exc:
            log.warning("Voice intent recognized event publish failed: error=%s", exc)
            return self._record_recognition(DomainEventPublishDecision("failed", "mqtt_publish_failed", event_id, event_type, topic))

        return self._record_recognition(
            DomainEventPublishDecision(
                status="published",
                reason="published",
                event_id=event_id,
                event_type=event_type,
                topic=topic,
                published_at=datetime.now(UTC).isoformat(),
            )
        )

    def _record(self, decision: DomainEventPublishDecision) -> DomainEventPublishDecision:
        self._last_decision = decision
        log.info(
            "Timer create domain event publish decision: status=%s reason=%s event_type=%s topic=%s",
            decision.status,
            decision.reason,
            decision.event_type,
            decision.topic,
        )
        return decision

    def _record_recognition(self, decision: DomainEventPublishDecision) -> DomainEventPublishDecision:
        self._last_recognition_decision = decision
        log.info(
            "Voice intent recognized event publish decision: status=%s reason=%s event_type=%s topic=%s",
            decision.status,
            decision.reason,
            decision.event_type,
            decision.topic,
        )
        return decision

    def _publish(
        self,
        *,
        host: str,
        port: int,
        identity: str,
        token: str,
        trusted_node_id: str,
        topic: str,
        payload: dict[str, Any],
        request_timestamp: datetime,
    ) -> None:
        import paho.mqtt.client as mqtt

        client_id = f"{identity}-hexevoice-events"
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:
            client = mqtt.Client(client_id=client_id)
        client.username_pw_set(identity, token)
        client.connect(host, port, keepalive=30)
        client.loop_start()
        try:
            self._stamp_mqtt_sent(payload, request_timestamp)
            encoded = validate_domain_event_publish(topic, payload, trusted_node_id=trusted_node_id)
            info = client.publish(topic, encoded, qos=1, retain=False)
            info.wait_for_publish(timeout=self._settings.voice_domain_events_mqtt_timeout_s)
            if hasattr(info, "is_published") and not info.is_published():
                raise RuntimeError("publish_not_confirmed")
        finally:
            client.loop_stop()
            client.disconnect()

    def _stamp_mqtt_sent(self, payload: dict[str, Any], request_timestamp: datetime) -> None:
        mqtt_sent_timestamp = utc_event_timestamp()
        data = payload.setdefault("data", {})
        if not isinstance(data, dict):
            data = {}
            payload["data"] = data
        data["mqtt_sent_at"] = format_event_timestamp(mqtt_sent_timestamp)
        data["request_to_mqtt_latency_ms"] = event_latency_ms(request_timestamp, mqtt_sent_timestamp)
