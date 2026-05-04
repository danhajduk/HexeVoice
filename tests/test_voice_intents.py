from fastapi.testclient import TestClient

from hexevoice.assistant import LocalIntentFinder, VoiceIntentRegistry, VoiceIntentStateStore
from hexevoice.config.settings import Settings
from hexevoice.main import create_app


def test_voice_intent_registry_seeds_timer_and_persists_lifecycle(tmp_path):
    store = VoiceIntentStateStore(path=tmp_path / "voice_intents.json")
    registry = VoiceIntentRegistry(store=store)

    snapshot = registry.snapshot()

    assert snapshot["registered_count"] == 1
    assert snapshot["active_count"] == 1
    assert snapshot["intents"][0]["intent_id"] == "timer.create"

    registry.transition_intent(intent_id="timer.create", status="disabled", reason="unit_test")
    reloaded = VoiceIntentRegistry(store=VoiceIntentStateStore(path=tmp_path / "voice_intents.json"))

    assert reloaded.get_intent(intent_id="timer.create")["status"] == "disabled"
    assert reloaded.snapshot()["active_count"] == 0


def test_registered_intent_finder_uses_registry_and_can_disable_timer(tmp_path):
    registry = VoiceIntentRegistry(store=VoiceIntentStateStore(path=tmp_path / "voice_intents.json"))
    finder = LocalIntentFinder(registry=registry)

    assert finder.find("set a timer for 5 minutes").command == "timer.create"

    registry.transition_intent(intent_id="timer.create", status="disabled", reason="unit_test")

    assert finder.find("set a timer for 5 minutes") is None


def test_voice_intent_api_registers_custom_intent_and_dispatches(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    initial = client.get("/api/voice/intents")
    assert initial.status_code == 200
    assert initial.json()["intents"][0]["intent_id"] == "timer.create"

    registered = client.post(
        "/api/voice/intents",
        json={
            "intent_id": "kitchen.status",
            "intent_name": "Kitchen status",
            "definition": {
                "utterance_examples": ["kitchen status"],
                "dispatch": {"type": "local_response", "command": "kitchen.status"},
                "response": {"reply_text": "Kitchen status accepted."},
                "matcher": {"type": "exact_example"},
            },
        },
    )

    assert registered.status_code == 200
    assert registered.json()["registered_count"] == 2

    dispatch = client.post(
        "/api/voice/intents/dispatch",
        json={"endpoint_id": "box-1", "text": "kitchen status"},
    )

    assert dispatch.status_code == 200
    assert dispatch.json() == {
        "matched": True,
        "intent_id": "kitchen.status",
        "command": "kitchen.status",
        "slots": {},
        "reply_text": "Kitchen status accepted.",
        "provider_id": "registered_intent",
    }

    disabled = client.post(
        "/api/voice/intents/kitchen.status/lifecycle",
        json={"status": "disabled", "reason": "unit_test"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["active_count"] == 1

    dispatch_after_disable = client.post(
        "/api/voice/intents/dispatch",
        json={"endpoint_id": "box-1", "text": "kitchen status"},
    )
    assert dispatch_after_disable.status_code == 200
    assert dispatch_after_disable.json()["matched"] is False


def test_assistant_turn_uses_registered_timer_intent_in_app(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")))

    response = client.post(
        "/api/assistant/turn",
        json={"endpoint_id": "box-1", "text": "Hexa, set a timer for 2 minutes"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["handled_locally"] is True
    assert payload["command"] == "timer.create"
    assert payload["provider_id"] == "registered_intent"
    assert payload["spoken_text"] == "Setting timer for 2 minutes."
