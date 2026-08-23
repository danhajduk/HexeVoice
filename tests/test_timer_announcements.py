import asyncio
import json
from types import SimpleNamespace

from hexevoice.config.settings import Settings
from hexevoice.timer_announcements import TimerSucceededAnnouncementService, timer_completed_alarm, timer_success_announcement


def test_timer_success_announcement_uses_success_payload_title():
    announcement = timer_success_announcement(
        "hexe/events/timer/create_succeeded",
        {
            "event_id": "interaction-timer-create-succeeded-session-1",
            "event_type": "timer.create_succeeded",
            "subject": {
                "family": "timer",
                "record_id": "session-1",
            },
            "data": {
                "endpoint_id": "esp-box-1",
                "title": "1 hour and 30 minutes",
                "duration_seconds": 5400,
                "duration_hhmmss": "01:30:00",
            },
        },
    )

    assert announcement is not None
    assert announcement.endpoint_id == "esp-box-1"
    assert announcement.session_id == "session-1"
    assert announcement.text == "Timer is on for 1 hour and 30 minutes."
    assert announcement.event_id == "interaction-timer-create-succeeded-session-1"


def test_timer_status_announcement_uses_remaining_text():
    announcement = timer_success_announcement(
        "hexe/events/timer/status_succeeded",
        {
            "event_id": "timer-status-succeeded-session-2",
            "event_type": "timer.status_succeeded",
            "subject": {
                "family": "timer",
                "record_id": "timer-123",
            },
            "data": {
                "endpoint_id": "esp-box-1",
                "session_id": "session-2",
                "timer_id": "timer-123",
                "state": "active",
                "remaining_seconds": 85,
                "remaining_hhmmss": "00:01:25",
                "remaining_text": "1 minute and 25 seconds",
            },
        },
    )

    assert announcement is not None
    assert announcement.endpoint_id == "esp-box-1"
    assert announcement.session_id == "session-2"
    assert announcement.text == "1 minute and 25 seconds left on the timer."
    assert announcement.event_id == "timer-status-succeeded-session-2"


def test_timer_status_announcement_reports_no_active_timer():
    announcement = timer_success_announcement(
        "hexe/events/timer/status_succeeded",
        {
            "event_id": "timer-status-succeeded-session-3",
            "event_type": "timer.status_succeeded",
            "data": {
                "endpoint_id": "esp-box-1",
                "session_id": "session-3",
                "state": "inactive",
            },
        },
    )

    assert announcement is not None
    assert announcement.text == "No active timer."


def test_timer_success_announcement_ignores_non_success_events():
    assert timer_success_announcement("hexe/events/timer/create_requested", {"event_type": "timer.create_requested"}) is None


def test_timer_completed_alarm_uses_promoted_event_endpoint_and_dedupe_key():
    alarm = timer_completed_alarm(
        "hexe/events/timer/completed",
        {
            "event_id": "interaction-timer-completed-timer-1",
            "event_type": "timer.completed",
            "promoted_event_type": "timer.completed",
            "source": {
                "node_id": "node-timer",
                "component": "hexe.timer",
                "topic": "hexe/nodes/node-timer/events/timer/completed",
            },
            "subject": {
                "family": "timer",
                "record_id": "timer-1",
            },
            "routing": {
                "domain_topic": "hexe/events/timer/completed",
                "dedupe_key": "node-timer|timer-1",
            },
            "data": {
                "endpoint_id": "esp-pe-1",
                "device_id": "esp-pe-1",
                "timer_id": "timer-1",
                "title": "20 seconds",
                "duration_seconds": 20,
                "started_at": "2026-08-23T22:35:38+00:00",
                "due_at": "2026-08-23T22:35:58+00:00",
                "completed_at": "2026-08-23T22:35:59.032117+00:00",
            },
        },
    )

    assert alarm is not None
    assert alarm.endpoint_id == "esp-pe-1"
    assert alarm.session_id == "timer-completed-timer-1"
    assert alarm.timer_id == "timer-1"
    assert alarm.text == "20 seconds timer is done."
    assert alarm.event_id == "interaction-timer-completed-timer-1"
    assert alarm.dedupe_key == "node-timer|timer-1"
    assert alarm.metadata["source"] == "timer.completed"
    assert alarm.metadata["source_node_id"] == "node-timer"
    assert alarm.metadata["duration_seconds"] == 20


def test_timer_completed_alarm_requires_target_endpoint():
    assert timer_completed_alarm("hexe/events/timer/completed", {"event_type": "timer.completed", "data": {}}) is None


def test_timer_service_queues_timer_completed_alarm_once():
    async def run() -> None:
        calls = []

        async def play_alarm(alarm):
            calls.append(alarm)
            return {"accepted": True, "request_id": "alarm-1", "status": "sent"}

        service = TimerSucceededAnnouncementService(
            settings=Settings(),
            announce=lambda announcement: None,
            play_alarm=play_alarm,
        )
        service._loop = asyncio.get_running_loop()
        payload = {
            "event_id": "timer-completed-1",
            "event_type": "timer.completed",
            "subject": {"family": "timer", "record_id": "timer-1"},
            "routing": {"dedupe_key": "timer-completed-once"},
            "data": {"endpoint_id": "esp-box-1", "timer_id": "timer-1"},
        }
        msg = SimpleNamespace(topic="hexe/events/timer/completed", payload=json.dumps(payload).encode("utf-8"))

        service._on_message(None, None, msg)
        await asyncio.sleep(0.01)
        service._on_message(None, None, msg)
        await asyncio.sleep(0.01)

        assert len(calls) == 1
        assert service.status()["last_alarm"]["endpoint_id"] == "esp-box-1"
        assert service.status()["last_alarm"]["status"] == "sent"
        assert service.status()["last_alarm"]["request_id"] == "alarm-1"
        assert service.status()["reason"] == "duplicate_timer_completed_ignored"

    asyncio.run(run())
