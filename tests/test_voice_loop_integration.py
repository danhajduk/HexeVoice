from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.voice import DeterministicWakeDetector


def event(event_type, *, session_id="esp-box-1-1", payload=None):
    return {
        "event_type": event_type,
        "endpoint_id": "esp-box-1",
        "direction": "endpoint_to_backend",
        "session_id": session_id,
        "payload": payload or {},
    }


def test_single_endpoint_wake_to_reply_loop_updates_backend_and_dashboard_status(tmp_path):
    client = TestClient(
        create_app(
            Settings(onboarding_state_path=tmp_path / "state.json", node_name="lab-voice"),
            voice_wake_detector=DeterministicWakeDetector(detect_on_chunk_index=0),
        )
    )

    heartbeat = client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "device_state": "idle",
            "firmware_version": "0.1.0",
            "ip_address": "10.0.0.55",
            "rssi_dbm": -58,
        },
    )
    assert heartbeat.status_code == 200

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            event(
                "session.start",
                payload={
                    "firmware_version": "0.1.0",
                    "wake_source": "unknown",
                    "audio_format": {"encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1},
                },
            )
        )
        started = websocket.receive_json()

        websocket.send_json(
            event(
                "audio.chunk",
                payload={
                    "chunk_index": 0,
                    "audio_format": {"encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1},
                    "payload_base64": "V0FLRQ==",
                },
            )
        )
        wake = websocket.receive_json()
        capturing = websocket.receive_json()

        websocket.send_json(event("audio.end", payload={"reason": "vad_silence"}))
        transcript = websocket.receive_json()
        response = websocket.receive_json()
        tts = websocket.receive_json()
        completed = websocket.receive_json()

    assert started["payload"]["snapshot"]["ux_state"] == "wake_armed"
    assert wake["event_type"] == "wake.accepted"
    assert wake["payload"]["wake"]["source"] == "backend_openwakeword"
    assert capturing["payload"]["snapshot"]["session_state"] == "capturing"
    assert transcript["event_type"] == "transcript.final"
    assert transcript["payload"]["text"] == "hello"
    assert response["event_type"] == "response.text"
    assert "Hello from lab-voice" in response["payload"]["text"]
    assert tts["event_type"] == "tts.ready"
    assert tts["payload"]["stream_id"].startswith("tts-")
    assert completed["event_type"] == "session.completed"
    assert completed["payload"]["snapshot"]["session_state"] == "completed"

    voice_status = client.get("/api/voice/status")
    assert voice_status.status_code == 200
    voice_payload = voice_status.json()
    assert voice_payload["last_transcript"] == "hello"
    assert "Hello from lab-voice" in voice_payload["last_response"]
    assert voice_payload["last_error"] is None
    assert voice_payload["supported_actions"]["test_assistant_turn"] is True

    endpoint_status = client.get("/api/endpoint/status/esp-box-1")
    assert endpoint_status.status_code == 200
    assert endpoint_status.json()["firmware_version"] == "0.1.0"


def test_single_endpoint_cancel_path_updates_voice_status(tmp_path):
    client = TestClient(
        create_app(
            Settings(onboarding_state_path=tmp_path / "state.json"),
            voice_wake_detector=DeterministicWakeDetector(detect_on_chunk_index=0),
        )
    )

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(event("session.start"))
        websocket.receive_json()

        cancel = client.post("/api/voice/session/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["accepted"] is True

        status = client.get("/api/voice/status")
        assert status.json()["active_session"] is None
        assert status.json()["supported_actions"]["stop_session"] is False
