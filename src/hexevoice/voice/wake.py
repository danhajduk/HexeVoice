from __future__ import annotations

from dataclasses import dataclass
import base64
import struct
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from hexevoice.voice.contracts import VoiceAudioChunkPayload

if TYPE_CHECKING:
    from hexevoice.config.settings import Settings


@dataclass(frozen=True)
class WakeDetectionResult:
    detected: bool
    confidence: float | None = None
    model: str | None = None
    reason: str | None = None


class WakeDetector(Protocol):
    def inspect_chunk(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        chunk: VoiceAudioChunkPayload,
    ) -> WakeDetectionResult:
        ...

    def status(self) -> dict[str, Any]:
        ...


class DeterministicWakeDetector:
    def __init__(self, *, detect_on_chunk_index: int | None = None, marker: bytes = b"WAKE") -> None:
        self._detect_on_chunk_index = detect_on_chunk_index
        self._marker = marker
        self._last_detection: WakeDetectionResult | None = None

    def inspect_chunk(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        chunk: VoiceAudioChunkPayload,
    ) -> WakeDetectionResult:
        if self._detect_on_chunk_index is not None and chunk.chunk_index == self._detect_on_chunk_index:
            self._last_detection = WakeDetectionResult(detected=True, confidence=1.0, model="deterministic")
            return self._last_detection

        if chunk.payload_base64:
            try:
                payload = base64.b64decode(chunk.payload_base64, validate=True)
            except ValueError:
                payload = b""
            if self._marker in payload:
                self._last_detection = WakeDetectionResult(detected=True, confidence=1.0, model="deterministic")
                return self._last_detection

        self._last_detection = WakeDetectionResult(detected=False, model="deterministic")
        return self._last_detection

    def status(self) -> dict[str, Any]:
        return {
            "provider": "deterministic",
            "healthy": True,
            "configured": True,
            "loaded": True,
            "last_detection": _result_status(self._last_detection),
        }


