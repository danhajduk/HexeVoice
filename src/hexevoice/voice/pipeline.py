from __future__ import annotations

from dataclasses import dataclass
import io
import importlib.util
import logging
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Protocol
import wave
from uuid import uuid4

import httpx

from hexevoice.api.models import AssistantTurnRequest, AssistantTurnResponse
from hexevoice.assistant import AssistantTurnService

if TYPE_CHECKING:
    from hexevoice.config.settings import Settings


log = logging.getLogger(__name__)


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

    def status(self) -> dict:
        ...


class TextToSpeechAdapter(Protocol):
    def synthesize(self, *, endpoint_id: str, session_id: str, text: str) -> TtsSynthesis:
        ...

    def status(self) -> dict:
        ...


class DeterministicSpeechToTextAdapter:
    def __init__(self, *, transcript: str = "hello") -> None:
        self._transcript = transcript

    def transcribe(self, audio: VoiceTurnAudioSummary) -> SpeechTranscript:
        if audio.chunk_count <= 0:
            return SpeechTranscript(text="", confidence=0.0)
        return SpeechTranscript(text=self._transcript, confidence=1.0)

    def status(self) -> dict:
        return {"provider": "deterministic", "healthy": True, "configured": True}


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
        self._last_error: str | None = None

    def transcribe(self, audio: VoiceTurnAudioSummary) -> SpeechTranscript:
        if not self._api_key:
            self._last_error = "missing_api_key"
            return SpeechTranscript(text="", confidence=0.0, provider_id="openai", error="missing_api_key")
        if not audio.audio_bytes:
            self._last_error = "empty_audio"
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
            self._last_error = None
            return SpeechTranscript(text=text, provider_id="openai")
        except Exception as exc:
            self._last_error = str(exc)
            return SpeechTranscript(text="", confidence=0.0, provider_id="openai", error=self._last_error)

    def status(self) -> dict:
        return {
            "provider": "openai",
            "healthy": self._last_error is None,
            "configured": bool(self._api_key),
            "model": self._model,
            "base_url": self._base_url,
            "last_error": self._last_error,
        }

    def _audio_file(self, audio: VoiceTurnAudioSummary) -> bytes:
        return audio_file_bytes(audio)


class FasterWhisperSpeechToTextAdapter:
    def __init__(
        self,
        *,
        model_name: str = "small.en",
        device: str = "cpu",
        compute_type: str = "int8",
        temp_dir: Path,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._temp_dir = temp_dir
        self._model_factory = model_factory
        self._model: Any | None = None
        self._last_error: str | None = None

    def transcribe(self, audio: VoiceTurnAudioSummary) -> SpeechTranscript:
        if not audio.audio_bytes:
            self._last_error = "empty_audio"
            return SpeechTranscript(text="", confidence=0.0, provider_id="faster_whisper", error="empty_audio")

        temp_path: Path | None = None
        try:
            model = self._load_model()
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                prefix=f"{audio.endpoint_id}-{audio.session_id}-",
                dir=self._temp_dir,
                delete=False,
            ) as temp_file:
                temp_file.write(audio_file_bytes(audio))
                temp_path = Path(temp_file.name)

            segments, _info = model.transcribe(str(temp_path))
            text = " ".join(str(getattr(segment, "text", "")).strip() for segment in segments).strip()
            self._last_error = None
            log.info(
                "Local STT completed: provider=faster_whisper endpoint_id=%s session_id=%s model=%s text_chars=%s",
                audio.endpoint_id,
                audio.session_id,
                self._model_name,
                len(text),
            )
            return SpeechTranscript(text=text, provider_id="faster_whisper")
        except Exception as exc:
            self._last_error = str(exc)
            log.error(
                "Local STT failed: provider=faster_whisper endpoint_id=%s session_id=%s model=%s error=%s",
                audio.endpoint_id,
                audio.session_id,
                self._model_name,
                self._last_error,
            )
            return SpeechTranscript(text="", confidence=0.0, provider_id="faster_whisper", error=self._last_error)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    log.debug("Failed to remove temporary STT audio file: path=%s", temp_path)

    def status(self) -> dict:
        return {
            "provider": "faster_whisper",
            "healthy": self._last_error is None,
            "configured": self._model_factory is not None or importlib.util.find_spec("faster_whisper") is not None,
            "model": self._model_name,
            "device": self._device,
            "compute_type": self._compute_type,
            "temp_dir": str(self._temp_dir),
            "loaded": self._model is not None,
            "last_error": self._last_error,
        }

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            factory = self._model_factory
            if factory is None:
                from faster_whisper import WhisperModel

                factory = WhisperModel
            self._model = factory(self._model_name, device=self._device, compute_type=self._compute_type)
            return self._model
        except ModuleNotFoundError as exc:
            if exc.name == "faster_whisper":
                raise RuntimeError("missing_dependency:faster-whisper") from exc
            raise


class DeterministicTextToSpeechAdapter:
    def synthesize(self, *, endpoint_id: str, session_id: str, text: str) -> TtsSynthesis:
        return TtsSynthesis(stream_id=f"tts-{uuid4().hex[:12]}")

    def status(self) -> dict:
        return {"provider": "deterministic", "healthy": True, "configured": True}


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
        self._last_error: str | None = None

    def synthesize(self, *, endpoint_id: str, session_id: str, text: str) -> TtsSynthesis:
        stream_id = f"tts-{uuid4().hex[:12]}"
        content_type = self._content_type()
        if not self._api_key:
            self._last_error = "missing_api_key"
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
            self._last_error = None
            return TtsSynthesis(
                content_type=content_type,
                stream_id=stream_id,
                audio_url=f"/api/voice/tts/{stream_id}",
                provider_id="openai",
            )
        except Exception as exc:
            self._last_error = str(exc)
            return TtsSynthesis(
                content_type=content_type,
                stream_id=stream_id,
                provider_id="openai",
                error=self._last_error,
            )

    def _content_type(self) -> str:
        if self._response_format == "mp3":
            return "audio/mpeg"
        if self._response_format == "opus":
            return "audio/ogg"
        return f"audio/{self._response_format}"

    def status(self) -> dict:
        return {
            "provider": "openai",
            "healthy": self._last_error is None,
            "configured": bool(self._api_key),
            "model": self._model,
            "voice": self._voice,
            "base_url": self._base_url,
            "response_format": self._response_format,
            "last_error": self._last_error,
        }


def audio_file_bytes(audio: VoiceTurnAudioSummary) -> bytes:
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

    def status(self) -> dict:
        return {
            "stt": self._stt_adapter.status(),
            "tts": self._tts_adapter.status(),
        }


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
    elif settings.voice_stt_provider == "faster_whisper":
        stt_adapter = FasterWhisperSpeechToTextAdapter(
            model_name=settings.voice_stt_faster_whisper_model,
            device=settings.voice_stt_faster_whisper_device,
            compute_type=settings.voice_stt_faster_whisper_compute_type,
            temp_dir=settings.resolved_faster_whisper_temp_dir(),
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
