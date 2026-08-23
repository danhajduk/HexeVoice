from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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


class TimerSucceededAnnouncementService:
    def __init__(
        self,
        *,
        settings: Settings,
        announce: Callable[[TimerAnnouncement], Awaitable[dict[str, Any]] | dict[str, Any]],
        onboarding_state_store: OnboardingStateStore | None = None,
    ) -> None:
        self._settings = settings
        self._announce = announce
        self._store = onboarding_state_store or OnboardingStateStore(path=settings.resolved_onboarding_state_path())
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._last_status = "stopped"
        self._last_reason: str | None = None
        self._last_announcement: dict[str, Any] | None = None

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
        loop = self._loop
        if loop is None:
            self._last_reason = "event_loop_unavailable"
            return
        asyncio.run_coroutine_threadsafe(self._announce_async(announcement), loop)

    async def _announce_async(self, announcement: TimerAnnouncement) -> None:
        try:
            result = self._announce(announcement)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            self._last_reason = "announcement_failed"
            log.warning("Timer announcement failed: error=%s", exc)

    def _timer_topics(self) -> list[str]:
        topics = [
            self._settings.voice_timer_success_mqtt_topic,
            self._settings.voice_timer_status_mqtt_topic,
        ]
        deduped: list[str] = []
        for topic in topics:
            normalized = str(topic or "").strip()
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped
