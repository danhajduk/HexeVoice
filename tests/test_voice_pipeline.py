import io
from pathlib import Path
from types import SimpleNamespace
import wave

import httpx

from hexevoice.assistant import AssistantTurnService
from hexevoice.config.settings import Settings
from hexevoice.domain_events import DomainEventPublishDecision
from hexevoice.runtime.service import NodeRuntimeService
from hexevoice.voice import (
    DeterministicSpeechToTextAdapter,
    DeterministicTextToSpeechAdapter,
    FasterWhisperSpeechToTextAdapter,
    OpenAiSpeechToTextAdapter,
    OpenAiTextToSpeechAdapter,
    PiperTextToSpeechAdapter,
    VoiceTurnAudioSummary,
    VoiceTurnPipeline,
    build_voice_turn_pipeline,
)


class FakeTimerEventPublisher:
    def __init__(self) -> None:
        self.calls = []

    def publish_timer_create(self, **payload):
        self.calls.append(payload)
        return DomainEventPublishDecision(status="published", reason="published", topic="hexe/nodes/node-1/events/timer/create_requested")

    def status(self):
        return {"provider": "fake", "enabled": True, "last_decision": None}


class CaptureTextToSpeechAdapter:
    def __init__(self) -> None:
        self.calls = []

    def synthesize(self, *, endpoint_id, session_id, text, voice=None, audio_format=None, stream_id=None):
        self.calls.append(
            {
                "endpoint_id": endpoint_id,
                "session_id": session_id,
                "text": text,
                "voice": voice,
                "audio_format": audio_format,
                "stream_id": stream_id,
            }
        )
        return DeterministicTextToSpeechAdapter().synthesize(
            endpoint_id=endpoint_id,
            session_id=session_id,
            text=text,
            voice=voice,
            audio_format=audio_format,
            stream_id=stream_id,
        )

    def status(self):
        return {"provider": "capture", "healthy": True, "configured": True}


def test_voice_turn_pipeline_runs_stt_assistant_and_tts(tmp_path):
    runtime = NodeRuntimeService(settings=Settings(onboarding_state_path=tmp_path / "state.json", node_name="lab-voice"))
    assistant = AssistantTurnService(settings=Settings(node_name="lab-voice"), runtime_service=runtime)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="status"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=2)
    )

    assert result.transcript.text == "status"
    assert result.assistant_response.command is None
    assert result.assistant_response.spoken_text == "I heard status"
    assert result.tts.content_type == "audio/wav"
    assert result.tts.stream_id.startswith("tts-")
    assert result.timings.stt_ms >= 0
    assert result.timings.assistant_ms >= 0
    assert result.timings.tts_ms >= 0
    assert result.timings.total_ms >= 0


def test_voice_turn_pipeline_can_select_voice_by_endpoint(tmp_path):
    runtime = NodeRuntimeService(settings=Settings(onboarding_state_path=tmp_path / "state.json", node_name="lab-voice"))
    assistant = AssistantTurnService(settings=Settings(node_name="lab-voice"), runtime_service=runtime)
    tts_adapter = CaptureTextToSpeechAdapter()
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="status"),
        tts_adapter=tts_adapter,
        endpoint_voices={"esp-pe-1": "en_US-hfc_female-medium"},
    )

    pipeline.complete_turn(VoiceTurnAudioSummary(endpoint_id="esp-pe-1", session_id="voice-session-1", chunk_count=2))
    pipeline.synthesize_reply(
        endpoint_id="esp-pe-1",
        session_id="voice-session-2",
        text="hello",
        voice="en_US-lessac-medium",
    )

    assert tts_adapter.calls[0]["voice"] == "en_US-hfc_female-medium"
    assert tts_adapter.calls[1]["voice"] == "en_US-lessac-medium"
    assert pipeline.status()["endpoint_voices"] == {"esp-pe-1": "en_US-hfc_female-medium"}


def test_build_voice_turn_pipeline_keeps_deterministic_stt_as_default(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", runtime_dir=tmp_path)
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)
    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=1)
    )

    assert isinstance(pipeline._stt_adapter, DeterministicSpeechToTextAdapter)
    assert result.transcript.provider_id == "deterministic"
    assert result.transcript.text == "hello"
    assert result.assistant_response.spoken_text == "I heard hello"


def test_voice_turn_pipeline_strips_wake_word_from_final_transcript(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, what time is it?"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=1)
    )

    assert result.transcript.text == "what time is it?"
    assert result.assistant_response.heard_text == "what time is it?"
    assert result.assistant_response.spoken_text == "I heard what time is it?"


