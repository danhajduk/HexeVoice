from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from typing import Any

from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimerAnnouncement:
    endpoint_id: str
    session_id: str
    text: str
    event_id: str
    topic: str


@dataclass(frozen=True)
class TimerCompletedAlarm:
    endpoint_id: str
    session_id: str
    timer_id: str
    text: str
    event_id: str
    dedupe_key: str
    topic: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TimerOwnerRecord:
    timer_id: str
    endpoint_id: str
    owner_node_id: str | None
    title: str | None
    state: str
    due_at: str | None
    remaining_seconds: int | None
    remaining_text: str | None
    alarm_status: str | None
    last_event_type: str
    last_event_id: str | None
    last_seen_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "timer_id": self.timer_id,
            "endpoint_id": self.endpoint_id,
            "owner_node_id": self.owner_node_id,
            "title": self.title,
            "state": self.state,
            "due_at": self.due_at,
            "remaining_seconds": self.remaining_seconds,
            "remaining_text": self.remaining_text,
            "alarm_status": self.alarm_status,
            "last_event_type": self.last_event_type,
            "last_event_id": self.last_event_id,
            "last_seen_at": self.last_seen_at,
        }


class TimerOwnershipCache:
    def __init__(self, *, max_records: int = 100) -> None:
        self._max_records = max(10, max_records)
        self._records: dict[str, TimerOwnerRecord] = {}

    def update_from_event(self, topic: str, payload: dict[str, Any]) -> list[TimerOwnerRecord]:
        event_type = str(payload.get("promoted_event_type") or payload.get("event_type") or "").strip()
        if not event_type.startswith("timer."):
            return []
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        timer_items = data.get("timers") if isinstance(data.get("timers"), list) else None
        if timer_items:
            updated: list[TimerOwnerRecord] = []
            for item in timer_items:
                if isinstance(item, dict):
                    merged = dict(data)
                    merged.update(item)
                    updated.extend(self._update_one(topic, payload, merged, event_type))
            return updated
        return self._update_one(topic, payload, data, event_type)

    def select_timer(self, endpoint_id: str) -> dict[str, Any]:
        candidates = self.active_for_endpoint(endpoint_id)
        if not candidates:
            return {"status": "unknown", "endpoint_id": endpoint_id, "candidates": []}
        if len(candidates) == 1:
            return {
                "status": "selected",
                "strategy": "single_active",
                "endpoint_id": endpoint_id,
                "timer": candidates[0].as_dict(),
                "candidates": [candidates[0].as_dict()],
            }
        due_candidates = [(record, _parse_event_datetime(record.due_at)) for record in candidates]
        due_candidates = [(record, due_at) for record, due_at in due_candidates if due_at is not None]
        if due_candidates:
            due_candidates.sort(key=lambda item: item[1])
            if len(due_candidates) == 1 or due_candidates[0][1] < due_candidates[1][1]:
                return {
                    "status": "selected",
                    "strategy": "nearest_due",
                    "endpoint_id": endpoint_id,
                    "timer": due_candidates[0][0].as_dict(),
                    "candidates": [record.as_dict() for record in candidates],
                }
        return {
            "status": "ambiguous",
            "endpoint_id": endpoint_id,
            "candidate_count": len(candidates),
            "candidates": [record.as_dict() for record in candidates],
        }

    def active_for_endpoint(self, endpoint_id: str) -> list[TimerOwnerRecord]:
        normalized_endpoint = str(endpoint_id or "").strip()
        records = [
            record
            for record in self._records.values()
            if record.endpoint_id == normalized_endpoint and _is_active_timer_state(record.state)
        ]
        return sorted(records, key=lambda record: (_parse_event_datetime(record.due_at) or datetime.max.replace(tzinfo=UTC), record.timer_id))

    def status(self) -> dict[str, Any]:
        records = sorted(
            self._records.values(),
            key=lambda record: (record.endpoint_id, _parse_event_datetime(record.due_at) or datetime.max.replace(tzinfo=UTC), record.timer_id),
        )
        active = [record for record in records if _is_active_timer_state(record.state)]
        return {
            "record_count": len(records),
            "active_count": len(active),
            "records": [record.as_dict() for record in records[: self._max_records]],
        }

    def _update_one(
        self,
        topic: str,
        payload: dict[str, Any],
        data: dict[str, Any],
        event_type: str,
    ) -> list[TimerOwnerRecord]:
        subject = payload.get("subject") if isinstance(payload.get("subject"), dict) else {}
        timer_id = str(data.get("timer_id") or subject.get("record_id") or "").strip()
        endpoint_id = str(data.get("endpoint_id") or data.get("device_id") or "").strip()
        if not timer_id or not endpoint_id:
            return []
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        existing = self._records.get(timer_id)
        state = _timer_state_from_event(event_type, data)
        owner_node_id = str(source.get("node_id") or data.get("requester_node_id") or "").strip() or None
        title = str(data.get("title") or data.get("label") or "").strip() or (existing.title if existing else None)
        due_at = str(data.get("due_at") or "").strip() or (existing.due_at if existing else None)
        remaining_text = str(data.get("remaining_text") or data.get("remaining_hhmmss") or "").strip() or (
            existing.remaining_text if existing else None
        )
        remaining_seconds = _optional_int(data.get("remaining_seconds"))
        if remaining_seconds is None and existing is not None:
            remaining_seconds = existing.remaining_seconds
        alarm_status = _alarm_status_from_event(event_type, data) or (existing.alarm_status if existing else None)
        record = TimerOwnerRecord(
            timer_id=timer_id,
            endpoint_id=endpoint_id,
            owner_node_id=owner_node_id or (existing.owner_node_id if existing else None),
            title=title,
            state=state,
            due_at=due_at,
            remaining_seconds=remaining_seconds,
            remaining_text=remaining_text,
            alarm_status=alarm_status,
            last_event_type=event_type,
            last_event_id=str(payload.get("event_id") or "").strip() or None,
            last_seen_at=datetime.now(UTC).isoformat(),
        )
        self._records[timer_id] = record
        self._trim()
        return [record]

    def _trim(self) -> None:
        if len(self._records) <= self._max_records:
            return
        records = sorted(self._records.values(), key=lambda record: record.last_seen_at, reverse=True)
        self._records = {record.timer_id: record for record in records[: self._max_records]}


