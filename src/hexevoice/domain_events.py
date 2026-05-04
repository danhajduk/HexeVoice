from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import uuid
from typing import Any, Protocol

from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore


log = logging.getLogger(__name__)


def domain_event_topic(node_id: str, event_type: str) -> str:
    node_key = str(node_id or "").strip()
    parts = str(event_type or "").strip().lower().split(".")
    if len(parts) < 2:
        raise ValueError("event_type must use <domain>.<event_name>")
    domain = parts[0]
    event_name = "/".join(parts[1:])
    if not node_key or not domain or not event_name:
        raise ValueError("node_id and event_type are required")
    return f"hexe/nodes/{node_key}/events/{domain}/{event_name}"


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

    def status(self) -> dict[str, Any]:
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

    def status(self) -> dict[str, Any]:
        return {"provider": "noop", "enabled": False, "last_decision": None}


class HexeMqttTimerCreateEventPublisher:
    def __init__(self, *, settings: Settings, onboarding_state_store: OnboardingStateStore | None = None) -> None:
        self._settings = settings
        self._store = onboarding_state_store or OnboardingStateStore(path=settings.resolved_onboarding_state_path())
        self._last_decision: DomainEventPublishDecision | None = None

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
        topic = domain_event_topic(node_id, event_type) if node_id else None

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
                "heard_text": heard_text,
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

    def status(self) -> dict[str, Any]:
        return {
            "provider": "hexe_mqtt",
            "enabled": self._settings.voice_domain_events_enabled,
            "event_type": "timer.create_requested",
            "last_decision": self._last_decision.as_dict() if self._last_decision else None,
        }

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

    def _publish(
        self,
        *,
        host: str,
        port: int,
        identity: str,
        token: str,
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
            info = client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=False)
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