def test_voice_turn_pipeline_handles_timer_intent_locally(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", voice_wake_models="Hexa")
    runtime = NodeRuntimeService(settings=settings)
    publisher = FakeTimerEventPublisher()
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime, timer_event_publisher=publisher)
    pipeline = VoiceTurnPipeline(
        assistant_service=assistant,
        stt_adapter=DeterministicSpeechToTextAdapter(transcript="Hexa, set a timer for 10 minutes"),
        tts_adapter=DeterministicTextToSpeechAdapter(),
    )

    result = pipeline.complete_turn(
        VoiceTurnAudioSummary(endpoint_id="esp-box-1", session_id="voice-session-1", chunk_count=1)
    )

    assert result.transcript.text == "set a timer for 10 minutes"
    assert result.assistant_response.handled_locally is True
    assert result.assistant_response.command == "timer.create"
    assert result.assistant_response.provider_id == "local_pattern"
    assert result.assistant_response.spoken_text == "Setting timer for 10 minutes."
    assert result.assistant_response.intent_latency_ms >= 0
    assert publisher.calls == [
        {
            "endpoint_id": "esp-box-1",
            "session_id": "voice-session-1",
            "heard_text": "set a timer for 10 minutes",
            "duration_seconds": 600,
            "duration_text": "10 minutes",
            "requested_at": publisher.calls[0]["requested_at"],
        }
    ]
    assert publisher.calls[0]["requested_at"].tzinfo is not None


def test_openai_stt_adapter_posts_wav_transcription_request():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "what time is it"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fake_token = "unit-test-token"
    adapter = OpenAiSpeechToTextAdapter(
        api_key=fake_token,
        model="gpt-4o-mini-transcribe",
        base_url="https://api.openai.test/v1",
        http_client=client,
    )

    transcript = adapter.transcribe(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x00\x00" * 320,
        )
    )

    assert transcript.text == "what time is it"
    assert transcript.provider_id == "openai"
    assert captured["authorization"] == f"Bearer {fake_token}"
    assert b'gpt-4o-mini-transcribe' in captured["body"]
    assert b"audio.wav" in captured["body"]