def timer_success_announcement(topic: str, payload: dict[str, Any]) -> TimerAnnouncement | None:
    event_type = str(payload.get("event_type") or "").strip()
    if event_type not in {"timer.create_succeeded", "timer.status_succeeded"}:
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    endpoint_id = str(data.get("endpoint_id") or "").strip()
    if not endpoint_id:
        return None
    subject = payload.get("subject") if isinstance(payload.get("subject"), dict) else {}
    session_source = data.get("session_id") if event_type == "timer.status_succeeded" else None
    session_id = str(session_source or subject.get("record_id") or data.get("session_id") or "timer-announcement").strip()
    event_id = str(payload.get("event_id") or "").strip()
    if event_type == "timer.status_succeeded":
        state = str(data.get("state") or "").strip().lower()
        label = str(data.get("remaining_text") or data.get("remaining_hhmmss") or "").strip()
        if label:
            text = f"{label} left on the timer."
        elif state in {"inactive", "cancelled", "canceled", "cleared", "none", "not_found"}:
            text = "No active timer."
        else:
            text = "I could not read the timer remaining time."
    else:
        label = str(data.get("title") or data.get("duration_text") or data.get("duration_hhmmss") or "").strip()
        text = f"Timer is on for {label}." if label else "Timer is on."
    return TimerAnnouncement(
        endpoint_id=endpoint_id,
        session_id=session_id,
        text=text,
        event_id=event_id,
        topic=str(topic or "").strip(),
    )


def timer_completed_alarm(topic: str, payload: dict[str, Any], *, default_text: str = "Timer done.") -> TimerCompletedAlarm | None:
    event_type = str(payload.get("promoted_event_type") or payload.get("event_type") or "").strip()
    if event_type != "timer.completed":
        return None
    subject = payload.get("subject") if isinstance(payload.get("subject"), dict) else {}
    if str(subject.get("family") or "").strip() not in {"", "timer"}:
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    endpoint_id = str(data.get("endpoint_id") or data.get("device_id") or "").strip()
    if not endpoint_id:
        return None
    timer_id = str(data.get("timer_id") or subject.get("record_id") or "").strip()
    event_id = str(payload.get("event_id") or "").strip()
    if not timer_id and not event_id:
        return None
    routing = payload.get("routing") if isinstance(payload.get("routing"), dict) else {}
    dedupe_key = str(routing.get("dedupe_key") or event_id or f"{endpoint_id}:{timer_id}").strip()
    if not dedupe_key:
        return None
    title = str(data.get("title") or data.get("label") or "").strip()
    text = f"{title} timer is done." if title else str(default_text or "Timer done.").strip()
    if not text:
        text = "Timer done."
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    metadata = {
        "source": "timer.completed",
        "timer_id": timer_id or None,
        "title": title or None,
        "source_node_id": source.get("node_id") or data.get("requester_node_id"),
        "source_component": source.get("component"),
        "source_topic": source.get("topic"),
        "domain_topic": routing.get("domain_topic"),
        "dedupe_key": dedupe_key,
        "completed_at": data.get("completed_at"),
        "due_at": data.get("due_at"),
        "started_at": data.get("started_at"),
        "duration_seconds": data.get("duration_seconds"),
    }
    return TimerCompletedAlarm(
        endpoint_id=endpoint_id,
        session_id=f"timer-completed-{timer_id or event_id}",
        timer_id=timer_id,
        text=text,
        event_id=event_id,
        dedupe_key=dedupe_key,
        topic=str(topic or "").strip(),
        metadata={key: value for key, value in metadata.items() if value is not None},
    )


