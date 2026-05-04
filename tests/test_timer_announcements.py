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


def test_timer_success_announcement_ignores_non_success_events():
    assert timer_success_announcement("hexe/events/timer/create_requested", {"event_type": "timer.create_requested"}) is None
