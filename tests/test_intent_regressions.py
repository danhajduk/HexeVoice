from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from hexevoice.assistant import LocalIntentFinder, VoiceIntentRegistry, VoiceIntentStateStore


@dataclass(frozen=True)
class IntentCase:
    text: str
    command: str | None
    slots: dict[str, object] | None = None
    pending_followup: dict[str, object] | None = None


def _finder(tmp_path) -> LocalIntentFinder:
    registry = VoiceIntentRegistry(store=VoiceIntentStateStore(path=tmp_path / "voice_intents.json"))
    return LocalIntentFinder(registry=registry)


@pytest.mark.parametrize(
    "case",
    [
        IntentCase("set a timer for 5 minutes", "timer.create", {"duration_seconds": 300}),
        IntentCase("Five minutes timer.", "timer.create", {"duration_hhmmss": "00:05:00"}),
        IntentCase("What is the time?", "voice.time.query"),
        IntentCase("test follow up", "voice.debug.followup"),
        IntentCase("yes", None),
        IntentCase(
            "yes",
            "voice.confirm.yes",
            {"response": "yes", "pending_intent_id": "debug.test"},
            {
                "intent_id": "debug.test",
                "command": "debug.test",
                "prompt": "Continue?",
                "yes_reply_text": "Continuing.",
                "no_reply_text": "Stopping.",
            },
        ),
        IntentCase(
            "no",
            "voice.confirm.no",
            {"response": "no", "pending_intent_id": "debug.test"},
            {
                "intent_id": "debug.test",
                "command": "debug.test",
                "prompt": "Continue?",
                "yes_reply_text": "Continuing.",
                "no_reply_text": "Stopping.",
            },
        ),
    ],
)
def test_transcript_to_intent_regressions(tmp_path, case: IntentCase):
    finder = _finder(tmp_path)
    requested_at = datetime(2026, 5, 9, 18, 34, tzinfo=UTC)

    match = finder.find(case.text, requested_at=requested_at, pending_followup=case.pending_followup)

    if case.command is None:
        assert match is None
        return

    assert match is not None
    assert match.command == case.command
    for key, value in (case.slots or {}).items():
        assert match.slots[key] == value