class TimerSucceededAnnouncementService:
    def __init__(
        self,
        *,
        settings: Settings,
        announce: Callable[[TimerAnnouncement], Awaitable[dict[str, Any]] | dict[str, Any]],
        play_alarm: Callable[[TimerCompletedAlarm], Awaitable[dict[str, Any]] | dict[str, Any]] | None = None,
        onboarding_state_store: OnboardingStateStore | None = None,
        ownership_cache: TimerOwnershipCache | None = None,
    ) -> None:
        self._settings = settings
        self._announce = announce
        self._play_alarm = play_alarm
        self._store = onboarding_state_store or OnboardingStateStore(path=settings.resolved_onboarding_state_path())
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._last_status = "stopped"
        self._last_reason: str | None = None
        self._last_announcement: dict[str, Any] | None = None
        self._last_alarm: dict[str, Any] | None = None
        self._seen_alarm_keys: list[str] = []
        self._ownership_cache = ownership_cache or TimerOwnershipCache()

    def start(self, loop: asyncio.AbstractEventLoop) -> dict[str, Any]:
        self._loop = loop
        if self._running:
            return self.status()
        if not self._settings.voice_timer_announcements_enabled:
            self._last_status = "skipped"
            self._last_reason = "timer_announcements_disabled"
            return self.status()

        state = self._store.load()
        trust = state.trust_activation
        if state.operational_status.operational_ready is not True or trust.trust_status != "trusted":
            self._last_status = "skipped"
            self._last_reason = "trusted_operational_node_required"
            return self.status()
        if not trust.operational_mqtt_identity or not trust.operational_mqtt_token:
            self._last_status = "skipped"
            self._last_reason = "missing_operational_mqtt_credentials"
            return self.status()
        if not trust.operational_mqtt_host or not trust.operational_mqtt_port:
            self._last_status = "skipped"
            self._last_reason = "missing_operational_mqtt_endpoint"
            return self.status()

        try:
            import paho.mqtt.client as mqtt

            client_id = f"{trust.operational_mqtt_identity}-hexevoice-timer-announcements"
            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
            except AttributeError:
                client = mqtt.Client(client_id=client_id)
            client.username_pw_set(trust.operational_mqtt_identity, trust.operational_mqtt_token)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.connect_async(
                trust.operational_mqtt_host,
                int(trust.operational_mqtt_port),
                keepalive=30,
            )
            client.loop_start()
        except ModuleNotFoundError:
            self._last_status = "failed"
            self._last_reason = "missing_paho_mqtt_dependency"
            return self.status()
        except Exception as exc:
            self._last_status = "failed"
            self._last_reason = "mqtt_subscribe_failed"
            log.warning("Timer announcement MQTT subscriber failed to start: error=%s", exc)
            return self.status()

        self._client = client
        self._running = True
        self._last_status = "starting"
        self._last_reason = None
        return self.status()

    def stop(self) -> None:
        client = self._client
        self._client = None
        self._running = False
        self._last_status = "stopped"
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception as exc:
            log.warning("Timer announcement MQTT subscriber failed to stop: error=%s", exc)

    def status(self) -> dict[str, Any]:
        return {
            "provider": "hexe_mqtt",
            "enabled": self._settings.voice_timer_announcements_enabled,
            "topic": self._settings.voice_timer_success_mqtt_topic,
            "topics": self._timer_topics(),
            "status": self._last_status,
            "reason": self._last_reason,
            "last_announcement": self._last_announcement,
            "last_alarm": self._last_alarm,
            "ownership": self._ownership_cache.status(),
        }

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        rc = getattr(reason_code, "value", reason_code)
        try:
            rc_int = int(rc)
        except Exception:
            rc_int = -1
        if rc_int != 0:
            self._last_status = "failed"
            self._last_reason = f"connect_rc:{rc_int}"
            return
        for topic in self._timer_topics():
            client.subscribe(topic, qos=1)
        self._last_status = "connected"
        self._last_reason = None
        log.info("Timer announcement subscriber connected: topics=%s", ",".join(self._timer_topics()))

    def _on_disconnect(self, client: Any, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any = None) -> None:
        self._last_status = "disconnected"

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            self._last_reason = "invalid_json"
            return
        if not isinstance(payload, dict):
            self._last_reason = "invalid_payload"
            return
        loop = self._loop
        if loop is None:
            self._last_reason = "event_loop_unavailable"
            return
        self._ownership_cache.update_from_event(str(msg.topic), payload)
        alarm = timer_completed_alarm(
            str(msg.topic),
            payload,
            default_text=self._settings.voice_timer_completed_alarm_text,
        )
        if alarm is not None:
            if alarm.dedupe_key in self._seen_alarm_keys:
                self._last_reason = "duplicate_timer_completed_ignored"
                return
            self._seen_alarm_keys.insert(0, alarm.dedupe_key)
            del self._seen_alarm_keys[100:]
            self._last_alarm = {
                "endpoint_id": alarm.endpoint_id,
                "session_id": alarm.session_id,
                "timer_id": alarm.timer_id,
                "text": alarm.text,
                "event_id": alarm.event_id,
                "dedupe_key": alarm.dedupe_key,
                "topic": alarm.topic,
                "metadata": alarm.metadata,
                "status": "queued",
            }
            asyncio.run_coroutine_threadsafe(self._play_alarm_async(alarm), loop)
            return
        announcement = timer_success_announcement(str(msg.topic), payload)
        if announcement is None:
            return
        self._last_announcement = {
            "endpoint_id": announcement.endpoint_id,
            "session_id": announcement.session_id,
            "text": announcement.text,
            "event_id": announcement.event_id,
            "topic": announcement.topic,
        }
        asyncio.run_coroutine_threadsafe(self._announce_async(announcement), loop)

    async def _announce_async(self, announcement: TimerAnnouncement) -> None:
        try:
            result = self._announce(announcement)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            self._last_reason = "announcement_failed"
            log.warning("Timer announcement failed: error=%s", exc)

    async def _play_alarm_async(self, alarm: TimerCompletedAlarm) -> None:
        if self._play_alarm is None:
            self._last_reason = "timer_alarm_callback_unavailable"
            return
        try:
            result = self._play_alarm(alarm)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, dict):
                if self._last_alarm is not None and self._last_alarm.get("dedupe_key") == alarm.dedupe_key:
                    self._last_alarm["status"] = result.get("status") or ("accepted" if result.get("accepted") else "failed")
                    self._last_alarm["request_id"] = result.get("request_id")
                    self._last_alarm["reason"] = result.get("reason")
                if not result.get("accepted", False):
                    self._last_reason = str(result.get("reason") or "timer_alarm_rejected")
        except Exception as exc:
            self._last_reason = "timer_alarm_failed"
            if self._last_alarm is not None and self._last_alarm.get("dedupe_key") == alarm.dedupe_key:
                self._last_alarm["status"] = "failed"
                self._last_alarm["reason"] = "timer_alarm_failed"
            log.warning("Timer alarm playback failed: error=%s", exc)

    def _timer_topics(self) -> list[str]:
        topics = [
            self._settings.voice_timer_success_mqtt_topic,
            self._settings.voice_timer_status_mqtt_topic,
            self._settings.voice_timer_completed_mqtt_topic,
            "hexe/events/timer/+",
        ]
        deduped: list[str] = []
        for topic in topics:
            normalized = str(topic or "").strip()
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped


def _timer_state_from_event(event_type: str, data: dict[str, Any]) -> str:
    explicit = str(data.get("state") or "").strip().lower()
    if explicit:
        return explicit
    if event_type == "timer.completed":
        return "completed"
    if event_type.startswith("timer.cancel"):
        return "cancelled"
    if event_type.startswith("timer.stop"):
        return "stopped"
    if event_type.startswith(("timer.create", "timer.status", "timer.adjust_time")):
        return "active"
    return "unknown"


def _alarm_status_from_event(event_type: str, data: dict[str, Any]) -> str | None:
    explicit = str(data.get("alarm_status") or data.get("playback_state") or "").strip().lower()
    if explicit:
        return explicit
    if event_type == "timer.completed":
        return "pending"
    return None


def _is_active_timer_state(state: str) -> bool:
    return str(state or "").strip().lower() not in {
        "",
        "inactive",
        "cancelled",
        "canceled",
        "cleared",
        "completed",
        "done",
        "expired",
        "none",
        "not_found",
        "stopped",
        "unknown",
    }


def _optional_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_event_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
