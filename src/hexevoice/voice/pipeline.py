from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from contextlib import ExitStack
import base64
import io
import importlib.util
import json
import logging
from pathlib import Path
import tempfile
import threading
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Protocol
import wave
from uuid import uuid4

import httpx
import numpy as np
import soxr

from hexevoice.api.models import AssistantTurnRequest, AssistantTurnResponse
from hexevoice.assistant import AssistantTurnService
from hexevoice.engine_http import client_for_engine
from hexevoice.voice.records import record_voice_event

if TYPE_CHECKING:
    from hexevoice.config.settings import Settings


log = logging.getLogger(__name__)

DEFAULT_TTS_AUDIO_TTL_SECONDS = 3600
PIPER_TTS_AUDIO_CHUNK_FRAMES = 4096
DEFAULT_PIPER_TTS_AUDIO_VARIANT_SAMPLE_RATES = {
    "16k": 16000,
    "48k": 48000,
}
PIPER_TTS_AUDIO_VARIANT_SAMPLE_RATES = DEFAULT_PIPER_TTS_AUDIO_VARIANT_SAMPLE_RATES


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
    model: str | None = None
    duration_ms: float | None = None
    timing_breakdown_ms: dict[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class TtsSynthesis:
    content_type: str = "audio/wav"
    stream_id: str | None = None
    audio_url: str | None = None
    endpoint_audio_url: str | None = None
    audio_urls: dict[str, str] = field(default_factory=dict)
    provider_id: str = "deterministic"
    model_id: str | None = None
    voice_id: str | None = None
    audio_variant: str | None = None
    audio_variants: dict[str, str] = field(default_factory=dict)
    planned_audio_variants: dict[str, str] = field(default_factory=dict)
    pending_audio_variants: dict[str, str] = field(default_factory=dict)
    conversion_policy: str | None = None
    raw_audio_path: str | None = None
    raw_sample_rate_hz: int | None = None
    audio_variant_sample_rate_hz: int | None = None
    audio_variant_source_sample_rate_hz: int | None = None
    output_sample_rate_hz: int | None = None
    variant_sample_rates_hz: dict[str, int | None] = field(default_factory=dict)
    timing_breakdown_ms: dict[str, float] = field(default_factory=dict)
    metadata_path: str | None = None
    expires_at: str | None = None
    ttl_seconds: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class VoiceTurnTimings:
    stt_ms: float
    assistant_ms: float
    tts_ms: float
    total_ms: float


@dataclass(frozen=True)
class VoiceTurnResult:
    transcript: SpeechTranscript
    assistant_response: AssistantTurnResponse
    tts: TtsSynthesis
    timings: VoiceTurnTimings


class SpeechToTextAdapter(Protocol):
    def transcribe(self, audio: VoiceTurnAudioSummary) -> SpeechTranscript:
        ...

    def status(self) -> dict:
        ...


class TextToSpeechAdapter(Protocol):
    def synthesize(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        text: str,
        voice: str | None = None,
        audio_format: str | None = None,
        stream_id: str | None = None,
    ) -> TtsSynthesis:
        ...

    def status(self) -> dict:
        ...


class DeterministicSpeechToTextAdapter:
    def __init__(self, *, transcript: str = "hello") -> None:
        self._transcript = transcript

    def transcribe(self, audio: VoiceTurnAudioSummary) -> SpeechTranscript:
        if audio.chunk_count <= 0:
            return SpeechTranscript(text="", confidence=0.0, model="deterministic")
        return SpeechTranscript(text=self._transcript, confidence=1.0, model="deterministic")

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
            started_at = time.perf_counter()
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
            return SpeechTranscript(
                text=text,
                provider_id="openai",
                model=self._model,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
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
        model_name: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
        temp_dir: Path,
        language: str | None = "en",
        beam_size: int | None = 5,
        best_of: int | None = 5,
        without_timestamps: bool = True,
        word_timestamps: bool = False,
        max_initial_timestamp: float | None = 1.0,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._temp_dir = temp_dir
        self._language = language
        self._beam_size = beam_size
        self._best_of = best_of
        self._without_timestamps = without_timestamps
        self._word_timestamps = word_timestamps
        self._max_initial_timestamp = max_initial_timestamp
        self._model_factory = model_factory
        self._model: Any | None = None
        self._last_error: str | None = None
        self._last_duration_ms: float | None = None
        self._last_timing_breakdown_ms: dict[str, float] = {}
        self._last_text_chars: int | None = None
        self._last_load_duration_ms: float | None = None
        self._loaded_at: datetime | None = None
        self._load_count = 0
        self._loaded_config: dict[str, object] | None = None

    def transcribe(self, audio: VoiceTurnAudioSummary) -> SpeechTranscript:
        if not audio.audio_bytes:
            self._last_error = "empty_audio"
            return SpeechTranscript(text="", confidence=0.0, provider_id="faster_whisper", error="empty_audio")

        temp_path: Path | None = None
        timing: dict[str, float] = {}
        try:
            started_at = time.perf_counter()
            model = self._load_model()
            prepared_at = time.perf_counter()
            wav_bytes = audio_file_bytes(audio)
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                prefix=f"{audio.endpoint_id}-{audio.session_id}-",
                dir=self._temp_dir,
                delete=False,
            ) as temp_file:
                temp_file.write(wav_bytes)
                temp_path = Path(temp_file.name)
            timing["audio_preparation_ms"] = round((time.perf_counter() - prepared_at) * 1000, 2)

            inference_started_at = time.perf_counter()
            segments, _info = model.transcribe(str(temp_path), **self._transcribe_options())
            timing["model_inference_ms"] = round((time.perf_counter() - inference_started_at) * 1000, 2)
            decoding_started_at = time.perf_counter()
            segment_texts = [str(getattr(segment, "text", "")).strip() for segment in segments]
            timing["decoding_ms"] = round((time.perf_counter() - decoding_started_at) * 1000, 2)
            post_processing_started_at = time.perf_counter()
            text = " ".join(segment_texts).strip()
            timing["post_processing_ms"] = round((time.perf_counter() - post_processing_started_at) * 1000, 2)
            self._last_duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            timing["total_ms"] = self._last_duration_ms
            self._last_timing_breakdown_ms = timing
            self._last_text_chars = len(text)
            self._last_error = None
            log.info(
                "Local STT completed: provider=faster_whisper endpoint_id=%s session_id=%s model=%s duration_ms=%s text_chars=%s",
                audio.endpoint_id,
                audio.session_id,
                self._model_name,
                self._last_duration_ms,
                len(text),
            )
            return SpeechTranscript(
                text=text,
                provider_id="faster_whisper",
                model=self._model_name,
                duration_ms=self._last_duration_ms,
                timing_breakdown_ms=timing,
            )
        except Exception as exc:
            self._last_error = str(exc)
            self._last_timing_breakdown_ms = timing
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

    def preload(self) -> dict:
        try:
            self._load_model()
            self._last_error = None
            return {
                "provider": "faster_whisper",
                "loaded": True,
                "model": self._model_name,
                "duration_ms": self._last_load_duration_ms,
            }
        except Exception as exc:
            self._last_error = str(exc)
            log.error(
                "Local STT preload failed: provider=faster_whisper model=%s error=%s",
                self._model_name,
                self._last_error,
            )
            return {
                "provider": "faster_whisper",
                "loaded": False,
                "model": self._model_name,
                "error": self._last_error,
            }

    def status(self) -> dict:
        return {
            "provider": "faster_whisper",
            "healthy": self._last_error is None,
            "configured": self._model_factory is not None or importlib.util.find_spec("faster_whisper") is not None,
            "model": self._model_name,
            "device": self._device,
            "compute_type": self._compute_type,
            "transcribe_options": self._transcribe_options(),
            "temp_dir": str(self._temp_dir),
            "loaded": self._model is not None,
            "loaded_at": self._loaded_at.isoformat() if self._loaded_at else None,
            "load_count": self._load_count,
            "loaded_config": self._loaded_config,
            "reload_required": self._reload_required(),
            "last_load_duration_ms": self._last_load_duration_ms,
            "last_duration_ms": self._last_duration_ms,
            "last_timing_breakdown_ms": self._last_timing_breakdown_ms,
            "last_text_chars": self._last_text_chars,
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
            started_at = time.perf_counter()
            self._model = factory(self._model_name, device=self._device, compute_type=self._compute_type)
            self._last_load_duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._loaded_at = datetime.now(UTC)
            self._load_count += 1
            self._loaded_config = self._current_config()
            log.info(
                "Local STT model loaded: provider=faster_whisper model=%s device=%s compute_type=%s duration_ms=%s",
                self._model_name,
                self._device,
                self._compute_type,
                self._last_load_duration_ms,
            )
            return self._model
        except ModuleNotFoundError as exc:
            if exc.name == "faster_whisper":
                raise RuntimeError("missing_dependency:faster-whisper") from exc
            raise

    def _transcribe_options(self) -> dict[str, object]:
        options: dict[str, object] = {
            "without_timestamps": self._without_timestamps,
            "word_timestamps": self._word_timestamps,
        }
        if self._language:
            options["language"] = self._language
        if self._beam_size is not None:
            options["beam_size"] = self._beam_size
        if self._best_of is not None:
            options["best_of"] = self._best_of
        if self._max_initial_timestamp is not None:
            options["max_initial_timestamp"] = self._max_initial_timestamp
        return options

    def _current_config(self) -> dict[str, object]:
        return {
            "model": self._model_name,
            "device": self._device,
            "compute_type": self._compute_type,
            "transcribe_options": self._transcribe_options(),
        }

    def _reload_required(self) -> bool:
        return self._loaded_config is not None and self._loaded_config != self._current_config()


class ExternalFasterWhisperSpeechToTextAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        socket_path: Path | None = None,
        model_name: str = "base.en",
        timeout_s: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._socket_path = socket_path
        self._model_name = model_name
        self._timeout_s = timeout_s
        self._http_client = http_client
        self._last_error: str | None = None
        self._last_duration_ms: float | None = None
        self._last_timing_breakdown_ms: dict[str, float] = {}

    def transcribe(self, audio: VoiceTurnAudioSummary) -> SpeechTranscript:
        if not audio.audio_bytes:
            self._last_error = "empty_audio"
            return SpeechTranscript(
                text="",
                confidence=0.0,
                provider_id="external_faster_whisper",
                model=self._model_name,
                error="empty_audio",
            )

        started_at = time.perf_counter()
        client = self._http_client or client_for_engine(timeout=self._timeout_s, socket_path=self._socket_path)
        try:
            response = client.post(
                f"{self._base_url}/transcribe",
                json={
                    "endpoint_id": audio.endpoint_id,
                    "session_id": audio.session_id,
                    "chunk_count": audio.chunk_count,
                    "sample_rate_hz": audio.sample_rate_hz,
                    "encoding": audio.encoding,
                    "channels": audio.channels,
                    "audio_base64": base64.b64encode(audio.audio_bytes).decode("ascii"),
                },
            )
            response.raise_for_status()
            payload = response.json()
            self._last_duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            timing_breakdown = payload.get("timing_breakdown_ms")
            self._last_timing_breakdown_ms = timing_breakdown if isinstance(timing_breakdown, dict) else {}
            self._last_error = None
            return SpeechTranscript(
                text=str(payload.get("text") or "").strip(),
                confidence=payload.get("confidence"),
                provider_id="external_faster_whisper",
                model=str(payload.get("model") or self._model_name),
                duration_ms=payload.get("duration_ms") or self._last_duration_ms,
                timing_breakdown_ms=self._last_timing_breakdown_ms,
                error=payload.get("error"),
            )
        except Exception as exc:
            self._last_duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._last_error = str(exc)
            return SpeechTranscript(
                text="",
                confidence=0.0,
                provider_id="external_faster_whisper",
                model=self._model_name,
                duration_ms=self._last_duration_ms,
                error=self._last_error,
            )
        finally:
            if self._http_client is None:
                client.close()

    def preload(self) -> dict:
        client = self._http_client or client_for_engine(timeout=self._timeout_s, socket_path=self._socket_path)
        try:
            response = client.post(f"{self._base_url}/preload")
            response.raise_for_status()
            payload = response.json()
            self._last_error = payload.get("error")
            return payload
        except Exception as exc:
            self._last_error = str(exc)
            return {
                "provider": "external_faster_whisper",
                "loaded": False,
                "model": self._model_name,
                "error": self._last_error,
            }
        finally:
            if self._http_client is None:
                client.close()

    def status(self) -> dict:
        client = self._http_client or client_for_engine(timeout=min(self._timeout_s, 2.0), socket_path=self._socket_path)
        try:
            response = client.get(f"{self._base_url}/health")
            response.raise_for_status()
            payload = response.json()
            service_error = payload.get("last_error")
            self._last_error = str(service_error) if service_error else None
            return {
                "provider": "external_faster_whisper",
                "healthy": bool(payload.get("healthy", True)),
                "configured": True,
                "base_url": self._base_url,
                "socket_path": str(self._socket_path) if self._socket_path is not None else None,
                "model": payload.get("model") or self._model_name,
                "service": payload,
                "last_duration_ms": self._last_duration_ms,
                "last_timing_breakdown_ms": self._last_timing_breakdown_ms,
                "last_error": self._last_error,
            }
        except Exception as exc:
            self._last_error = str(exc)
            return {
                "provider": "external_faster_whisper",
                "healthy": False,
                "configured": True,
                "base_url": self._base_url,
                "socket_path": str(self._socket_path) if self._socket_path is not None else None,
                "model": self._model_name,
                "last_duration_ms": self._last_duration_ms,
                "last_timing_breakdown_ms": self._last_timing_breakdown_ms,
                "last_error": self._last_error,
            }
        finally:
            if self._http_client is None:
                client.close()


class DeterministicTextToSpeechAdapter:
    def synthesize(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        text: str,
        voice: str | None = None,
        audio_format: str | None = None,
        stream_id: str | None = None,
    ) -> TtsSynthesis:
        synthesis = TtsSynthesis(
            stream_id=stream_id or f"tts-{uuid4().hex[:12]}",
            model_id="deterministic",
            voice_id=voice,
        )
        record_voice_event(
            "tts.synthesized",
            endpoint_id=endpoint_id,
            session_id=session_id,
            provider_id=synthesis.provider_id,
            model_id=synthesis.model_id,
            voice_id=synthesis.voice_id,
            stream_id=synthesis.stream_id,
            content_type=synthesis.content_type,
            text_chars=len(text or ""),
            error=synthesis.error,
        )
        return synthesis

    def status(self) -> dict:
        return {"provider": "deterministic", "healthy": True, "configured": True}


class PiperTextToSpeechAdapter:
    def __init__(
        self,
        *,
        base_url: str | None,
        socket_path: Path | None = None,
        synthesize_path: str = "/api/tts",
        voice: str | None = None,
        output_dir: Path,
        timeout_s: float = 30.0,
        output_sample_rate_hz: int | None = 16000,
        endpoint_sample_rates: dict[str, int] | None = None,
        conversion_sample_rates: dict[str, int] | None = None,
        conversion_policy: str = "blocking_all",
        fallback: TextToSpeechAdapter | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._socket_path = socket_path
        self._synthesize_path = synthesize_path if synthesize_path.startswith("/") else f"/{synthesize_path}"
        self._voice = voice
        self._output_dir = output_dir
        self._timeout_s = timeout_s
        self._output_sample_rate_hz = output_sample_rate_hz
        self._conversion_sample_rates = normalize_tts_conversion_sample_rates(conversion_sample_rates)
        self._conversion_policy = normalize_tts_conversion_policy(conversion_policy)
        self._endpoint_sample_rates: dict[str, int] = {}
        for endpoint_id, sample_rate in (endpoint_sample_rates or {}).items():
            endpoint_id = str(endpoint_id).strip()
            try:
                sample_rate_hz = int(sample_rate)
            except (TypeError, ValueError):
                continue
            if endpoint_id and sample_rate_hz > 0:
                self._endpoint_sample_rates[endpoint_id] = sample_rate_hz
        self._fallback = fallback or DeterministicTextToSpeechAdapter()
        self._http_client = http_client
        self._last_error: str | None = None

    def synthesize(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        text: str,
        voice: str | None = None,
        audio_format: str | None = None,
        stream_id: str | None = None,
    ) -> TtsSynthesis:
        if not self._base_url:
            self._last_error = "missing_piper_base_url"
            return self._fallback.synthesize(endpoint_id=endpoint_id, session_id=session_id, text=text, voice=voice, stream_id=stream_id)

        stream_id = stream_id or f"tts-{uuid4().hex[:12]}"
        client = self._http_client or client_for_engine(timeout=self._timeout_s, socket_path=self._socket_path)
        selected_voice = voice or self._voice or "piper-default"
        timing_breakdown_ms: dict[str, float] = {}
        try:
            piper_started_at = time.perf_counter()
            response = client.post(
                f"{self._base_url}{self._synthesize_path}",
                json={"text": text, "voice": selected_voice},
            )
            response.raise_for_status()
            timing_breakdown_ms["piper_generation_ms"] = elapsed_ms(piper_started_at)
            self._output_dir.mkdir(parents=True, exist_ok=True)
            raw_audio = response.content
            raw_path = self._output_dir / f"{stream_id}.raw.wav"
            target_sample_rate_hz = self._sample_rate_for_endpoint(endpoint_id)
            audio_variant = self._variant_for_sample_rate(target_sample_rate_hz)
            all_variant_paths = {
                variant: self._output_dir / f"{stream_id}.{variant}.wav"
                for variant in self._conversion_sample_rates
            }
            blocking_sample_rates = self._blocking_conversion_sample_rates(audio_variant)
            optional_sample_rates = {
                variant: sample_rate
                for variant, sample_rate in self._conversion_sample_rates.items()
                if variant not in blocking_sample_rates
            }
            blocking_variant_paths = {variant: all_variant_paths[variant] for variant in blocking_sample_rates}
            optional_variant_paths = {variant: all_variant_paths[variant] for variant in optional_sample_rates}
            variant_sample_rates_hz = write_wav_variants_with_soxr(
                raw_audio,
                raw_path=raw_path,
                variant_paths=blocking_variant_paths,
                variant_sample_rates=blocking_sample_rates,
                timing_breakdown_ms=timing_breakdown_ms,
            )
            raw_sample_rate_hz = variant_sample_rates_hz.get("raw")
            audio_variant_paths = {"raw": str(raw_path)}
            audio_variant_paths.update({variant: str(path) for variant, path in blocking_variant_paths.items()})
            planned_audio_variant_paths = {"raw": str(raw_path)}
            planned_audio_variant_paths.update({variant: str(path) for variant, path in all_variant_paths.items()})
            output_sample_rate_hz = variant_sample_rates_hz.get(audio_variant) if audio_variant else raw_sample_rate_hz
            audio_url = tts_audio_base_url(stream_id)
            audio_urls = tts_audio_variant_urls(stream_id, audio_variant_paths)
            endpoint_audio_url = audio_urls.get(audio_variant or "") or audio_urls.get("raw") or audio_url
            content_type = response.headers.get("content-type", "audio/wav")
            sidecar_started_at = time.perf_counter()
            metadata_path, expires_at = write_default_tts_sidecar(
                self._output_dir,
                stream_id,
                metadata={
                    "endpoint_id": endpoint_id,
                    "session_id": session_id,
                    "provider_id": "piper",
                    "model_id": selected_voice,
                    "voice_id": selected_voice,
                    "content_type": content_type,
                    **tts_audio_url_metadata(audio_urls, endpoint_audio_url=endpoint_audio_url),
                    "text_chars": len(text or ""),
                    "audio_variant": audio_variant,
                    "audio_variant_sample_rate_hz": output_sample_rate_hz,
                    "audio_variant_source_sample_rate_hz": raw_sample_rate_hz,
                    "audio_variants": audio_variant_paths,
                    "planned_audio_variants": planned_audio_variant_paths,
                    "pending_audio_variants": {
                        variant: str(path) for variant, path in optional_variant_paths.items()
                    },
                    "ready_audio_variants": list(audio_variant_paths),
                    "conversion_policy": self._conversion_policy,
                    "optional_conversion_status": "pending" if optional_sample_rates else "not_needed",
                    "raw_audio_path": str(raw_path),
                    "raw_sample_rate_hz": raw_sample_rate_hz,
                    "target_sample_rate_hz": target_sample_rate_hz,
                    "output_sample_rate_hz": output_sample_rate_hz,
                    "variant_sample_rates_hz": variant_sample_rates_hz,
                    "voice": selected_voice,
                    "tts_timing_breakdown_ms": dict(timing_breakdown_ms),
                },
            )
            timing_breakdown_ms["sidecar_write_ms"] = elapsed_ms(sidecar_started_at)
            update_tts_sidecar_timing(metadata_path, timing_breakdown_ms)
            if optional_sample_rates:
                self._start_optional_conversions(
                    raw_audio=raw_audio,
                    stream_id=stream_id,
                    metadata_path=metadata_path,
                    endpoint_audio_url=endpoint_audio_url,
                    variant_paths=optional_variant_paths,
                    variant_sample_rates=optional_sample_rates,
                )
            self._last_error = None
            record_voice_event(
                "tts.synthesized",
                endpoint_id=endpoint_id,
                session_id=session_id,
                provider_id="piper",
                model_id=selected_voice,
                voice_id=selected_voice,
                stream_id=stream_id,
                content_type=content_type,
                audio_url=audio_url,
                text_chars=len(text or ""),
                audio_variant=audio_variant,
                audio_variants=audio_variant_paths,
                planned_audio_variants=planned_audio_variant_paths,
                pending_audio_variants={variant: str(path) for variant, path in optional_variant_paths.items()},
                conversion_policy=self._conversion_policy,
                raw_audio_path=str(raw_path),
                raw_sample_rate_hz=raw_sample_rate_hz,
                audio_variant_sample_rate_hz=output_sample_rate_hz,
                audio_variant_source_sample_rate_hz=raw_sample_rate_hz,
                target_sample_rate_hz=target_sample_rate_hz,
                output_sample_rate_hz=output_sample_rate_hz,
                variant_sample_rates_hz=variant_sample_rates_hz,
                tts_timing_breakdown_ms=timing_breakdown_ms,
                metadata_path=str(metadata_path),
                expires_at=expires_at,
                error=None,
            )
            return TtsSynthesis(
                content_type=content_type,
                stream_id=stream_id,
                audio_url=audio_url,
                endpoint_audio_url=endpoint_audio_url,
                audio_urls=audio_urls,
                provider_id="piper",
                model_id=selected_voice,
                voice_id=selected_voice,
                audio_variant=audio_variant,
                audio_variants=audio_variant_paths,
                planned_audio_variants=planned_audio_variant_paths,
                pending_audio_variants={variant: str(path) for variant, path in optional_variant_paths.items()},
                conversion_policy=self._conversion_policy,
                raw_audio_path=str(raw_path),
                raw_sample_rate_hz=raw_sample_rate_hz,
                audio_variant_sample_rate_hz=output_sample_rate_hz,
                audio_variant_source_sample_rate_hz=raw_sample_rate_hz,
                output_sample_rate_hz=output_sample_rate_hz,
                variant_sample_rates_hz=variant_sample_rates_hz,
                timing_breakdown_ms=timing_breakdown_ms,
                metadata_path=str(metadata_path),
                expires_at=expires_at,
                ttl_seconds=DEFAULT_TTS_AUDIO_TTL_SECONDS,
            )
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("Piper TTS failed; using deterministic fallback: error=%s", self._last_error)
            record_voice_event(
                "tts.failed",
                endpoint_id=endpoint_id,
                session_id=session_id,
                provider_id="piper",
                stream_id=stream_id,
                text_chars=len(text or ""),
                error=self._last_error,
            )
            return self._fallback.synthesize(endpoint_id=endpoint_id, session_id=session_id, text=text, voice=voice, stream_id=stream_id)
        finally:
            if self._http_client is None:
                client.close()

    def status(self) -> dict:
        return {
            "provider": "piper",
            "healthy": self._last_error is None,
            "configured": bool(self._base_url),
            "base_url": self._base_url,
            "socket_path": str(self._socket_path) if self._socket_path is not None else None,
            "synthesize_path": self._synthesize_path,
            "voice": self._voice,
            "output_sample_rate_hz": self._output_sample_rate_hz,
            "endpoint_sample_rates": dict(self._endpoint_sample_rates),
            "conversion_sample_rates": dict(self._conversion_sample_rates),
            "conversion_policy": self._conversion_policy,
            "last_error": self._last_error,
            "fallback": self._fallback.status(),
        }

    def _sample_rate_for_endpoint(self, endpoint_id: str) -> int | None:
        if endpoint_id in self._endpoint_sample_rates:
            return self._endpoint_sample_rates[endpoint_id]
        return self._output_sample_rate_hz

    def _variant_for_sample_rate(self, sample_rate_hz: int | None) -> str:
        if sample_rate_hz is None or sample_rate_hz <= 0:
            return "raw"
        for variant, variant_sample_rate_hz in self._conversion_sample_rates.items():
            if sample_rate_hz == variant_sample_rate_hz:
                return variant
        return "raw"

    def _blocking_conversion_sample_rates(self, audio_variant: str | None) -> dict[str, int]:
        if self._conversion_policy == "endpoint_required_sync":
            if audio_variant and audio_variant in self._conversion_sample_rates:
                return {audio_variant: self._conversion_sample_rates[audio_variant]}
            return {}
        return dict(self._conversion_sample_rates)

    def _start_optional_conversions(
        self,
        *,
        raw_audio: bytes,
        stream_id: str,
        metadata_path: Path,
        endpoint_audio_url: str,
        variant_paths: dict[str, Path],
        variant_sample_rates: dict[str, int],
    ) -> None:
        thread = threading.Thread(
            target=complete_optional_tts_conversions,
            kwargs={
                "raw_audio": raw_audio,
                "stream_id": stream_id,
                "metadata_path": metadata_path,
                "endpoint_audio_url": endpoint_audio_url,
                "variant_paths": variant_paths,
                "variant_sample_rates": variant_sample_rates,
            },
            name=f"tts-convert-{stream_id}",
            daemon=True,
        )
        thread.start()


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

    def synthesize(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        text: str,
        voice: str | None = None,
        audio_format: str | None = None,
        stream_id: str | None = None,
    ) -> TtsSynthesis:
        stream_id = stream_id or f"tts-{uuid4().hex[:12]}"
        response_format = audio_format or self._response_format
        content_type = self._content_type(response_format)
        selected_voice = voice or self._voice
        if not self._api_key:
            self._last_error = "missing_api_key"
            record_voice_event(
                "tts.failed",
                endpoint_id=endpoint_id,
                session_id=session_id,
                provider_id="openai",
                model=self._model,
                voice=selected_voice,
                model_id=self._model,
                voice_id=selected_voice,
                stream_id=stream_id,
                content_type=content_type,
                text_chars=len(text or ""),
                error="missing_api_key",
            )
            return TtsSynthesis(
                content_type=content_type,
                stream_id=stream_id,
                provider_id="openai",
                model_id=self._model,
                voice_id=selected_voice,
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
                        "voice": selected_voice,
                        "input": text,
                        "response_format": response_format,
                    },
                )
            finally:
                if self._http_client is None:
                    client.close()
            response.raise_for_status()
            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / f"{stream_id}.{response_format}"
            output_path.write_bytes(response.content)
            audio_url = tts_audio_base_url(stream_id)
            audio_urls = {"raw": audio_url}
            metadata_path, expires_at = write_default_tts_sidecar(
                self._output_dir,
                stream_id,
                metadata={
                    "endpoint_id": endpoint_id,
                    "session_id": session_id,
                    "provider_id": "openai",
                    "model_id": self._model,
                    "voice_id": selected_voice,
                    "model": self._model,
                    "voice": selected_voice,
                    "content_type": content_type,
                    **tts_audio_url_metadata(audio_urls, endpoint_audio_url=audio_url),
                    "text_chars": len(text or ""),
                    "requested_format": response_format,
                },
            )
            self._last_error = None
            record_voice_event(
                "tts.synthesized",
                endpoint_id=endpoint_id,
                session_id=session_id,
                provider_id="openai",
                model=self._model,
                voice=selected_voice,
                model_id=self._model,
                voice_id=selected_voice,
                stream_id=stream_id,
                content_type=content_type,
                audio_url=audio_url,
                text_chars=len(text or ""),
                metadata_path=str(metadata_path),
                expires_at=expires_at,
                error=None,
            )
            return TtsSynthesis(
                content_type=content_type,
                stream_id=stream_id,
                audio_url=audio_url,
                endpoint_audio_url=audio_url,
                audio_urls=audio_urls,
                provider_id="openai",
                model_id=self._model,
                voice_id=selected_voice,
                metadata_path=str(metadata_path),
                expires_at=expires_at,
                ttl_seconds=DEFAULT_TTS_AUDIO_TTL_SECONDS,
            )
        except Exception as exc:
            self._last_error = str(exc)
            record_voice_event(
                "tts.failed",
                endpoint_id=endpoint_id,
                session_id=session_id,
                provider_id="openai",
                model=self._model,
                voice=selected_voice,
                model_id=self._model,
                voice_id=selected_voice,
                stream_id=stream_id,
                content_type=content_type,
                text_chars=len(text or ""),
                error=self._last_error,
            )
            return TtsSynthesis(
                content_type=content_type,
                stream_id=stream_id,
                provider_id="openai",
                model_id=self._model,
                voice_id=selected_voice,
                error=self._last_error,
            )

    def _content_type(self, response_format: str | None = None) -> str:
        normalized_format = response_format or self._response_format
        if normalized_format == "mp3":
            return "audio/mpeg"
        if normalized_format == "opus":
            return "audio/ogg"
        return f"audio/{normalized_format}"

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


def normalize_tts_conversion_sample_rates(sample_rates: dict[str, int] | None) -> dict[str, int]:
    normalized: dict[str, int] = {}
    allowed = {16000: "16k", 22050: "22050", 48000: "48k"}
    for _variant, sample_rate in (sample_rates or DEFAULT_PIPER_TTS_AUDIO_VARIANT_SAMPLE_RATES).items():
        try:
            parsed_sample_rate = int(sample_rate)
        except (TypeError, ValueError):
            continue
        variant = allowed.get(parsed_sample_rate)
        if variant:
            normalized[variant] = parsed_sample_rate
    return normalized or dict(DEFAULT_PIPER_TTS_AUDIO_VARIANT_SAMPLE_RATES)


def normalize_tts_conversion_policy(policy: str | None) -> str:
    normalized = str(policy or "").strip().lower()
    return normalized if normalized in {"blocking_all", "endpoint_required_sync"} else "blocking_all"


def tts_audio_variant_urls(stream_id: str, variants: dict[str, Any]) -> dict[str, str]:
    base_url = tts_audio_base_url(stream_id)
    urls: dict[str, str] = {}
    for variant in variants:
        normalized_variant = str(variant or "").strip().lower()
        if normalized_variant == "raw":
            urls["raw"] = f"{base_url}raw"
        elif normalized_variant:
            urls[normalized_variant] = f"{base_url}{normalized_variant}"
    return urls


def tts_audio_url_metadata(audio_urls: dict[str, str], *, endpoint_audio_url: str | None) -> dict[str, Any]:
    audio_url = _base_url_from_variant_urls(audio_urls) or endpoint_audio_url
    metadata: dict[str, Any] = {
        "audio_url": audio_url,
        "audio_url_raw": audio_urls.get("raw"),
        "audio_urls": dict(audio_urls),
        "endpoint_audio_url": endpoint_audio_url,
    }
    for variant, url in audio_urls.items():
        metadata[f"audio_url_{variant}"] = url
        if variant == "48k":
            metadata["audio_url_48K"] = url
    return metadata


def tts_audio_base_url(stream_id: str) -> str:
    return f"/api/voice/tts/{stream_id}/"


def _base_url_from_variant_urls(audio_urls: dict[str, str]) -> str | None:
    for url in audio_urls.values():
        if not url:
            continue
        if url.endswith("/"):
            return url
        head, separator, _tail = url.rstrip("/").rpartition("/")
        if separator:
            return f"{head}/"
    return None


def write_default_tts_sidecar(output_dir: Path, stream_id: str, *, metadata: dict[str, Any]) -> tuple[Path, str]:
    created_at = datetime.now(UTC)
    expires_at = created_at + timedelta(seconds=DEFAULT_TTS_AUDIO_TTL_SECONDS)
    payload = {
        "stream_id": stream_id,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "ttl_seconds": DEFAULT_TTS_AUDIO_TTL_SECONDS,
        "lifetime": "short_lived",
        **metadata,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dir / f"{stream_id}.json"
    metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return metadata_path, expires_at.isoformat()


def normalize_wav_sample_rate(audio: bytes, target_sample_rate_hz: int | None) -> bytes:
    if target_sample_rate_hz is None or target_sample_rate_hz <= 0:
        return audio
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())
    except wave.Error:
        return audio

    if sample_rate == target_sample_rate_hz:
        return audio
    if sample_width != 2 or channels <= 0:
        return audio

    resampled = resample_pcm16le(frames, source_rate=sample_rate, target_rate=target_sample_rate_hz, channels=channels)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(target_sample_rate_hz)
        wav_file.writeframes(resampled)
    return buffer.getvalue()


def write_wav_variants_with_soxr(
    audio: bytes,
    *,
    raw_path: Path,
    variant_paths: dict[str, Path],
    variant_sample_rates: dict[str, int],
    timing_breakdown_ms: dict[str, float] | None = None,
) -> dict[str, int | None]:
    raw_started_at = time.perf_counter()
    raw_path.write_bytes(audio)
    record_elapsed_ms(timing_breakdown_ms, "raw_save_ms", raw_started_at)
    conversion_started_at = time.perf_counter()
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            if sample_width != 2 or channels <= 0 or sample_rate <= 0:
                copy_audio_to_variant_paths(audio, variant_paths, timing_breakdown_ms=timing_breakdown_ms)
                record_elapsed_ms(timing_breakdown_ms, "conversion_total_ms", conversion_started_at)
                return {"raw": sample_rate if sample_rate > 0 else None} | {
                    variant: sample_rate if sample_rate > 0 else None for variant in variant_paths
                }
            if all(sample_rate == target_rate for target_rate in variant_sample_rates.values()):
                copy_audio_to_variant_paths(audio, variant_paths, timing_breakdown_ms=timing_breakdown_ms)
                record_elapsed_ms(timing_breakdown_ms, "conversion_total_ms", conversion_started_at)
                return {"raw": sample_rate} | {variant: sample_rate for variant in variant_paths}

            with ExitStack() as stack:
                writers: dict[str, wave.Wave_write] = {}
                resamplers: dict[str, soxr.ResampleStream | None] = {}
                for variant, target_rate in variant_sample_rates.items():
                    path = variant_paths[variant]
                    if target_rate == sample_rate:
                        variant_started_at = time.perf_counter()
                        path.write_bytes(audio)
                        record_elapsed_ms(timing_breakdown_ms, f"conversion_{variant}_ms", variant_started_at)
                        continue
                    writer = stack.enter_context(wave.open(str(path), "wb"))
                    writer.setnchannels(channels)
                    writer.setsampwidth(sample_width)
                    writer.setframerate(target_rate)
                    writers[variant] = writer
                    resamplers[variant] = soxr.ResampleStream(sample_rate, target_rate, channels, dtype="int16")

                while True:
                    frame_bytes = wav_file.readframes(PIPER_TTS_AUDIO_CHUNK_FRAMES)
                    if not frame_bytes:
                        break
                    samples = pcm16le_bytes_to_ndarray(frame_bytes, channels=channels)
                    for variant, resampler in resamplers.items():
                        if resampler is None:
                            continue
                        variant_started_at = time.perf_counter()
                        resampled = resampler.resample_chunk(samples, last=False)
                        if resampled.size:
                            writers[variant].writeframes(ndarray_to_pcm16le_bytes(resampled))
                        add_elapsed_ms(timing_breakdown_ms, f"conversion_{variant}_ms", variant_started_at)

                empty = empty_pcm16le_array(channels=channels)
                for variant, resampler in resamplers.items():
                    if resampler is None:
                        continue
                    variant_started_at = time.perf_counter()
                    resampled = resampler.resample_chunk(empty, last=True)
                    if resampled.size:
                        writers[variant].writeframes(ndarray_to_pcm16le_bytes(resampled))
                    add_elapsed_ms(timing_breakdown_ms, f"conversion_{variant}_ms", variant_started_at)
    except wave.Error:
        copy_audio_to_variant_paths(audio, variant_paths, timing_breakdown_ms=timing_breakdown_ms)
        record_elapsed_ms(timing_breakdown_ms, "conversion_total_ms", conversion_started_at)
        return {"raw": None} | {variant: None for variant in variant_paths}

    record_elapsed_ms(timing_breakdown_ms, "conversion_total_ms", conversion_started_at)
    return {"raw": sample_rate} | dict(variant_sample_rates)


def copy_audio_to_variant_paths(
    audio: bytes,
    variant_paths: dict[str, Path],
    *,
    timing_breakdown_ms: dict[str, float] | None = None,
) -> None:
    for variant, path in variant_paths.items():
        variant_started_at = time.perf_counter()
        path.write_bytes(audio)
        record_elapsed_ms(timing_breakdown_ms, f"conversion_{variant}_ms", variant_started_at)


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def record_elapsed_ms(timings: dict[str, float] | None, key: str, started_at: float) -> None:
    if timings is not None:
        timings[key] = elapsed_ms(started_at)


def add_elapsed_ms(timings: dict[str, float] | None, key: str, started_at: float) -> None:
    if timings is not None:
        timings[key] = round(timings.get(key, 0.0) + ((time.perf_counter() - started_at) * 1000), 2)


def update_tts_sidecar_timing(metadata_path: Path, timing_breakdown_ms: dict[str, float]) -> None:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(metadata, dict):
        return
    metadata["tts_timing_breakdown_ms"] = dict(timing_breakdown_ms)
    try:
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return


def complete_optional_tts_conversions(
    *,
    raw_audio: bytes,
    stream_id: str,
    metadata_path: Path,
    endpoint_audio_url: str,
    variant_paths: dict[str, Path],
    variant_sample_rates: dict[str, int],
) -> None:
    timing_breakdown_ms: dict[str, float] = {}
    variant_sample_rates_hz: dict[str, int | None] = {}
    try:
        for variant, target_sample_rate_hz in variant_sample_rates.items():
            started_at = time.perf_counter()
            converted_audio = normalize_wav_sample_rate(raw_audio, target_sample_rate_hz)
            variant_paths[variant].write_bytes(converted_audio)
            timing_breakdown_ms[f"background_conversion_{variant}_ms"] = elapsed_ms(started_at)
            variant_sample_rates_hz[variant] = wav_sample_rate_hz(converted_audio) or target_sample_rate_hz
    except Exception as exc:
        update_tts_sidecar_optional_conversion(
            stream_id=stream_id,
            metadata_path=metadata_path,
            endpoint_audio_url=endpoint_audio_url,
            variant_paths=variant_paths,
            variant_sample_rates_hz=variant_sample_rates_hz,
            timing_breakdown_ms=timing_breakdown_ms,
            status="failed",
            error=str(exc),
        )
        record_voice_event(
            "tts.optional_conversion_failed",
            stream_id=stream_id,
            error=str(exc),
            pending_audio_variants={variant: str(path) for variant, path in variant_paths.items()},
        )
        return
    update_tts_sidecar_optional_conversion(
        stream_id=stream_id,
        metadata_path=metadata_path,
        endpoint_audio_url=endpoint_audio_url,
        variant_paths=variant_paths,
        variant_sample_rates_hz=variant_sample_rates_hz,
        timing_breakdown_ms=timing_breakdown_ms,
        status="completed",
        error=None,
    )
    record_voice_event(
        "tts.optional_conversion_completed",
        stream_id=stream_id,
        audio_variants={variant: str(path) for variant, path in variant_paths.items()},
        variant_sample_rates_hz=variant_sample_rates_hz,
        tts_timing_breakdown_ms=timing_breakdown_ms,
    )


def update_tts_sidecar_optional_conversion(
    *,
    stream_id: str,
    metadata_path: Path,
    endpoint_audio_url: str,
    variant_paths: dict[str, Path],
    variant_sample_rates_hz: dict[str, int | None],
    timing_breakdown_ms: dict[str, float],
    status: str,
    error: str | None,
) -> None:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(metadata, dict):
        return
    audio_variants = metadata.get("audio_variants") if isinstance(metadata.get("audio_variants"), dict) else {}
    audio_variants.update({variant: str(path) for variant, path in variant_paths.items()})
    metadata["audio_variants"] = audio_variants
    metadata["pending_audio_variants"] = {}
    metadata["ready_audio_variants"] = list(audio_variants)
    metadata["optional_conversion_status"] = status
    metadata["optional_conversion_completed_at"] = datetime.now(UTC).isoformat()
    metadata["optional_conversion_error"] = error
    sample_rates = (
        metadata.get("variant_sample_rates_hz") if isinstance(metadata.get("variant_sample_rates_hz"), dict) else {}
    )
    sample_rates.update(variant_sample_rates_hz)
    metadata["variant_sample_rates_hz"] = sample_rates
    timings = (
        metadata.get("tts_timing_breakdown_ms") if isinstance(metadata.get("tts_timing_breakdown_ms"), dict) else {}
    )
    timings.update(timing_breakdown_ms)
    metadata["tts_timing_breakdown_ms"] = timings
    audio_urls = tts_audio_variant_urls(stream_id, audio_variants)
    metadata.update(tts_audio_url_metadata(audio_urls, endpoint_audio_url=endpoint_audio_url))
    try:
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return


def pcm16le_bytes_to_ndarray(frame_bytes: bytes, *, channels: int) -> np.ndarray:
    samples = np.frombuffer(frame_bytes, dtype="<i2")
    if channels > 1:
        frame_count = samples.size // channels
        samples = samples[: frame_count * channels].reshape(frame_count, channels)
    return samples


def empty_pcm16le_array(*, channels: int) -> np.ndarray:
    if channels > 1:
        return np.empty((0, channels), dtype=np.int16)
    return np.empty((0,), dtype=np.int16)


def ndarray_to_pcm16le_bytes(samples: np.ndarray) -> bytes:
    if samples.dtype != np.int16:
        samples = samples.astype(np.int16)
    return np.asarray(samples, dtype="<i2").tobytes()


def wav_sample_rate_hz(audio: bytes) -> int | None:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
    except wave.Error:
        return None
    return sample_rate if sample_rate > 0 else None


def resample_pcm16le(data: bytes, *, source_rate: int, target_rate: int, channels: int) -> bytes:
    frame_size = channels * 2
    frame_count = len(data) // frame_size
    if frame_count <= 1 or source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
        return data

    samples = pcm16le_bytes_to_ndarray(data, channels=channels)
    converted = soxr.resample(samples, source_rate, target_rate, quality="HQ")
    return ndarray_to_pcm16le_bytes(converted)


class VoiceTurnPipeline:
    def __init__(
        self,
        *,
        assistant_service: AssistantTurnService,
        stt_adapter: SpeechToTextAdapter | None = None,
        tts_adapter: TextToSpeechAdapter | None = None,
        endpoint_voices: dict[str, str] | None = None,
    ) -> None:
        self._assistant_service = assistant_service
        self._stt_adapter = stt_adapter or DeterministicSpeechToTextAdapter()
        self._tts_adapter = tts_adapter or DeterministicTextToSpeechAdapter()
        self._endpoint_voices = dict(endpoint_voices or {})

    def complete_turn(self, audio: VoiceTurnAudioSummary) -> VoiceTurnResult:
        turn_started_at = time.perf_counter()
        stt_started_at = time.perf_counter()
        transcript = self._stt_adapter.transcribe(audio)
        stt_ms = round((time.perf_counter() - stt_started_at) * 1000, 2)
        record_voice_event(
            "stt.failed" if transcript.error else "stt.completed",
            endpoint_id=audio.endpoint_id,
            session_id=audio.session_id,
            provider_id=transcript.provider_id,
            model=transcript.model,
            confidence=transcript.confidence,
            duration_ms=transcript.duration_ms,
            text_chars=len(transcript.text or ""),
            transcript_text=transcript.text,
            error=transcript.error,
            chunk_count=audio.chunk_count,
            stt_ms=stt_ms,
        )
        assistant_started_at = time.perf_counter()
        assistant_response = self._assistant_service.handle_turn(
            AssistantTurnRequest(
                endpoint_id=audio.endpoint_id,
                session_id=audio.session_id,
                text=transcript.text or " ",
            )
        )
        if assistant_response.heard_text != transcript.text:
            transcript = replace(transcript, text=assistant_response.heard_text)
        assistant_ms = round((time.perf_counter() - assistant_started_at) * 1000, 2)
        tts_started_at = time.perf_counter()
        tts = self._tts_adapter.synthesize(
            endpoint_id=audio.endpoint_id,
            session_id=audio.session_id,
            text=assistant_response.spoken_text,
            voice=self._voice_for_endpoint(audio.endpoint_id),
        )
        tts_ms = round((time.perf_counter() - tts_started_at) * 1000, 2)
        timings = VoiceTurnTimings(
            stt_ms=stt_ms,
            assistant_ms=assistant_ms,
            tts_ms=tts_ms,
            total_ms=round((time.perf_counter() - turn_started_at) * 1000, 2),
        )
        log.info(
            "Voice turn pipeline completed: endpoint_id=%s session_id=%s stt_ms=%s assistant_ms=%s tts_ms=%s total_ms=%s",
            audio.endpoint_id,
            audio.session_id,
            timings.stt_ms,
            timings.assistant_ms,
            timings.tts_ms,
            timings.total_ms,
        )
        return VoiceTurnResult(
            transcript=transcript,
            assistant_response=assistant_response,
            tts=tts,
            timings=timings,
        )

    def status(self) -> dict:
        stt_status = self._stt_adapter.status()
        tts_status = self._tts_adapter.status()
        if not (tts_status.get("model") or tts_status.get("model_id") or tts_status.get("voice")):
            first_endpoint_voice = next(iter(self._endpoint_voices.values()), None)
            if first_endpoint_voice:
                tts_status = {**tts_status, "model": first_endpoint_voice}
        return {
            "assistant": self._assistant_service.status(),
            "stt": _engine_status(
                role="stt_engine",
                status=stt_status,
                fallback_implementation=str(stt_status.get("provider") or "unknown"),
            ),
            "tts": _engine_status(
                role="tts_engine",
                status=tts_status,
                fallback_implementation=str(tts_status.get("provider") or "unknown"),
            ),
            "endpoint_voices": dict(self._endpoint_voices),
        }

    def synthesize_reply(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        text: str,
        voice: str | None = None,
        audio_format: str | None = None,
        stream_id: str | None = None,
    ) -> TtsSynthesis:
        return self._tts_adapter.synthesize(
            endpoint_id=endpoint_id,
            session_id=session_id,
            text=text,
            voice=voice or self._voice_for_endpoint(endpoint_id),
            audio_format=audio_format,
            stream_id=stream_id,
        )

    def _voice_for_endpoint(self, endpoint_id: str) -> str | None:
        return self._endpoint_voices.get(endpoint_id)

    def preload_stt(self) -> dict | None:
        preload = getattr(self._stt_adapter, "preload", None)
        if not callable(preload):
            return None
        return preload()


def _engine_status(*, role: str, status: dict, fallback_implementation: str) -> dict:
    provider = str(status.get("provider") or fallback_implementation or "unknown")
    model = status.get("model") or status.get("model_id")
    health = {
        "engine_role": role,
        "active_implementation": provider,
        "provider": provider,
        "model": model,
        "healthy": bool(status.get("healthy", True)),
        "configured": bool(status.get("configured", True)),
        "last_error": status.get("last_error") or status.get("error"),
    }
    enriched = dict(status)
    enriched["engine_role"] = role
    enriched["implementation"] = provider
    enriched["implementation_health"] = health
    return enriched


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
            language=settings.voice_stt_faster_whisper_language,
            beam_size=settings.voice_stt_faster_whisper_beam_size,
            best_of=settings.voice_stt_faster_whisper_best_of,
            without_timestamps=settings.voice_stt_faster_whisper_without_timestamps,
            word_timestamps=settings.voice_stt_faster_whisper_word_timestamps,
            max_initial_timestamp=settings.voice_stt_faster_whisper_max_initial_timestamp,
        )
    elif settings.voice_stt_provider == "external_faster_whisper":
        stt_adapter = ExternalFasterWhisperSpeechToTextAdapter(
            base_url=settings.resolved_voice_stt_service_base_url(),
            socket_path=settings.resolved_voice_stt_service_socket_path(),
            model_name=settings.voice_stt_faster_whisper_model,
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
    elif settings.voice_tts_provider == "piper":
        tts_adapter = PiperTextToSpeechAdapter(
            base_url=settings.resolved_voice_tts_piper_base_url(),
            socket_path=settings.resolved_voice_tts_piper_socket_path(),
            synthesize_path=settings.voice_tts_piper_synthesize_path,
            voice=settings.voice_tts_piper_voice,
            output_dir=settings.runtime_dir / "voice_tts",
            timeout_s=settings.voice_tts_timeout_s,
            output_sample_rate_hz=settings.voice_tts_output_sample_rate_hz,
            endpoint_sample_rates=settings.resolved_voice_tts_endpoint_sample_rates(),
            conversion_sample_rates=settings.resolved_voice_tts_conversion_sample_rates(),
            conversion_policy=settings.resolved_voice_tts_conversion_policy(),
            fallback=DeterministicTextToSpeechAdapter(),
        )

    return VoiceTurnPipeline(
        assistant_service=assistant_service,
        stt_adapter=stt_adapter,
        tts_adapter=tts_adapter,
        endpoint_voices=settings.resolved_voice_tts_endpoint_voices(),
    )
