import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.api.models import AssistantTurnResponse
from hexevoice.voice import (
    DeterministicWakeDetector,
    PiperTextToSpeechAdapter,
    SpeechTranscript,
    TtsSynthesis,
    WakeDetectionResult,
    VoiceSessionManager,
    VoiceTurnResult,
    VoiceTurnTimings,
)


def voice_event(event_type, *, endpoint_id="esp-box-1", session_id="voice-session-1", payload=None):
    return {
        "event_type": event_type,
        "endpoint_id": endpoint_id,
        "direction": "endpoint_to_backend",
        "session_id": session_id,
        "payload": payload or {},
    }


class CloseTrackingWakeDetector(DeterministicWakeDetector):
    def __init__(self, *, detect_on_chunk_index: int | None = None) -> None:
        super().__init__(detect_on_chunk_index=detect_on_chunk_index)
        self.closed_sessions: list[tuple[str, str]] = []

    def close_session(self, *, endpoint_id: str, session_id: str) -> None:
        self.closed_sessions.append((endpoint_id, session_id))


class SequenceWakeDetector:
    def __init__(self, results: list[WakeDetectionResult]) -> None:
        self.results = list(results)
        self.last_detection: WakeDetectionResult | None = None

    def inspect_chunk(self, *, endpoint_id, session_id, chunk):
        if self.results:
            self.last_detection = self.results.pop(0)
        else:
            self.last_detection = WakeDetectionResult(detected=False, model="sequence", reason="no_detection")
        return self.last_detection

    def status(self):
        return {
            "provider": "sequence",
            "healthy": True,
            "configured": True,
            "loaded": True,
            "last_detection": None
            if self.last_detection is None
            else {
                "detected": self.last_detection.detected,
                "confidence": self.last_detection.confidence,
                "model": self.last_detection.model,
                "reason": self.last_detection.reason,
            },
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
    assert response["event_id"].startswith("evt_")
    assert response["schema_version"] == "hexevoice.voice.event.v1"
    assert response["direction"] == "backend_to_endpoint"
    assert response["endpoint_id"] == "esp-box-1"
    assert response["session_id"] == "voice-session-1"
    assert response["payload"]["snapshot"]["session_state"] == "idle"
    assert response["payload"]["snapshot"]["connection_state"] == "connected"
    assert response["payload"]["snapshot"]["ux_state"] == "wake_armed"


def test_voice_websocket_treats_button_session_start_as_wake(tmp_path):
    detector = DeterministicWakeDetector(detect_on_chunk_index=None)
    manager = VoiceSessionManager(wake_detector=detector)
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json"), voice_session_manager=manager))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            voice_event(
                "session.start",
                payload={"wake_source": "button", "audio_format": {"sample_rate_hz": 16000}},
            )
        )
        wake_response = websocket.receive_json()
        state_response = websocket.receive_json()

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
        chunk_response = websocket.receive_json()

    assert wake_response["event_type"] == "wake.accepted"
    assert wake_response["payload"]["snapshot"]["session_state"] == "wake_detected"
    assert wake_response["payload"]["wake"]["source"] == "button"
    assert state_response["payload"]["snapshot"]["session_state"] == "listening"
    assert chunk_response["payload"]["snapshot"]["session_state"] == "capturing"

    status = client.get("/api/voice/status").json()
    assert status["wake_history"][0]["outcome"] == "accepted"
    assert status["wake_history"][0]["source"] == "button"
    assert status["wake_history"][0]["detected"] is True


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

    status = client.get("/api/voice/status").json()
    assert status["wake_history"][0]["outcome"] == "accepted"
    assert status["wake_history"][0]["detected"] is True
    assert status["wake_history"][0]["session_id"] == "voice-session-1"
    assert status["wake_history"][0]["model"] == "deterministic"