def test_openai_stt_adapter_wraps_pcm_as_wav():
    adapter = OpenAiSpeechToTextAdapter(**{"api" + "_key": "unit-test-token"})

    audio = adapter._audio_file(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 320,
        )
    )

    with wave.open(io.BytesIO(audio), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnframes() == 320


def test_build_voice_turn_pipeline_uses_openai_stt_when_configured(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        voice_stt_provider="openai",
        openai_api_key="test-key",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert isinstance(pipeline._stt_adapter, OpenAiSpeechToTextAdapter)


def test_faster_whisper_stt_adapter_transcribes_temp_wav_and_removes_it(tmp_path):
    captured = {}

    class FakeModel:
        def __init__(self, model_name, *, device, compute_type):
            captured["model_name"] = model_name
            captured["device"] = device
            captured["compute_type"] = compute_type

        def transcribe(self, path):
            captured["path"] = path
            with wave.open(path, "rb") as wav_file:
                captured["sample_rate_hz"] = wav_file.getframerate()
                captured["channels"] = wav_file.getnchannels()
                captured["frames"] = wav_file.getnframes()
            return [SimpleNamespace(text=" what "), SimpleNamespace(text=" time ")], object()

    adapter = FasterWhisperSpeechToTextAdapter(
        model_name="base.en",
        device="cpu",
        compute_type="int8",
        temp_dir=tmp_path,
        model_factory=FakeModel,
    )

    transcript = adapter.transcribe(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 320,
        )
    )

    assert transcript.text == "what time"
    assert transcript.provider_id == "faster_whisper"
    assert transcript.model == "base.en"
    assert transcript.duration_ms is not None
    assert captured["model_name"] == "base.en"
    assert captured["device"] == "cpu"
    assert captured["compute_type"] == "int8"
    assert captured["sample_rate_hz"] == 16000
    assert captured["channels"] == 1
    assert captured["frames"] == 320
    assert not Path(captured["path"]).exists()


def test_faster_whisper_stt_adapter_preloads_model(tmp_path):
    captured = {"loads": 0}

    class FakeModel:
        def __init__(self, model_name, *, device, compute_type):
            captured["loads"] += 1
            captured["model_name"] = model_name
            captured["device"] = device
            captured["compute_type"] = compute_type

    adapter = FasterWhisperSpeechToTextAdapter(
        model_name="base.en",
        device="cpu",
        compute_type="int8",
        temp_dir=tmp_path,
        model_factory=FakeModel,
    )

    preload = adapter.preload()
    second_preload = adapter.preload()

    assert preload["loaded"] is True
    assert preload["model"] == "base.en"
    assert preload["duration_ms"] is not None
    assert second_preload["loaded"] is True
    assert captured["loads"] == 1
    assert adapter.status()["loaded"] is True
    assert adapter.status()["last_load_duration_ms"] is not None


def test_build_voice_turn_pipeline_uses_faster_whisper_stt_when_configured(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_stt_provider="faster_whisper",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert isinstance(pipeline._stt_adapter, FasterWhisperSpeechToTextAdapter)


def test_faster_whisper_stt_adapter_returns_error_without_losing_fallback_modes(tmp_path):
    class FailingModel:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("model unavailable")

    adapter = FasterWhisperSpeechToTextAdapter(
        model_name="small.en",
        device="cpu",
        compute_type="int8",
        temp_dir=tmp_path,
        model_factory=FailingModel,
    )

    transcript = adapter.transcribe(
        VoiceTurnAudioSummary(
            endpoint_id="esp-box-1",
            session_id="voice-session-1",
            chunk_count=1,
            sample_rate_hz=16000,
            encoding="pcm_s16le",
            channels=1,
            audio_bytes=b"\x01\x00" * 320,
        )
    )

    assert transcript.provider_id == "faster_whisper"
    assert transcript.text == ""
    assert transcript.error == "model unavailable"
    assert adapter.status()["healthy"] is False
    assert adapter.status()["last_error"] == "model unavailable"


def test_openai_tts_adapter_posts_speech_request_and_stores_audio(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["json"] = request.read()
        return httpx.Response(200, content=b"RIFFtest-wav")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fake_token = "unit-test-token"
    adapter = OpenAiTextToSpeechAdapter(
        **{"api" + "_key": fake_token},
        output_dir=tmp_path,
        model="gpt-4o-mini-tts",
        voice="alloy",
        base_url="https://api.openai.test/v1",
        response_format="wav",
        http_client=client,
    )

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert synthesis.provider_id == "openai"
    assert synthesis.content_type == "audio/wav"
    assert synthesis.stream_id is not None
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}"
    assert (tmp_path / f"{synthesis.stream_id}.wav").read_bytes() == b"RIFFtest-wav"
    assert captured["authorization"] == f"Bearer {fake_token}"
    assert b"gpt-4o-mini-tts" in captured["json"]
    assert b"hello" in captured["json"]


def test_openai_tts_adapter_can_override_voice_and_format_per_request(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        return httpx.Response(200, content=b"mp3-bytes")

    adapter = OpenAiTextToSpeechAdapter(
        **{"api" + "_key": "unit-test-token"},
        output_dir=tmp_path,
        model="gpt-4o-mini-tts",
        voice="alloy",
        base_url="https://api.openai.test/v1",
        response_format="wav",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(
        endpoint_id="esp-box-1",
        session_id="voice-session-1",
        text="hello",
        voice="nova",
        audio_format="mp3",
    )

    assert synthesis.content_type == "audio/mpeg"
    assert (tmp_path / f"{synthesis.stream_id}.mp3").read_bytes() == b"mp3-bytes"
    assert b"nova" in captured["json"]
    assert b"mp3" in captured["json"]


def test_build_voice_turn_pipeline_uses_openai_tts_when_configured(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_tts_provider="openai",
        **{"openai" + "_api_key": "unit-test-token"},
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert isinstance(pipeline._tts_adapter, OpenAiTextToSpeechAdapter)


def test_piper_tts_adapter_posts_synthesis_request_and_stores_audio(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read()
        return httpx.Response(200, content=b"RIFFpiper-wav", headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        synthesize_path="/api/tts",
        voice="en_US-test",
        output_dir=tmp_path,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert captured["url"] == "http://piper.test:10200/api/tts"
    assert b"hello" in captured["json"]
    assert b"en_US-test" in captured["json"]
    assert synthesis.provider_id == "piper"
    assert synthesis.content_type == "audio/wav"
    assert synthesis.audio_variant == "16k"
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}/16k"
    assert (tmp_path / f"{synthesis.stream_id}.raw.wav").read_bytes() == b"RIFFpiper-wav"
    assert (tmp_path / f"{synthesis.stream_id}.16k.wav").read_bytes() == b"RIFFpiper-wav"
    assert (tmp_path / f"{synthesis.stream_id}.48k.wav").read_bytes() == b"RIFFpiper-wav"
    assert adapter.status()["healthy"] is True


def test_piper_tts_adapter_resamples_wav_for_endpoint(tmp_path):
    source = io.BytesIO()
    with wave.open(source, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes((b"\x00\x00\xff\x7f" * 2205))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=source.getvalue(), headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        output_dir=tmp_path,
        output_sample_rate_hz=16000,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert synthesis.audio_variant == "16k"
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}/16k"
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.16k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnchannels() == 1
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.48k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 48000
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.raw.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 22050
    assert synthesis.raw_sample_rate_hz == 22050
    assert synthesis.output_sample_rate_hz == 16000
    assert synthesis.variant_sample_rates_hz == {"raw": 22050, "16k": 16000, "48k": 48000}


def test_piper_tts_adapter_uses_endpoint_specific_sample_rate(tmp_path):
    source = io.BytesIO()
    with wave.open(source, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes((b"\x00\x00\xff\x7f" * 2205))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=source.getvalue(), headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        output_dir=tmp_path,
        output_sample_rate_hz=16000,
        endpoint_sample_rates={"esp-pe-1": 48000},
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-pe-1", session_id="voice-session-1", text="hello")

    assert synthesis.audio_variant == "48k"
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}/48k"
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.48k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 48000
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.16k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
    assert synthesis.output_sample_rate_hz == 48000
    assert adapter.status()["endpoint_sample_rates"] == {"esp-pe-1": 48000}


def test_piper_tts_adapter_keeps_native_wav_when_resampling_disabled(tmp_path):
    source = io.BytesIO()
    with wave.open(source, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes((b"\x00\x00\xff\x7f" * 1600))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=source.getvalue(), headers={"content-type": "audio/wav"})

    adapter = PiperTextToSpeechAdapter(
        base_url="http://piper.test:10200",
        output_dir=tmp_path,
        output_sample_rate_hz=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert synthesis.audio_variant == "raw"
    assert synthesis.audio_url == f"/api/voice/tts/{synthesis.stream_id}/raw"
    assert (tmp_path / f"{synthesis.stream_id}.raw.wav").read_bytes() == source.getvalue()
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.16k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
    with wave.open(str(tmp_path / f"{synthesis.stream_id}.48k.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 48000


def test_piper_tts_adapter_falls_back_when_unconfigured(tmp_path):
    adapter = PiperTextToSpeechAdapter(base_url=None, output_dir=tmp_path)

    synthesis = adapter.synthesize(endpoint_id="esp-box-1", session_id="voice-session-1", text="hello")

    assert synthesis.provider_id == "deterministic"
    assert synthesis.stream_id.startswith("tts-")
    assert adapter.status()["healthy"] is False
    assert adapter.status()["last_error"] == "missing_piper_base_url"


def test_build_voice_turn_pipeline_uses_piper_tts_when_configured(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_tts_provider="piper",
        voice_tts_piper_base_url="http://piper.test:10200",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert isinstance(pipeline._tts_adapter, PiperTextToSpeechAdapter)


def test_build_voice_turn_pipeline_routes_piper_to_supervised_default(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_tts_provider="piper",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    status = pipeline.status()["tts"]
    assert status["provider"] == "piper"
    assert status["configured"] is True
    assert status["base_url"] == "http://127.0.0.1:10200"
    assert status["synthesize_path"] == "/api/tts"
    assert status["output_sample_rate_hz"] == 16000
    assert status["fallback"]["provider"] == "deterministic"


def test_build_voice_turn_pipeline_applies_endpoint_voice_overrides(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_tts_endpoint_voices="esp-pe-1=en_US-hfc_female-medium",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert pipeline.status()["endpoint_voices"] == {"esp-pe-1": "en_US-hfc_female-medium"}


def test_build_voice_turn_pipeline_applies_endpoint_sample_rate_overrides(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        runtime_dir=tmp_path,
        voice_tts_provider="piper",
        voice_tts_endpoint_sample_rates="esp-pe-1=48000,esp-box-1=16000",
    )
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)

    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    assert pipeline.status()["tts"]["endpoint_sample_rates"] == {"esp-pe-1": 48000, "esp-box-1": 16000}


def test_voice_turn_pipeline_status_reports_provider_health(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json", runtime_dir=tmp_path)
    runtime = NodeRuntimeService(settings=settings)
    assistant = AssistantTurnService(settings=settings, runtime_service=runtime)
    pipeline = build_voice_turn_pipeline(settings=settings, assistant_service=assistant)

    status = pipeline.status()

    assert status["stt"]["provider"] == "deterministic"
    assert status["stt"]["healthy"] is True
    assert status["tts"]["provider"] == "deterministic"
    assert status["tts"]["healthy"] is True
