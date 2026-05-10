from datetime import UTC, datetime, timedelta
import json
import os

from fastapi.testclient import TestClient

from hexevoice.assistant import LocalIntentFinder, VoiceIntentRegistry, VoiceIntentStateStore
from hexevoice.assistant.intents import _format_clock_time
from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.tts.service import TtsAudioService


def test_voice_intent_registry_seeds_voice_node_builtins_and_persists_lifecycle(tmp_path):
    store = VoiceIntentStateStore(path=tmp_path / "voice_intents.json")
    registry = VoiceIntentRegistry(store=store)

    snapshot = registry.snapshot()

    assert snapshot["registered_count"] == 4
    assert snapshot["active_count"] == 4
    assert snapshot["intents"][0]["intent_id"] == "timer.create"
    assert snapshot["intents"][1]["intent_id"] == "voice.time.query"
    assert snapshot["intents"][1]["owner_service"] == "hexevoice"
    assert snapshot["intents"][1]["metadata"]["owned_by"] == "voice_node"
    assert snapshot["intents"][2]["intent_id"] == "voice.confirm.yes"
    assert snapshot["intents"][3]["intent_id"] == "voice.confirm.no"

    registry.transition_intent(intent_id="timer.create", status="disabled", reason="unit_test")
    reloaded = VoiceIntentRegistry(store=VoiceIntentStateStore(path=tmp_path / "voice_intents.json"))

    assert reloaded.get_intent(intent_id="timer.create")["status"] == "disabled"
    assert reloaded.snapshot()["active_count"] == 3


def test_registered_intent_finder_uses_registry_and_can_disable_timer(tmp_path):
    registry = VoiceIntentRegistry(store=VoiceIntentStateStore(path=tmp_path / "voice_intents.json"))
    finder = LocalIntentFinder(registry=registry)

    assert finder.find("set a timer for 5 minutes").command == "timer.create"
    assert finder.find("Five minutes timer.").slots["duration_seconds"] == 300

    registry.transition_intent(intent_id="timer.create", status="disabled", reason="unit_test")

    assert finder.find("set a timer for 5 minutes") is None


def test_registered_intent_finder_answers_voice_node_time_query(tmp_path):
    registry = VoiceIntentRegistry(store=VoiceIntentStateStore(path=tmp_path / "voice_intents.json"))
    finder = LocalIntentFinder(registry=registry)
    requested_at = datetime(2026, 5, 9, 18, 34, tzinfo=UTC)

    match = finder.find("What is the time?", requested_at=requested_at)

    assert match.command == "voice.time.query"
    assert match.provider_id == "registered_intent"
    assert match.slots["time_text"]
    assert match.slots["requested_at"] == requested_at.isoformat()
    assert match.reply_text.startswith("It is ")


def test_confirmation_intents_require_pending_followup(tmp_path):
    registry = VoiceIntentRegistry(store=VoiceIntentStateStore(path=tmp_path / "voice_intents.json"))
    finder = LocalIntentFinder(registry=registry)
    requested_at = datetime(2026, 5, 9, 18, 34, tzinfo=UTC)

    assert finder.find("yes", requested_at=requested_at) is None

    match = finder.find(
        "yes",
        requested_at=requested_at,
        pending_followup={
            "intent_id": "debug.delete_cache",
            "command": "debug.delete_cache",
            "prompt": "Delete cache?",
            "yes_reply_text": "Deleting cache.",
            "no_reply_text": "Leaving cache alone.",
        },
    )

    assert match.command == "voice.confirm.yes"
    assert match.reply_text == "Deleting cache."
    assert match.slots["response"] == "yes"
    assert match.slots["pending_intent_id"] == "debug.delete_cache"


def test_time_query_formats_clock_for_tts():
    assert _format_clock_time(datetime(2026, 5, 9, 16, 5, tzinfo=UTC)) == "four oh five PM"
    assert _format_clock_time(datetime(2026, 5, 9, 16, 34, tzinfo=UTC)) == "four thirty four PM"
    assert _format_clock_time(datetime(2026, 5, 9, 16, 0, tzinfo=UTC)) == "four PM"


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
    assert registered.json()["registered_count"] == 5

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
    assert disabled.json()["active_count"] == 4

    dispatch_after_disable = client.post(
        "/api/voice/intents/dispatch",
        json={"endpoint_id": "box-1", "text": "kitchen status"},
    )
    assert dispatch_after_disable.status_code == 200
    assert dispatch_after_disable.json()["matched"] is False