def test_voice_status_records_wake_confidence_history_for_tuning(tmp_path):
    detector = SequenceWakeDetector(
        [
            WakeDetectionResult(detected=False, confidence=0.42, model="Hexa", reason="below_threshold"),
            WakeDetectionResult(detected=True, confidence=0.86, model="Hexa"),
        ]
    )
    manager = VoiceSessionManager(wake_detector=detector)
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
        below_threshold = websocket.receive_json()
        websocket.send_json(
            voice_event(
                "audio.chunk",
                payload={
                    "chunk_index": 1,
                    "audio_format": {"encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1},
                    "payload_base64": "AAECAw==",
                },
            )
        )
        wake_response = websocket.receive_json()
        capturing = websocket.receive_json()

    assert below_threshold["payload"]["wake"]["confidence"] == 0.42
    assert below_threshold["payload"]["wake"]["reason"] == "below_threshold"
    assert wake_response["event_type"] == "wake.accepted"
    assert capturing["payload"]["wake"]["confidence"] == 0.86

    status = client.get("/api/voice/status").json()
    confidence_history = status["wake_confidence_history"]
    assert confidence_history[0]["confidence"] == 0.86
    assert confidence_history[0]["accepted"] is True
    assert confidence_history[0]["chunk_index"] == 1
    assert confidence_history[1]["confidence"] == 0.42
    assert confidence_history[1]["accepted"] is False
    assert confidence_history[1]["reason"] == "below_threshold"


def test_voice_websocket_closes_wake_stream_after_completed_session(tmp_path):
    detector = CloseTrackingWakeDetector(detect_on_chunk_index=0)
    manager = VoiceSessionManager(wake_detector=detector)
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
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json(voice_event("audio.end"))
        websocket.receive_json()

    assert detector.closed_sessions == [("esp-box-1", "voice-session-1")]


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
    assert transcript["payload"]["snapshot"]["session_state"] == "transcribing"
    assert transcript["payload"]["snapshot"]["ux_state"] == "thinking"
    assert transcript["payload"]["text"] == "hello"
    assert response_text["event_type"] == "response.text"
    assert response_text["payload"]["snapshot"]["session_state"] == "routing"
    assert response_text["payload"]["snapshot"]["ux_state"] == "thinking"
    assert response_text["payload"]["text"] == "I heard hello"
    assert tts_ready["event_type"] == "tts.ready"
    assert tts_ready["payload"]["snapshot"]["session_state"] == "responding"
    assert tts_ready["payload"]["snapshot"]["ux_state"] == "speaking"
    assert tts_ready["payload"]["content_type"] == "audio/wav"
    assert tts_ready["payload"]["stream_id"].startswith("tts-")
    assert completed["event_type"] == "session.completed"
    assert completed["payload"]["snapshot"]["session_state"] == "completed"

    status = client.get("/api/voice/status")
    assert status.status_code == 200
    assert status.json()["last_transcript"] == "hello"
    assert status.json()["last_transcript_metadata"]["provider_id"] == "deterministic"
    assert status.json()["last_transcript_metadata"]["model"] == "deterministic"
    assert status.json()["last_transcript_metadata"]["text_chars"] == 5
    assert status.json()["last_turn_timings"]["stt_ms"] >= 0
    assert status.json()["last_turn_timings"]["assistant_ms"] >= 0
    assert status.json()["last_turn_timings"]["tts_ms"] >= 0
    assert status.json()["last_turn_timings"]["total_ms"] >= 0
    assert status.json()["last_response"] == "I heard hello"
    assert status.json()["last_assistant"]["provider_id"] == "local_echo"
    assert status.json()["last_assistant"]["text"] == "I heard hello"
    assert status.json()["last_assistant"]["duration_ms"] >= 0
    assert status.json()["last_assistant"]["error"] is None
    assert status.json()["last_tts"]["stream_id"].startswith("tts-")


def test_voice_websocket_passes_transient_audio_to_turn_pipeline(tmp_path):
    class CapturingPipeline:
        def __init__(self) -> None:
            self.audio = None

        def complete_turn(self, audio):
            self.audio = audio
            return VoiceTurnResult(
                transcript=SpeechTranscript(text="hello", confidence=1.0),
                assistant_response=AssistantTurnResponse(
                    endpoint_id=audio.endpoint_id,
                    session_id=audio.session_id,
                    heard_text="hello",
                    reply_text="hi",
                    spoken_text="hi",
                    handled_locally=False,
                    command=None,
                    device_state="speaking",
                ),
                tts=TtsSynthesis(stream_id="tts-test"),
                timings=VoiceTurnTimings(stt_ms=1.0, assistant_ms=2.0, tts_ms=3.0, total_ms=6.0),
            )

    pipeline = CapturingPipeline()
    manager = VoiceSessionManager(
        wake_detector=DeterministicWakeDetector(detect_on_chunk_index=0),
        turn_pipeline=pipeline,
    )
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
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json(
            voice_event(
                "audio.chunk",
                payload={
                    "chunk_index": 1,
                    "audio_format": {"encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1},
                    "payload_base64": "BAUGBw==",
                },
            )
        )
        websocket.receive_json()
        websocket.send_json(voice_event("audio.end"))
        websocket.receive_json()

    assert pipeline.audio is not None
    assert pipeline.audio.audio_bytes == b"\x04\x05\x06\x07"
    assert pipeline.audio.sample_rate_hz == 16000
    assert pipeline.audio.encoding == "pcm_s16le"
    assert pipeline.audio.channels == 1


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
        assert status.json()["session_state"] == "idle"
        assert status.json()["ux_state"] == "wake_armed"
        assert status.json()["state_projection"] == {
            "connection_state": "connected",
            "ux_state": "wake_armed",
            "session_state": "idle",
            "transport_health": "online",
        }
        assert status.json()["active_session"]["session_state"] == "idle"
        assert status.json()["wake_provider"]["provider"] == "deterministic"
        assert status.json()["wake_provider"]["healthy"] is True
        assert status.json()["turn_pipeline"]["stt"]["provider"] == "deterministic"
        assert status.json()["turn_pipeline"]["tts"]["provider"] == "deterministic"
        assert status.json()["supported_actions"]["stop_session"] is True

        cancel = client.post("/api/voice/session/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["accepted"] is True
        assert cancel.json()["status"]["active_session"] is None


def test_voice_tts_audio_route_serves_generated_stream(tmp_path):
    stream_id = "tts-teststream"
    tts_dir = tmp_path / "voice_tts"
    tts_dir.mkdir()
    (tts_dir / f"{stream_id}.wav").write_bytes(b"RIFFtest-wav")
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", runtime_dir=tmp_path)))

    response = client.get(f"/api/voice/tts/{stream_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFFtest-wav"


def test_piper_tts_artifact_is_served_for_firmware_playback(tmp_path):
    class FakeResponse:
        headers = {"content-type": "audio/wav"}
        content = b"RIFFpiper-wav"

        def raise_for_status(self):
            return None

    class FakeHttpClient:
        def post(self, url, json):
            return FakeResponse()

    adapter = PiperTextToSpeechAdapter(
        base_url="http://127.0.0.1:10200",
        output_dir=tmp_path / "voice_tts",
        http_client=FakeHttpClient(),
    )
    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json", runtime_dir=tmp_path)))

    response = client.get(synthesis.audio_url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFFpiper-wav"


def test_replay_synthesizes_i_heard_last_transcript(tmp_path):
    class ReplayPipeline:
        def __init__(self):
            self.replay_text = None

        def status(self):
            return {"stt": {"provider": "test", "healthy": True}, "tts": {"provider": "test", "healthy": True}}

        def complete_turn(self, audio):
            return VoiceTurnResult(
                transcript=SpeechTranscript(text="what is the time", confidence=1.0),
                assistant_response=AssistantTurnResponse(
                    endpoint_id=audio.endpoint_id,
                    session_id=audio.session_id,
                    heard_text="what is the time",
                    reply_text="old response",
                    spoken_text="old response",
                    handled_locally=False,
                    command=None,
                    device_state="speaking",
                ),
                tts=TtsSynthesis(content_type="audio/wav", stream_id="tts-old", audio_url="/api/voice/tts/tts-old"),
                timings=VoiceTurnTimings(stt_ms=1.0, assistant_ms=1.0, tts_ms=1.0, total_ms=3.0),
            )

        def synthesize_reply(self, *, endpoint_id, session_id, text):
            self.replay_text = text
            return TtsSynthesis(content_type="audio/wav", stream_id="tts-replay", audio_url="/api/voice/tts/tts-replay", provider_id="test")

    pipeline = ReplayPipeline()
    manager = VoiceSessionManager(
        wake_detector=DeterministicWakeDetector(detect_on_chunk_index=0),
        turn_pipeline=pipeline,
    )
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json"), voice_session_manager=manager))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()
        websocket.send_json(voice_event("audio.chunk", payload={"chunk_index": 0, "audio_format": {"sample_rate_hz": 16000}}))
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json(voice_event("audio.end"))
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()
        replay_response = client.post("/api/endpoint/replay", json={"endpoint_id": "esp-box-1"})
        replay_event = websocket.receive_json()

    assert replay_response.status_code == 200
    assert pipeline.replay_text == "I heard what is the time"
    assert replay_event["event_type"] == "endpoint.replay"
    assert replay_event["payload"]["stream_id"] == "tts-replay"
    assert replay_event["payload"]["audio_url"] == "/api/voice/tts/tts-replay"
    assert client.get("/api/voice/status").json()["last_response"] == "I heard what is the time"


def test_voice_session_manager_pushes_timer_announcement_to_endpoint():
    class AnnouncementPipeline:
        def __init__(self):
            self.text = None

        def synthesize_reply(self, *, endpoint_id, session_id, text):
            self.text = text
            return TtsSynthesis(
                content_type="audio/wav",
                stream_id="tts-timer",
                audio_url="/api/voice/tts/tts-timer",
                provider_id="test",
            )

    class FakeWebSocket:
        def __init__(self):
            self.sent = []

        async def send_json(self, payload):
            self.sent.append(payload)

    pipeline = AnnouncementPipeline()
    websocket = FakeWebSocket()
    manager = VoiceSessionManager(turn_pipeline=pipeline)
    manager._connection_active = True
    manager._websocket = websocket
    manager._connected_endpoint_id = "esp-box-1"

    result = asyncio.run(
        manager.push_timer_announcement(
            endpoint_id="esp-box-1",
            session_id="session-1",
            text="Timer is on for 1 hour and 30 minutes.",
            source_event_id="interaction-timer-create-succeeded-session-1",
        )
    )

    assert result["accepted"] is True
    assert pipeline.text == "Timer is on for 1 hour and 30 minutes."
    assert websocket.sent[0]["event_type"] == "endpoint.replay"
    assert websocket.sent[0]["payload"]["stream_id"] == "tts-timer"
    assert websocket.sent[0]["payload"]["announcement_type"] == "timer.create_succeeded"
    assert websocket.sent[0]["payload"]["source_event_id"] == "interaction-timer-create-succeeded-session-1"


def test_voice_session_manager_pushes_speak_command_to_endpoint():
    class SpeakPipeline:
        def __init__(self):
            self.endpoint_id = None
            self.session_id = None
            self.text = None

        def synthesize_reply(self, *, endpoint_id, session_id, text):
            self.endpoint_id = endpoint_id
            self.session_id = session_id
            self.text = text
            return TtsSynthesis(
                content_type="audio/wav",
                stream_id="tts-speak",
                audio_url="/api/voice/tts/tts-speak",
                provider_id="test",
            )

    class FakeWebSocket:
        def __init__(self):
            self.sent = []

        async def send_json(self, payload):
            self.sent.append(payload)

    pipeline = SpeakPipeline()
    websocket = FakeWebSocket()
    manager = VoiceSessionManager(turn_pipeline=pipeline)
    manager._connection_active = True
    manager._websocket = websocket
    manager._connected_endpoint_id = "esp-pe-1"

    result = asyncio.run(
        manager.push_speak_command(
            endpoint_id="esp-pe-1",
            session_id="manual-speak",
            text="Vioce test",
        )
    )

    assert result["accepted"] is True
    assert pipeline.endpoint_id == "esp-pe-1"
    assert pipeline.session_id == "manual-speak"
    assert pipeline.text == "Vioce test"
    assert websocket.sent[0]["event_type"] == "endpoint.replay"
    assert websocket.sent[0]["payload"]["request_id"] == result["request_id"]
    assert websocket.sent[0]["payload"]["stream_id"] == "tts-speak"
    assert websocket.sent[0]["payload"]["audio_url"] == "/api/voice/tts/tts-speak"
    assert websocket.sent[0]["payload"]["text"] == "Vioce test"


def test_voice_websocket_surfaces_stt_provider_errors(tmp_path):
    class FailingSttPipeline:
        def status(self):
            return {"stt": {"provider": "test", "healthy": False}, "tts": {"provider": "test", "healthy": True}}

        def complete_turn(self, audio):
            return VoiceTurnResult(
                transcript=SpeechTranscript(text="", provider_id="test", error="stt unavailable"),
                assistant_response=AssistantTurnResponse(
                    endpoint_id=audio.endpoint_id,
                    session_id=audio.session_id,
                    heard_text="",
                    reply_text="",
                    spoken_text="",
                    handled_locally=False,
                    command=None,
                    device_state="idle",
                ),
                tts=TtsSynthesis(stream_id=None),
                timings=VoiceTurnTimings(stt_ms=1.0, assistant_ms=2.0, tts_ms=3.0, total_ms=6.0),
            )

    manager = VoiceSessionManager(
        wake_detector=DeterministicWakeDetector(detect_on_chunk_index=0),
        turn_pipeline=FailingSttPipeline(),
    )
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json"), voice_session_manager=manager))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()
        websocket.send_json(voice_event("audio.chunk", payload={"chunk_index": 0, "audio_format": {"sample_rate_hz": 16000}}))
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json(voice_event("audio.end"))
        error = websocket.receive_json()

    assert error["event_type"] == "session.error"
    assert error["payload"]["code"] == "stt_failed"
    assert error["payload"]["recoverable"] is True
    assert client.get("/api/voice/status").json()["last_error"]["code"] == "stt_failed"


def test_voice_websocket_surfaces_tts_provider_errors(tmp_path):
    class FailingTtsPipeline:
        def status(self):
            return {"stt": {"provider": "test", "healthy": True}, "tts": {"provider": "test", "healthy": False}}

        def complete_turn(self, audio):
            return VoiceTurnResult(
                transcript=SpeechTranscript(text="hello", confidence=1.0),
                assistant_response=AssistantTurnResponse(
                    endpoint_id=audio.endpoint_id,
                    session_id=audio.session_id,
                    heard_text="hello",
                    reply_text="hi",
                    spoken_text="hi",
                    handled_locally=False,
                    command=None,
                    device_state="speaking",
                ),
                tts=TtsSynthesis(stream_id="tts-test", provider_id="test", error="tts unavailable"),
                timings=VoiceTurnTimings(stt_ms=1.0, assistant_ms=2.0, tts_ms=3.0, total_ms=6.0),
            )

    manager = VoiceSessionManager(
        wake_detector=DeterministicWakeDetector(detect_on_chunk_index=0),
        turn_pipeline=FailingTtsPipeline(),
    )
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json"), voice_session_manager=manager))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()
        websocket.send_json(voice_event("audio.chunk", payload={"chunk_index": 0, "audio_format": {"sample_rate_hz": 16000}}))
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json(voice_event("audio.end"))
        transcript = websocket.receive_json()
        response = websocket.receive_json()
        error = websocket.receive_json()

    assert transcript["event_type"] == "transcript.final"
    assert response["event_type"] == "response.text"
    assert error["event_type"] == "session.error"
    assert error["payload"]["code"] == "tts_failed"
    assert client.get("/api/voice/status").json()["last_tts"]["error"] == "tts unavailable"


def test_voice_websocket_cancels_audio_end_when_wake_was_not_detected(tmp_path):
    detector = CloseTrackingWakeDetector(detect_on_chunk_index=None)
    manager = VoiceSessionManager(wake_detector=detector)
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

    status = client.get("/api/voice/status").json()
    assert status["wake_history"][0]["outcome"] == "not_detected"
    assert status["wake_history"][0]["detected"] is False
    assert status["wake_history"][0]["reason"] == "wake_not_detected"
    assert detector.closed_sessions == [("esp-box-1", "voice-session-1")]


def test_voice_websocket_cancel_returns_cancelled_event(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()
        websocket.send_json(voice_event("session.cancel", payload={"reason": "button"}))
        response = websocket.receive_json()

    assert response["event_type"] == "session.cancelled"
    assert response["payload"]["snapshot"]["session_state"] == "cancelled"
    assert response["payload"]["snapshot"]["ux_state"] == "idle"
    assert response["payload"]["snapshot"]["cancel_reason"] == "button"


def test_voice_status_projects_offline_state_after_reconnect_cycle(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()
        connected = client.get("/api/voice/status").json()
        assert connected["state_projection"]["connection_state"] == "connected"
        assert connected["state_projection"]["session_state"] == "idle"
        assert connected["state_projection"]["ux_state"] == "wake_armed"

    disconnected = client.get("/api/voice/status").json()
    assert disconnected["state_projection"]["connection_state"] == "offline"
    assert disconnected["state_projection"]["session_state"] is None
    assert disconnected["state_projection"]["ux_state"] == "idle"

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start", session_id="voice-session-2"))
        restarted = websocket.receive_json()

    assert restarted["payload"]["snapshot"]["connection_state"] == "connected"
    assert restarted["payload"]["snapshot"]["session_state"] == "idle"
    assert restarted["payload"]["snapshot"]["ux_state"] == "wake_armed"


def test_voice_websocket_rejects_invalid_event_envelope(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json({"event_type": "audio.chunk", "endpoint_id": "esp-box-1"})
        response = websocket.receive_json()

    assert response["event_type"] == "session.error"
    assert response["payload"]["code"] == "invalid_event_envelope"
    assert response["payload"]["message"]
    assert response["payload"]["recoverable"] is True
    diagnostics = client.get("/api/voice/status").json()["event_diagnostics"]
    assert diagnostics[0]["code"] in {"session.error", "invalid_event_envelope"}
    assert any(item["code"] == "invalid_event_envelope" for item in diagnostics)


def test_voice_websocket_rejects_unknown_schema_version(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            {
                "event_type": "session.start",
                "endpoint_id": "esp-box-1",
                "direction": "endpoint_to_backend",
                "schema_version": "hexevoice.voice.event.v2",
                "payload": {},
            }
        )
        response = websocket.receive_json()

    assert response["event_type"] == "session.error"
    assert response["payload"]["code"] == "invalid_event_envelope"
    assert "schema_version" in response["payload"]["message"]


def test_voice_websocket_records_command_acknowledgement_and_error(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(voice_event("session.start"))
        websocket.receive_json()
        websocket.send_json(
            voice_event(
                "command.ack",
                payload={
                    "request_id": "cmd-volume-1",
                    "command_type": "endpoint.volume",
                    "status": "succeeded",
                },
            )
        )
        websocket.receive_json()
        websocket.send_json(
            voice_event(
                "command.error",
                payload={
                    "request_id": "cmd-restart-1",
                    "command_type": "endpoint.restart",
                    "code": "unsupported_command",
                    "message": "Restart is not supported by this endpoint",
                    "recoverable": True,
                },
            )
        )
        websocket.receive_json()
        status = client.get("/api/voice/status").json()

    assert status["last_command_ack"]["request_id"] == "cmd-volume-1"
    assert status["last_command_ack"]["status"] == "succeeded"
    assert status["last_command_error"]["request_id"] == "cmd-restart-1"
    assert status["last_command_error"]["code"] == "unsupported_command"
    assert status["event_diagnostics"][0]["code"] == "unsupported_command"


def test_voice_websocket_rejects_malformed_command_acknowledgement(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    with client.websocket_connect("/api/voice/ws") as websocket:
        websocket.send_json(
            voice_event(
                "command.ack",
                session_id=None,
                payload={"request_id": "cmd-volume-1", "status": "succeeded"},
            )
        )
        response = websocket.receive_json()

    assert response["event_type"] == "session.error"
    assert response["payload"]["code"] == "invalid_command_ack"


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
