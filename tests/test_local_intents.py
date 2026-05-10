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


def test_local_intent_finder_ignores_non_timer_text():
    finder = LocalIntentFinder()

    assert finder.find("what is the weather") is None
    assert finder.find("timer") is None