class OpenWakeWordWakeDetector:
    def __init__(
        self,
        *,
        threshold: float = 0.5,
        wakeword_models: list[str] | None = None,
        auto_download_models: bool = False,
        enable_speex_noise_suppression: bool = False,
        vad_threshold: float | None = None,
        buffer_ms: int = 1280,
        prediction_frame_ms: int = 80,
    ) -> None:
        self._threshold = threshold
        self._wakeword_models = wakeword_models
        self._auto_download_models = auto_download_models
        self._enable_speex_noise_suppression = enable_speex_noise_suppression
        self._vad_threshold = vad_threshold
        self._buffer_ms = buffer_ms
        self._prediction_frame_ms = prediction_frame_ms
        self._model = None
        self._load_error: str | None = None
        self._audio_buffers: dict[tuple[str, str], bytearray] = {}
        self._last_detection: WakeDetectionResult | None = None

    def inspect_chunk(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        chunk: VoiceAudioChunkPayload,
    ) -> WakeDetectionResult:
        if not chunk.payload_base64:
            self._last_detection = WakeDetectionResult(
                detected=False,
                model="openwakeword",
                reason="empty_audio_payload",
            )
            return self._last_detection

        model = self._load_model()
        if model is None:
            self._last_detection = WakeDetectionResult(detected=False, model="openwakeword", reason=self._load_error)
            return self._last_detection

        try:
            audio_bytes = base64.b64decode(chunk.payload_base64, validate=True)
            buffered_audio = self._append_audio(
                endpoint_id=endpoint_id,
                session_id=session_id,
                audio_bytes=audio_bytes,
                chunk=chunk,
            )
            if len(buffered_audio) < self._prediction_frame_bytes(chunk):
                self._last_detection = WakeDetectionResult(
                    detected=False,
                    model="openwakeword",
                    reason="insufficient_audio",
                )
                return self._last_detection
            samples = self._pcm_s16le_to_samples(buffered_audio)
            prediction = model.predict(samples)
        except Exception as exc:  # pragma: no cover - depends on optional runtime package/model
            self._last_detection = WakeDetectionResult(detected=False, model="openwakeword", reason=str(exc))
            return self._last_detection

        if not prediction:
            self._last_detection = WakeDetectionResult(detected=False, model="openwakeword", reason="no_prediction")
            return self._last_detection

        model_name, confidence = max(prediction.items(), key=lambda item: item[1])
        confidence = float(confidence)
        self._last_detection = WakeDetectionResult(
            detected=bool(confidence >= self._threshold),
            confidence=confidence,
            model=str(model_name),
            reason=None if confidence >= self._threshold else "below_threshold",
        )
        return self._last_detection

    def status(self) -> dict[str, Any]:
        return {
            "provider": "openwakeword",
            "healthy": self._load_error is None,
            "configured": True,
            "loaded": self._model is not None,
            "load_error": self._load_error,
            "threshold": self._threshold,
            "models": self._wakeword_models,
            "auto_download_models": self._auto_download_models,
            "speex_noise_suppression": self._enable_speex_noise_suppression,
            "vad_threshold": self._vad_threshold,
            "buffer_ms": self._buffer_ms,
            "prediction_frame_ms": self._prediction_frame_ms,
            "active_buffers": len(self._audio_buffers),
            "last_detection": _result_status(self._last_detection),
        }

    def preload(self) -> dict[str, Any]:
        self._load_model()
        return self.status()

    def _load_model(self):
        if self._model is not None:
            return self._model
        if self._load_error is not None:
            return None
        try:
            from openwakeword.model import Model

            if self._auto_download_models:
                from openwakeword import utils

                utils.download_models()

            model_kwargs = {
                "enable_speex_noise_suppression": self._enable_speex_noise_suppression,
            }
            if self._wakeword_models:
                model_kwargs["wakeword_models"] = self._wakeword_models
            if self._vad_threshold is not None:
                model_kwargs["vad_threshold"] = self._vad_threshold

            self._model = Model(**model_kwargs)
        except Exception as exc:  # pragma: no cover - optional dependency is usually absent in tests
            self._load_error = str(exc)
        return self._model

    @staticmethod
    def _pcm_s16le_to_samples(audio_bytes: bytes):
        try:
            import numpy as np

            return np.frombuffer(audio_bytes, dtype="<i2")
        except Exception:
            sample_count = len(audio_bytes) // 2
            if sample_count == 0:
                return []
            return struct.unpack(f"<{sample_count}h", audio_bytes[: sample_count * 2])

    def _append_audio(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        audio_bytes: bytes,
        chunk: VoiceAudioChunkPayload,
    ) -> bytes:
        key = (endpoint_id, session_id)
        buffer = self._audio_buffers.setdefault(key, bytearray())
        buffer.extend(audio_bytes)
        max_bytes = self._buffer_bytes(chunk)
        if len(buffer) > max_bytes:
            del buffer[: len(buffer) - max_bytes]
        return bytes(buffer[-self._prediction_frame_bytes(chunk) :])

    def _buffer_bytes(self, chunk: VoiceAudioChunkPayload) -> int:
        bytes_per_ms = self._bytes_per_ms(chunk)
        return max(self._prediction_frame_bytes(chunk), int(bytes_per_ms * self._buffer_ms))

    def _prediction_frame_bytes(self, chunk: VoiceAudioChunkPayload) -> int:
        return int(self._bytes_per_ms(chunk) * self._prediction_frame_ms)

    @staticmethod
    def _bytes_per_ms(chunk: VoiceAudioChunkPayload) -> float:
        return chunk.audio_format.sample_rate_hz * chunk.audio_format.channels * 2 / 1000


def build_wake_detector(settings: "Settings") -> WakeDetector:
    if settings.voice_wake_provider == "deterministic":
        return DeterministicWakeDetector()

    return OpenWakeWordWakeDetector(
        threshold=settings.voice_wake_threshold,
        wakeword_models=_split_csv(settings.voice_wake_models),
        auto_download_models=settings.voice_wake_auto_download_models,
        enable_speex_noise_suppression=settings.voice_wake_enable_speex_noise_suppression,
        vad_threshold=settings.voice_wake_vad_threshold,
        buffer_ms=settings.voice_wake_buffer_ms,
        prediction_frame_ms=settings.voice_wake_prediction_frame_ms,
    )


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _result_status(result: WakeDetectionResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "detected": result.detected,
        "confidence": result.confidence,
        "model": result.model,
        "reason": result.reason,
    }
