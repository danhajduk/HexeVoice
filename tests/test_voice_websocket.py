import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.voice import DeterministicWakeDetector, VoiceSessionManager


def voice_event(event_type, *, endpoint_id="esp-box-1", session_id="voice-session-1", payload=None):
    return {
        "event_type": event_type,
        "endpoint_id": endpoint_id,
        "direction": "endpoint_to_backend",
        "session_id": session_id,
        "payload": payload or {},
    }


def test_voice_websocket_starts_single_endpoint_session(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            voice_event(
                "session.start",
                payload={"wake_source": "openwakeword", "audio_format": {"sample_rate_hz": 16000}},
            )
        )
        response = websocket.receive_json()

    assert response["event_type"] == "session.state"
    assert response["direction"] == "backend_to_endpoint"
    assert response["endpoint_id"] == "esp-box-1"
    assert response["session_id"] == "voice-session-1"
    assert response["payload"]["snapshot"]["session_state"] == "idle"
    assert response["payload"]["snapshot"]["connection_state"] == "connected"
    assert response["payload"]["snapshot"]["ux_state"] == "wake_armed"


def test_voice_websocket_accepts_wake_audio_chunks_and_completion(tmp_path):
    manager = VoiceSessionManager(wake_detector=DeterministicWakeDetector(detect_on_chunk_index=0))
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json"), voice_session_manager=manager))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()

        websocket.send_json(
            voice_event(
                "audio.chunk",
                payload={
                    "chunk_index": 0,
                    "audio_format": {"encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1},
                    "payload_base64": "AAECAw==",
                },
            )
        )
        wake_response = websocket.receive_json()
        chunk_response = websocket.receive_json()
        websocket.send_json(voice_event("audio.end"))
        complete_response = websocket.receive_json()

    assert wake_response["event_type"] == "wake.accepted"
    assert wake_response["payload"]["snapshot"]["session_state"] == "wake_detected"
    assert wake_response["payload"]["wake"]["source"] == "backend_openwakeword"
    assert chunk_response["payload"]["snapshot"]["session_state"] == "capturing"
    assert chunk_response["payload"]["chunk_index"] == 0
    assert chunk_response["payload"]["chunk_count"] == 1
    assert chunk_response["payload"]["wake"]["detected"] is True
    assert complete_response["event_type"] == "session.completed"
    assert complete_response["payload"]["snapshot"]["session_state"] == "completed"
    assert complete_response["payload"]["completion_reason"] == "turn_completed"


def test_voice_websocket_runs_transcript_assistant_and_tts_pipeline(tmp_path):
    client = TestClient(
        create_app(
            Settings(onboarding_state_path=tmp_path / "state.json", node_name="lab-voice"),
            voice_wake_detector=DeterministicWakeDetector(detect_on_chunk_index=0),
        )
    )

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()
        websocket.send_json(
            voice_event(
                "audio.chunk",
                payload={"chunk_index": 0, "audio_format": {"sample_rate_hz": 16000}},
            )
        )
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json(voice_event("audio.end"))
        transcript = websocket.receive_json()
        response_text = websocket.receive_json()
        tts_ready = websocket.receive_json()
        completed = websocket.receive_json()

    assert transcript["event_type"] == "transcript.final"
    assert transcript["payload"]["text"] == "hello"
    assert response_text["event_type"] == "response.text"
    assert "Hello from lab-voice" in response_text["payload"]["text"]
    assert tts_ready["event_type"] == "tts.ready"
    assert tts_ready["payload"]["content_type"] == "audio/wav"
    assert tts_ready["payload"]["stream_id"].startswith("tts-")
    assert completed["event_type"] == "session.completed"
    assert completed["payload"]["snapshot"]["session_state"] == "completed"

    status = client.get("/api/voice/status")
    assert status.status_code == 200
    assert status.json()["last_transcript"] == "hello"
    assert "Hello from lab-voice" in status.json()["last_response"]
    assert status.json()["last_tts"]["stream_id"].startswith("tts-")


def test_voice_status_and_operator_cancel_surface_active_session(tmp_path):
    client = TestClient(
        create_app(
            Settings(onboarding_state_path=tmp_path / "state.json"),
            voice_wake_detector=DeterministicWakeDetector(detect_on_chunk_index=0),
        )
    )

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()

        status = client.get("/api/voice/status")
        assert status.status_code == 200
        assert status.json()["connection_state"] == "connected"
        assert status.json()["active_session"]["session_state"] == "idle"
        assert status.json()["supported_actions"]["stop_session"] is True

        cancel = client.post("/api/voice/session/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["accepted"] is True
        assert cancel.json()["status"]["active_session"] is None


def test_voice_websocket_cancels_audio_end_when_wake_was_not_detected(tmp_path):
    manager = VoiceSessionManager(wake_detector=DeterministicWakeDetector(detect_on_chunk_index=None))
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json"), voice_session_manager=manager))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()
        websocket.send_json(
            voice_event(
                "audio.chunk",
                payload={"chunk_index": 0, "audio_format": {"sample_rate_hz": 16000}},
            )
        )
        chunk_response = websocket.receive_json()
        websocket.send_json(voice_event("audio.end"))
        end_response = websocket.receive_json()

    assert chunk_response["payload"]["snapshot"]["session_state"] == "idle"
    assert chunk_response["payload"]["wake"]["detected"] is False
    assert end_response["event_type"] == "session.cancelled"
    assert end_response["payload"]["snapshot"]["cancel_reason"] == "wake_not_detected"


def test_voice_websocket_cancel_returns_cancelled_event(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()
        websocket.send_json(voice_event("session.cancel", payload={"reason": "button"}))
        response = websocket.receive_json()

    assert response["event_type"] == "session.cancelled"
    assert response["payload"]["snapshot"]["session_state"] == "cancelled"
    assert response["payload"]["snapshot"]["cancel_reason"] == "button"


def test_voice_websocket_rejects_invalid_event_envelope(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json({"event_type": "audio.chunk", "endpoint_id": "esp-box-1"})
        response = websocket.receive_json()

    assert response["event_type"] == "session.error"
    assert response["payload"]["code"] == "invalid_event_envelope"
    assert response["payload"]["recoverable"] is True


def test_voice_websocket_rejects_audio_without_active_session(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            voice_event(
                "audio.chunk",
                payload={"chunk_index": 0, "audio_format": {"sample_rate_hz": 16000}},
            )
        )
        response = websocket.receive_json()

    assert response["event_type"] == "session.error"
    assert response["payload"]["code"] == "no_active_session"


def test_voice_websocket_allows_only_one_connected_endpoint(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws"):
        with client.websocket_connect("/api/voice/ws") as second_socket:
            response = second_socket.receive_json()
            assert response["event_type"] == "session.error"
            assert response["payload"]["code"] == "endpoint_busy"
            with pytest.raises(WebSocketDisconnect):
                second_socket.receive_json()
