from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol
import wave
from uuid import uuid4

import httpx

from hexevoice.api.models import AssistantTurnRequest, AssistantTurnResponse
from hexevoice.assistant import AssistantTurnService

if TYPE_CHECKING:
    from hexevoice.config.settings import Settings


@dataclass(frozen=True)
class VoiceTurnAudioSummary:
    endpoint_id: str
    session_id: str
    chunk_count: int
    sample_rate_hz: int | None = None
    encoding: str | None = None
    channels: int = 1
    audio_bytes: bytes | None = None


@dataclass(frozen=True)
class SpeechTranscript:
    text: str
    confidence: float | None = None
    provider_id: str = "deterministic"
    error: str | None = None


@dataclass(frozen=True)
class TtsSynthesis:
    content_type: str = "audio/wav"
    stream_id: str | None = None
    audio_url: str | None = None
    provider_id: str = "deterministic"
    error: str | None = None


@dataclass(frozen=True)
class VoiceTurnResult:
    transcript: SpeechTranscript
    assistant_response: AssistantTurnResponse
    tts: TtsSynthesis


class SpeechToTextAdapter(Protocol):
    def transcribe(self, audio: VoiceTurnAudioSummary) -> SpeechTranscript:
        ...


class TextToSpeechAdapter(Protocol):
    def synthesize(self, *, endpoint_id: str, session_id: str, text: str) -> TtsSynthesis:
        ...


class DeterministicSpeechToTextAdapter:
    def __init__(self, *, transcript: str = "hello") -> None:
        self._transcript = transcript

    def transcribe(self, audio: VoiceTurnAudioSummary) -> SpeechTranscript:
        if audio.chunk_count <= 0:
            return SpeechTranscript(text="", confidence=0.0)
        return SpeechTranscript(text=self._transcript, confidence=1.0)


