from datetime import datetime

from hexevoice.assistant import LocalIntentFinder


def test_local_intent_finder_detects_timer_create_with_digits():
    finder = LocalIntentFinder()

    match = finder.find("create a timer for 5 minutes")

    assert match is not None
    assert match.intent == "timer.create"
    assert match.command == "timer.create"
    assert match.slots["duration_seconds"] == 300
    assert match.slots["duration_text"] == "5 minutes"
    assert match.slots["duration_hhmmss"] == "00:05:00"
    assert datetime.fromisoformat(match.slots["requested_at"])
    assert match.reply_text == "Setting timer for 5 minutes."


def test_local_intent_finder_detects_timer_create_with_words_and_compound_duration():
    finder = LocalIntentFinder()

    match = finder.find("please set a one hour and thirty minute timer")

    assert match is not None
    assert match.slots["duration_seconds"] == 5400
    assert match.slots["duration_text"] == "1 hour and 30 minutes"


def test_local_intent_finder_detects_timer_status_query():
    finder = LocalIntentFinder()

    match = finder.find("how much time is left on the timer")

    assert match is not None
    assert match.intent == "timer.status"
    assert match.command == "timer.status"
    assert match.slots["scope"] == "active_for_endpoint"
    assert datetime.fromisoformat(match.slots["requested_at"])
    assert match.reply_text == "Checking the timer."


def test_local_intent_finder_detects_timer_stop_and_cancel():
    finder = LocalIntentFinder()

    stop = finder.find("stop the timer")
    cancel = finder.find("cancel my timer")

    assert stop is not None
    assert stop.intent == "timer.stop"
    assert stop.command == "timer.stop"
    assert stop.slots["action"] == "stop"
    assert stop.slots["scope"] == "active_for_endpoint"
    assert stop.reply_text == "Stopping the timer."
    assert cancel is not None
    assert cancel.intent == "timer.cancel"
    assert cancel.command == "timer.cancel"
    assert cancel.slots["action"] == "cancel"
    assert cancel.reply_text == "Cancelling the timer."


def test_local_intent_finder_ignores_non_timer_text():
    finder = LocalIntentFinder()

    assert finder.find("what is the weather") is None
    assert finder.find("timer") is None
