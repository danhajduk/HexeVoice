from __future__ import annotations

from dataclasses import dataclass
import base64
import struct
from typing import TYPE_CHECKING
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


class DeterministicWakeDetector:
    def __init__(self, *, detect_on_chunk_index: int | None = None, marker: bytes = b"WAKE") -> None:
        self._detect_on_chunk_index = detect_on_chunk_index
        self._marker = marker

    def inspect_chunk(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        chunk: VoiceAudioChunkPayload,
    ) -> WakeDetectionResult:
        if self._detect_on_chunk_index is not None and chunk.chunk_index == self._detect_on_chunk_index:
            return WakeDetectionResult(detected=True, confidence=1.0, model="deterministic")

        if chunk.payload_base64:
            try:
                payload = base64.b64decode(chunk.payload_base64, validate=True)
            except ValueError:
                payload = b""
            if self._marker in payload:
                return WakeDetectionResult(detected=True, confidence=1.0, model="deterministic")

        return WakeDetectionResult(detected=False, model="deterministic")


class OpenWakeWordWakeDetector:
    def __init__(
        self,
        *,
        threshold: float = 0.5,
        wakeword_models: list[str] | None = None,
        auto_download_models: bool = False,
        enable_speex_noise_suppression: bool = False,
        vad_threshold: float | None = None,
    ) -> None:
        self._threshold = threshold
        self._wakeword_models = wakeword_models
        self._auto_download_models = auto_download_models
        self._enable_speex_noise_suppression = enable_speex_noise_suppression
        self._vad_threshold = vad_threshold
        self._model = None
        self._load_error: str | None = None

    def inspect_chunk(
        self,
        *,
        endpoint_id: str,
        session_id: str,
        chunk: VoiceAudioChunkPayload,
    ) -> WakeDetectionResult:
        if not chunk.payload_base64:
            return WakeDetectionResult(detected=False, model="openwakeword", reason="empty_audio_payload")

        model = self._load_model()
        if model is None:
            return WakeDetectionResult(detected=False, model="openwakeword", reason=self._load_error)

        try:
            audio_bytes = base64.b64decode(chunk.payload_base64, validate=True)
            samples = self._pcm_s16le_to_samples(audio_bytes)
            prediction = model.predict(samples)
        except Exception as exc:  # pragma: no cover - depends on optional runtime package/model
            return WakeDetectionResult(detected=False, model="openwakeword", reason=str(exc))

        if not prediction:
            return WakeDetectionResult(detected=False, model="openwakeword", reason="no_prediction")

        model_name, confidence = max(prediction.items(), key=lambda item: item[1])
        return WakeDetectionResult(
            detected=confidence >= self._threshold,
            confidence=float(confidence),
            model=str(model_name),
            reason=None if confidence >= self._threshold else "below_threshold",
        )

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


def build_wake_detector(settings: "Settings") -> WakeDetector:
    if settings.voice_wake_provider == "deterministic":
        return DeterministicWakeDetector()

    return OpenWakeWordWakeDetector(
        threshold=settings.voice_wake_threshold,
        wakeword_models=_split_csv(settings.voice_wake_models),
        auto_download_models=settings.voice_wake_auto_download_models,
        enable_speex_noise_suppression=settings.voice_wake_enable_speex_noise_suppression,
        vad_threshold=settings.voice_wake_vad_threshold,
    )


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None