class OpenAiSpeechToTextAdapter:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "gpt-4o-mini-transcribe",
        base_url: str = "https://api.openai.com/v1",
        prompt: str | None = None,
        timeout_s: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._prompt = prompt
        self._timeout_s = timeout_s
        self._http_client = http_client

    def transcribe(self, audio: VoiceTurnAudioSummary) -> SpeechTranscript:
        if not self._api_key:
            return SpeechTranscript(text="", confidence=0.0, provider_id="openai", error="missing_api_key")
        if not audio.audio_bytes:
            return SpeechTranscript(text="", confidence=0.0, provider_id="openai", error="empty_audio")

        try:
            audio_file = self._audio_file(audio)
            data = {"model": self._model, "response_format": "json"}
            if self._prompt:
                data["prompt"] = self._prompt
            client = self._http_client or httpx.Client(timeout=self._timeout_s)
            try:
                response = client.post(
                    f"{self._base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data=data,
                    files={"file": ("audio.wav", audio_file, "audio/wav")},
                )
            finally:
                if self._http_client is None:
                    client.close()
            response.raise_for_status()
            payload = response.json()
            text = str(payload.get("text") or "").strip()
            return SpeechTranscript(text=text, provider_id="openai")
        except Exception as exc:
            return SpeechTranscript(text="", confidence=0.0, provider_id="openai", error=str(exc))

    def _audio_file(self, audio: VoiceTurnAudioSummary) -> bytes:
        if audio.encoding == "wav":
            return audio.audio_bytes or b""
        if audio.encoding != "pcm_s16le":
            return audio.audio_bytes or b""

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(audio.channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(audio.sample_rate_hz or 16000)
            wav_file.writeframes(audio.audio_bytes or b"")
        return buffer.getvalue()


class DeterministicTextToSpeechAdapter:
    def synthesize(self, *, endpoint_id: str, session_id: str, text: str) -> TtsSynthesis:
        return TtsSynthesis(stream_id=f"tts-{uuid4().hex[:12]}")


class OpenAiTextToSpeechAdapter:
    def __init__(
        self,
        *,
        api_key: str | None,
        output_dir: Path,
        model: str = "gpt-4o-mini-tts",
        voice: str = "alloy",
        base_url: str = "https://api.openai.com/v1",
        response_format: str = "wav",
        timeout_s: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._output_dir = output_dir
        self._model = model
        self._voice = voice
        self._base_url = base_url.rstrip("/")
        self._response_format = response_format
        self._timeout_s = timeout_s
        self._http_client = http_client

    def synthesize(self, *, endpoint_id: str, session_id: str, text: str) -> TtsSynthesis:
        stream_id = f"tts-{uuid4().hex[:12]}"
        content_type = self._content_type()
        if not self._api_key:
            return TtsSynthesis(
                content_type=content_type,
                stream_id=stream_id,
                provider_id="openai",
                error="missing_api_key",
            )

        try:
            client = self._http_client or httpx.Client(timeout=self._timeout_s)
            try:
                response = client.post(
                    f"{self._base_url}/audio/speech",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "voice": self._voice,
                        "input": text,
                        "response_format": self._response_format,
                    },
                )
            finally:
                if self._http_client is None:
                    client.close()
            response.raise_for_status()
            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / f"{stream_id}.{self._response_format}"
            output_path.write_bytes(response.content)
            return TtsSynthesis(
                content_type=content_type,
                stream_id=stream_id,
                audio_url=f"/api/voice/tts/{stream_id}",
                provider_id="openai",
            )
        except Exception as exc:
            return TtsSynthesis(
                content_type=content_type,
                stream_id=stream_id,
                provider_id="openai",
                error=str(exc),
            )

    def _content_type(self) -> str:
        if self._response_format == "mp3":
            return "audio/mpeg"
        if self._response_format == "opus":
            return "audio/ogg"
        return f"audio/{self._response_format}"


class VoiceTurnPipeline:
    def __init__(
        self,
        *,
        assistant_service: AssistantTurnService,
        stt_adapter: SpeechToTextAdapter | None = None,
        tts_adapter: TextToSpeechAdapter | None = None,
    ) -> None:
        self._assistant_service = assistant_service
        self._stt_adapter = stt_adapter or DeterministicSpeechToTextAdapter()
        self._tts_adapter = tts_adapter or DeterministicTextToSpeechAdapter()

    def complete_turn(self, audio: VoiceTurnAudioSummary) -> VoiceTurnResult:
        transcript = self._stt_adapter.transcribe(audio)
        assistant_response = self._assistant_service.handle_turn(
            AssistantTurnRequest(
                endpoint_id=audio.endpoint_id,
                session_id=audio.session_id,
                text=transcript.text or " ",
            )
        )
        tts = self._tts_adapter.synthesize(
            endpoint_id=audio.endpoint_id,
            session_id=audio.session_id,
            text=assistant_response.spoken_text,
        )
        return VoiceTurnResult(transcript=transcript, assistant_response=assistant_response, tts=tts)


def build_voice_turn_pipeline(*, settings: "Settings", assistant_service: AssistantTurnService) -> VoiceTurnPipeline:
    stt_adapter: SpeechToTextAdapter | None = None
    tts_adapter: TextToSpeechAdapter | None = None
    if settings.voice_stt_provider == "openai":
        stt_adapter = OpenAiSpeechToTextAdapter(
            api_key=settings.openai_api_key,
            model=settings.voice_stt_model,
            base_url=settings.voice_stt_base_url,
            prompt=settings.voice_stt_prompt,
            timeout_s=settings.voice_stt_timeout_s,
        )
    if settings.voice_tts_provider == "openai":
        tts_adapter = OpenAiTextToSpeechAdapter(
            api_key=settings.openai_api_key,
            output_dir=settings.runtime_dir / "voice_tts",
            model=settings.voice_tts_model,
            voice=settings.voice_tts_voice,
            base_url=settings.voice_tts_base_url,
            response_format=settings.voice_tts_response_format,
            timeout_s=settings.voice_tts_timeout_s,
        )

    return VoiceTurnPipeline(assistant_service=assistant_service, stt_adapter=stt_adapter, tts_adapter=tts_adapter)
