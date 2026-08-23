from hexevoice.timer_announcements import timer_success_announcement


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