def test_voice_intent_registration_rejects_invalid_extraction_contract(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    response = client.post(
        "/api/voice/intents",
        json={
            "intent_id": "bad.intent",
            "definition": {
                "extraction": {
                    "required": ["not", "an", "object"],
                },
            },
        },
    )

    assert response.status_code == 400
    assert "intent_extraction_required_must_be_object" in response.json()["detail"]


def test_voice_intent_invoke_executes_real_path_and_reports_events(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    response = client.post(
        "/api/voice/intents/invoke",
        json={"endpoint_id": "box-1", "text": "Five minutes timer."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is True
    assert payload["intent_id"] == "timer.create"
    assert payload["slots"]["duration_seconds"] == 300
    assert payload["slots"]["duration_hhmmss"] == "00:05:00"
    assert payload["recognized_event_id"].startswith("voice-intent-")
    assert payload["recognition_event"]["event_type"] == "voice.intent.recognized"
    assert payload["dispatch_event"]["event_type"] == "timer.create_requested"
    assert payload["latency_ms"] >= 0


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
    assert payload["intent_latency_ms"] >= 0


def test_assistant_turn_answers_voice_node_time_intent_in_app(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")))

    response = client.post(
        "/api/assistant/turn",
        json={"endpoint_id": "box-1", "text": "Hexa, what is the time?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["handled_locally"] is True
    assert payload["command"] == "voice.time.query"
    assert payload["provider_id"] == "registered_intent"
    assert payload["spoken_text"].startswith("It is ")
    assert payload["intent_latency_ms"] >= 0


def test_assistant_turn_supports_yes_no_followup_context(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")))
    client.post(
        "/api/voice/intents",
        json={
            "intent_id": "debug.delete_cache",
            "intent_name": "Delete cache",
            "definition": {
                "utterance_examples": ["delete cache"],
                "dispatch": {"type": "local_response", "command": "debug.delete_cache"},
                "reply": {"text_template": "Delete cache?"},
                "followup": {
                    "required": True,
                    "prompt": "Delete cache?",
                    "yes_reply_text": "Deleting cache.",
                    "no_reply_text": "Leaving cache alone.",
                    "ttl_seconds": 30,
                },
                "matcher": {"type": "exact_example"},
            },
        },
    )

    prompt = client.post(
        "/api/assistant/turn",
        json={"endpoint_id": "box-1", "text": "Hexa, delete cache"},
    )
    assert prompt.status_code == 200
    prompt_payload = prompt.json()
    assert prompt_payload["command"] == "debug.delete_cache"
    assert prompt_payload["spoken_text"] == "Delete cache?"
    assert prompt_payload["conversation_followup"]["intent_id"] == "debug.delete_cache"

    confirmation = client.post(
        "/api/assistant/turn",
        json={"endpoint_id": "box-1", "text": "yes"},
    )
    assert confirmation.status_code == 200
    confirmation_payload = confirmation.json()
    assert confirmation_payload["handled_locally"] is True
    assert confirmation_payload["command"] == "voice.confirm.yes"
    assert confirmation_payload["spoken_text"] == "Deleting cache."

    stale_yes = client.post(
        "/api/assistant/turn",
        json={"endpoint_id": "box-1", "text": "yes"},
    )
    assert stale_yes.status_code == 200
    assert stale_yes.json()["handled_locally"] is False


def test_intent_invoke_can_create_event_named_reply_audio_sidecar(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", runtime_dir=tmp_path)
    client = TestClient(create_app(settings))
    client.post(
        "/api/voice/intents",
        json={
            "intent_id": "kitchen.status",
            "intent_name": "Kitchen status",
            "definition": {
                "utterance_examples": ["kitchen status"],
                "dispatch": {"type": "local_response", "command": "kitchen.status"},
                "response": {"reply_text": "Kitchen status accepted."},
                "reply": {
                    "text_template": "Kitchen status accepted.",
                    "audio": {"mode": "best_effort", "ttl_seconds": 120},
                },
                "matcher": {"type": "exact_example"},
            },
        },
    )

    response = client.post(
        "/api/voice/intents/invoke",
        json={"endpoint_id": "box-1", "text": "kitchen status"},
    )

    assert response.status_code == 200
    payload = response.json()
    event_id = payload["recognized_event_id"]
    assert payload["reply_audio"]["stream_id"] == event_id
    assert payload["reply_audio"]["voice_ready"] is True
    assert payload["reply_audio"]["spoken_text"] == "Kitchen status accepted."
    assert payload["reply_audio"]["transcript"]["text"] == "kitchen status"
    assert payload["reply_audio"]["ttl_seconds"] == 120
    sidecar = tmp_path / "voice_tts" / f"{event_id}.json"
    audio = tmp_path / "voice_tts" / f"{event_id}.wav"
    assert audio.exists()
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    assert metadata["voice_ready"] is True
    assert metadata["spoken_text"] == "Kitchen status accepted."
    assert metadata["transcript"]["text"] == "kitchen status"
    assert metadata["model_id"] == "deterministic"
    assert metadata["ttl_seconds"] == 120
    assert metadata["expires_at"]


def test_intent_reply_audio_can_be_long_lived(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", runtime_dir=tmp_path)
    client = TestClient(create_app(settings))
    client.post(
        "/api/voice/intents",
        json={
            "intent_id": "timer.cancel",
            "intent_name": "Cancel timer",
            "definition": {
                "utterance_examples": ["cancel timer"],
                "dispatch": {"type": "local_response", "command": "timer.cancel"},
                "reply": {
                    "text_template": "Timer cancelled.",
                    "audio": {"mode": "best_effort", "lifetime": "long_lived"},
                },
                "matcher": {"type": "exact_example"},
            },
        },
    )

    response = client.post(
        "/api/voice/intents/invoke",
        json={"endpoint_id": "box-1", "text": "cancel timer"},
    )

    assert response.status_code == 200
    payload = response.json()
    event_id = payload["recognized_event_id"]
    assert payload["reply_audio"]["lifetime"] == "long_lived"
    assert payload["reply_audio"]["ttl_seconds"] is None
    assert payload["reply_audio"]["expires_at"] is None

    service = TtsAudioService(settings=settings, voice_turn_pipeline=None)  # type: ignore[arg-type]
    service.cleanup_expired()

    assert (tmp_path / "voice_tts" / f"{event_id}.wav").exists()
    metadata = json.loads((tmp_path / "voice_tts" / f"{event_id}.json").read_text(encoding="utf-8"))
    assert metadata["spoken_text"] == "Timer cancelled."
    assert metadata["lifetime"] == "long_lived"
    assert metadata["expires_at"] is None


def test_tts_audio_cleanup_removes_expired_sidecar_and_audio(tmp_path):
    service = TtsAudioService(settings=Settings(runtime_dir=tmp_path), voice_turn_pipeline=None)  # type: ignore[arg-type]
    tts_dir = tmp_path / "voice_tts"
    tts_dir.mkdir()
    (tts_dir / "voice-intent-expired.wav").write_bytes(b"RIFFexpired")
    (tts_dir / "voice-intent-expired.json").write_text(
        json.dumps({"expires_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (tts_dir / "voice-intent-live.wav").write_bytes(b"RIFFlive")
    (tts_dir / "voice-intent-live.json").write_text(
        json.dumps({"expires_at": "2999-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )

    service.cleanup_expired()

    assert not (tts_dir / "voice-intent-expired.wav").exists()
    assert not (tts_dir / "voice-intent-expired.json").exists()
    assert (tts_dir / "voice-intent-live.wav").exists()
    assert (tts_dir / "voice-intent-live.json").exists()


def test_tts_audio_cleanup_removes_expired_sidecar_and_all_variants(tmp_path):
    service = TtsAudioService(settings=Settings(runtime_dir=tmp_path), voice_turn_pipeline=None)  # type: ignore[arg-type]
    tts_dir = tmp_path / "voice_tts"
    tts_dir.mkdir()
    for suffix in (".wav", ".raw.wav", ".16k.wav", ".22050.wav", ".48k.wav", ".mp3"):
        (tts_dir / f"voice-intent-expired{suffix}").write_bytes(b"expired")
    (tts_dir / "voice-intent-expired.json").write_text(
        json.dumps({"expires_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )

    service.cleanup_expired(now=datetime(2026, 5, 9, tzinfo=UTC))

    assert not list(tts_dir.glob("voice-intent-expired*"))


def test_tts_artifact_listing_handles_legacy_sidecar_without_variant_metadata(tmp_path):
    service = TtsAudioService(settings=Settings(runtime_dir=tmp_path), voice_turn_pipeline=None)  # type: ignore[arg-type]
    tts_dir = tmp_path / "voice_tts"
    tts_dir.mkdir()
    (tts_dir / "legacy-tts.wav").write_bytes(b"RIFFlegacy")
    (tts_dir / "legacy-tts.json").write_text(
        json.dumps(
            {
                "stream_id": "legacy-tts",
                "content_type": "audio/wav",
                "expires_at": "2999-01-01T00:00:00+00:00",
                "audio_url": "/api/voice/tts/legacy-tts",
            }
        ),
        encoding="utf-8",
    )

    listing = service.list_artifacts()
    metadata = service.metadata("legacy-tts")

    assert listing["count"] == 1
    artifact = listing["artifacts"][0]
    assert artifact["stream_id"] == "legacy-tts"
    assert artifact["audio_url"] == "/api/voice/tts/legacy-tts"
    assert artifact["playable_urls"]["default"].endswith("/api/tts/audio/legacy-tts/")
    assert artifact["file_sizes"]["default"] == len(b"RIFFlegacy")
    assert metadata is not None
    assert metadata["stream_id"] == "legacy-tts"
    assert metadata["voice_ready"] is True
    assert metadata["audio_variant"] == "default"
    assert metadata["endpoint_audio_url"].endswith("/api/tts/audio/legacy-tts/")


def test_tts_audio_path_prefers_variant_artifacts_over_raw_sidecar(tmp_path):
    service = TtsAudioService(settings=Settings(runtime_dir=tmp_path), voice_turn_pipeline=None)  # type: ignore[arg-type]
    tts_dir = tmp_path / "voice_tts"
    tts_dir.mkdir()
    raw_audio = tts_dir / "voice-intent-1.raw.wav"
    playback_48k = tts_dir / "voice-intent-1.48k.wav"
    playback_16k = tts_dir / "voice-intent-1.16k.wav"
    raw_audio.write_bytes(b"RIFFraw")
    playback_48k.write_bytes(b"RIFF48k")
    playback_16k.write_bytes(b"RIFF16k")

    assert service.audio_path("voice-intent-1") == playback_48k
    assert service.audio_path("voice-intent-1", variant="16k") == playback_16k
    assert service.audio_path("voice-intent-1", variant="raw") == raw_audio


def test_tts_orphan_cleanup_removes_old_audio_without_sidecar(tmp_path):
    service = TtsAudioService(settings=Settings(runtime_dir=tmp_path), voice_turn_pipeline=None)  # type: ignore[arg-type]
    tts_dir = tmp_path / "voice_tts"
    tts_dir.mkdir()
    orphan = tts_dir / "orphan.wav"
    paired_audio = tts_dir / "paired.wav"
    paired_raw_audio = tts_dir / "paired.raw.wav"
    paired_48k_audio = tts_dir / "paired.48k.wav"
    paired_16k_audio = tts_dir / "paired.16k.wav"
    fresh_orphan = tts_dir / "fresh.wav"
    orphan.write_bytes(b"RIFForphan")
    paired_audio.write_bytes(b"RIFFpaired")
    paired_raw_audio.write_bytes(b"RIFFpairedraw")
    paired_48k_audio.write_bytes(b"RIFFpaired48k")
    paired_16k_audio.write_bytes(b"RIFFpaired16k")
    fresh_orphan.write_bytes(b"RIFFfresh")
    (tts_dir / "paired.json").write_text(
        json.dumps({"expires_at": "2999-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    old_timestamp = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(orphan, (old_timestamp, old_timestamp))
    os.utime(paired_audio, (old_timestamp, old_timestamp))
    os.utime(paired_raw_audio, (old_timestamp, old_timestamp))
    os.utime(paired_48k_audio, (old_timestamp, old_timestamp))
    os.utime(paired_16k_audio, (old_timestamp, old_timestamp))

    deleted_count = service.cleanup_orphaned_audio(min_age_seconds=600)

    assert deleted_count == 1
    assert not orphan.exists()
    assert paired_audio.exists()
    assert paired_raw_audio.exists()
    assert paired_48k_audio.exists()
    assert paired_16k_audio.exists()
    assert fresh_orphan.exists()
