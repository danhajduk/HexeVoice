import io
from pathlib import Path
from types import SimpleNamespace
import wave

import httpx

from hexevoice.assistant import AssistantTurnService
from hexevoice.config.settings import Settings
from hexevoice.runtime.service import NodeRuntimeService
from hexevoice.voice import (
    DeterministicSpeechToTextAdapter,
    DeterministicTextToSpeechAdapter,
    FasterWhisperSpeechToTextAdapter,
    OpenAiSpeechToTextAdapter,
    OpenAiTextToSpeechAdapter,
    VoiceTurnAudioSummary,
    VoiceTurnPipeline,
    build_voice_turn_pipeline,
)


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
    assert result.assistant_response.command == "status"
    assert "lab-voice is not ready" in result.assistant_response.spoken_text
    assert result.tts.content_type == "audio/wav"
    assert result.tts.stream_id.startswith("tts-")


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
