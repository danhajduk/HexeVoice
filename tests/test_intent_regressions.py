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


def _registry_and_finder(tmp_path) -> tuple[VoiceIntentRegistry, LocalIntentFinder]:
    registry = VoiceIntentRegistry(store=VoiceIntentStateStore(path=tmp_path / "voice_intents.json"))
    return registry, LocalIntentFinder(registry=registry)


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


def test_short_registered_intents_are_ignored_without_followup_or_global_scope(tmp_path):
    registry, finder = _registry_and_finder(tmp_path)
    registry.register_intent(
        intent_id="debug.ok",
        intent_name="Debug OK",
        definition={
            "utterance_examples": ["ok"],
            "dispatch": {"type": "local_response", "command": "debug.ok"},
            "reply": {"text_template": "OK accepted."},
            "matcher": {"type": "exact_example"},
        },
    )
    registry.register_intent(
        intent_id="debug.stop",
        intent_name="Debug stop",
        definition={
            "utterance_examples": ["stop"],
            "dispatch": {"type": "local_response", "command": "debug.stop"},
            "reply": {"text_template": "Stop accepted."},
            "matcher": {"type": "exact_example"},
        },
    )

    assert finder.find("ok") is None
    assert finder.find("stop") is None


def test_short_registered_intents_can_be_followup_scoped(tmp_path):
    registry, finder = _registry_and_finder(tmp_path)
    registry.register_intent(
        intent_id="debug.ok",
        intent_name="Debug OK",
        definition={
            "utterance_examples": ["ok"],
            "dispatch": {"type": "local_response", "command": "debug.ok"},
            "reply": {"text_template": "OK accepted."},
            "matcher": {"type": "exact_example"},
        },
    )

    match = finder.find(
        "ok",
        pending_followup={
            "intent_id": "debug.test",
            "command": "debug.test",
            "prompt": "Continue?",
        },
    )

    assert match is not None
    assert match.command == "debug.ok"


def test_short_registered_intents_can_be_declared_global(tmp_path):
    registry, finder = _registry_and_finder(tmp_path)
    registry.register_intent(
        intent_id="debug.ok.global",
        intent_name="Global debug OK",
        constraints={"short_intent_scope": "global"},
        definition={
            "utterance_examples": ["ok"],
            "dispatch": {"type": "local_response", "command": "debug.ok.global"},
            "reply": {"text_template": "Global OK accepted."},
            "matcher": {"type": "exact_example"},
        },
    )

    match = finder.find("ok")

    assert match is not None
    assert match.command == "debug.ok.global"
