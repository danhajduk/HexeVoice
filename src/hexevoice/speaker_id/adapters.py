from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import hashlib
import importlib
import importlib.util
import math
from pathlib import Path
import struct
import time
from typing import Protocol
import wave


class SpeakerIdProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeakerAudio:
    samples: tuple[float, ...]
    sample_rate_hz: int
    channels: int = 1
    source_path: str | None = None

    @property
    def duration_ms(self) -> int:
        if self.sample_rate_hz <= 0:
            return 0
        return int(round((len(self.samples) / self.sample_rate_hz) * 1000))


@dataclass(frozen=True)
class SpeakerThresholds:
    identify_min_confidence: float = 0.72
    identify_min_margin: float = 0.08
    verify_min_score: float = 0.75


@dataclass(frozen=True)
class SpeakerProviderMetadata:
    provider_id: str
    display_name: str
    model_id: str
    engine_family: str
    license: str | None
    download_size_mb: int | None
    memory_mb: int | None
    embedding_dimensions: int
    sample_rate_hz: int
    cpu_supported: bool
    cuda_supported: bool
    enrollment_min_seconds: float
    quality_constraints: tuple[str, ...]
    optional_dependency: str | None = None
    install_hint: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SpeakerEmbedding:
    provider_id: str
    model_id: str
    values: tuple[float, ...]
    duration_ms: float
    sample_rate_hz: int
    audio_duration_ms: int
    metadata: dict[str, object]

    @property
    def dimensions(self) -> int:
        return len(self.values)


@dataclass(frozen=True)
class SpeakerScore:
    provider_id: str
    model_id: str
    score: float
    threshold: float
    accepted: bool
    score_margin: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SpeakerIdAdapter(Protocol):
    metadata: SpeakerProviderMetadata

    def extract_embedding(self, audio: SpeakerAudio | Path | bytes | str) -> SpeakerEmbedding:
        ...

    def score_embeddings(
        self,
        reference: SpeakerEmbedding,
        candidate: SpeakerEmbedding,
        *,
        threshold: float | None = None,
    ) -> SpeakerScore:
        ...

    def status(self) -> dict[str, object]:
        ...


def load_wav_audio(source: Path | bytes | str) -> SpeakerAudio:
    if isinstance(source, bytes):
        from io import BytesIO

        wav_source = BytesIO(source)
        source_path = None
    else:
        path = Path(source)
        wav_source = str(path)
        source_path = path.as_posix()

    with wave.open(wav_source, "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate_hz = wav.getframerate()
        frame_count = wav.getnframes()
        raw = wav.readframes(frame_count)

    if sample_width != 2:
        raise ValueError(f"Unsupported WAV sample width {sample_width}; expected 16-bit PCM")
    if channels <= 0:
        raise ValueError("WAV must contain at least one channel")

    unpacked = struct.unpack(f"<{len(raw) // 2}h", raw)
    if channels == 1:
        samples = tuple(max(-1.0, min(1.0, value / 32768.0)) for value in unpacked)
    else:
        mono = []
        for offset in range(0, len(unpacked), channels):
            frame = unpacked[offset : offset + channels]
            mono.append(sum(frame) / (len(frame) * 32768.0))
        samples = tuple(max(-1.0, min(1.0, value)) for value in mono)

    return SpeakerAudio(samples=samples, sample_rate_hz=sample_rate_hz, channels=1, source_path=source_path)


def normalize_speaker_audio(audio: SpeakerAudio | Path | bytes | str) -> SpeakerAudio:
    if isinstance(audio, SpeakerAudio):
        return audio
    return load_wav_audio(audio)


class DeterministicSignalSpeakerIdAdapter:
    metadata = SpeakerProviderMetadata(
        provider_id="deterministic_signal",
        display_name="Deterministic signal fingerprint",
        model_id="deterministic-signal-v1",
        engine_family="test_stub",
        license="HexeVoice internal test stub",
        download_size_mb=0,
        memory_mb=1,
        embedding_dimensions=32,
        sample_rate_hz=16000,
        cpu_supported=True,
        cuda_supported=False,
        enrollment_min_seconds=0.2,
        quality_constraints=("16-bit PCM WAV recommended", "non-empty mono audio"),
    )

    def extract_embedding(self, audio: SpeakerAudio | Path | bytes | str) -> SpeakerEmbedding:
        started_at = time.perf_counter()
        speaker_audio = normalize_speaker_audio(audio)
        if not speaker_audio.samples:
            raise ValueError("Cannot extract Speaker ID embedding from empty audio")

        values = _deterministic_embedding_values(speaker_audio)
        return SpeakerEmbedding(
            provider_id=self.metadata.provider_id,
            model_id=self.metadata.model_id,
            values=values,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            sample_rate_hz=speaker_audio.sample_rate_hz,
            audio_duration_ms=speaker_audio.duration_ms,
            metadata={
                "algorithm": "deterministic_signal_fingerprint",
                "source_path": speaker_audio.source_path,
                "rms": round(_rms(speaker_audio.samples), 6),
                "zero_crossing_rate": round(_zero_crossing_rate(speaker_audio.samples), 6),
            },
        )

    def score_embeddings(
        self,
        reference: SpeakerEmbedding,
        candidate: SpeakerEmbedding,
        *,
        threshold: float | None = None,
    ) -> SpeakerScore:
        return score_embedding_pair(reference, candidate, threshold=threshold or SpeakerThresholds().verify_min_score)

    def status(self) -> dict[str, object]:
        return {
            "provider_id": self.metadata.provider_id,
            "healthy": True,
            "configured": True,
            "available": True,
            "loaded": True,
            "model_id": self.metadata.model_id,
            "metadata": self.metadata.to_dict(),
        }


class OptionalDependencySpeakerIdAdapter:
    def __init__(self, metadata: SpeakerProviderMetadata) -> None:
        self.metadata = metadata

    def extract_embedding(self, audio: SpeakerAudio | Path | bytes | str) -> SpeakerEmbedding:
        _audio = normalize_speaker_audio(audio)
        dependency = self.metadata.optional_dependency or self.metadata.provider_id
        if not dependency_available(dependency):
            raise SpeakerIdProviderUnavailable(
                f"{self.metadata.display_name} is not installed. {self.metadata.install_hint or 'Install the provider package to enable this adapter.'}"
            )
        raise SpeakerIdProviderUnavailable(
            f"{self.metadata.display_name} adapter boundary is defined, but the runtime implementation has not been enabled yet."
        )

    def score_embeddings(
        self,
        reference: SpeakerEmbedding,
        candidate: SpeakerEmbedding,
        *,
        threshold: float | None = None,
    ) -> SpeakerScore:
        return score_embedding_pair(reference, candidate, threshold=threshold or SpeakerThresholds().verify_min_score)

    def status(self) -> dict[str, object]:
        dependency = self.metadata.optional_dependency or self.metadata.provider_id
        available = dependency_available(dependency)
        return {
            "provider_id": self.metadata.provider_id,
            "healthy": False,
            "configured": available,
            "available": available,
            "loaded": False,
            "model_id": self.metadata.model_id,
            "reason": "implementation_pending" if available else "missing_optional_dependency",
            "optional_dependency": dependency,
            "install_hint": self.metadata.install_hint,
            "metadata": self.metadata.to_dict(),
        }


class SpeechBrainEcapaTdnnSpeakerIdAdapter:
    def __init__(
        self,
        metadata: SpeakerProviderMetadata,
        *,
        cache_dir: Path | None = None,
        device: str = "cpu",
    ) -> None:
        self.metadata = metadata
        self._cache_dir = cache_dir
        self._device = device.strip() or "cpu"
        self._classifier: object | None = None
        self._load_error: str | None = None
        self._runtime_error: str | None = None

    def extract_embedding(self, audio: SpeakerAudio | Path | bytes | str) -> SpeakerEmbedding:
        started_at = time.perf_counter()
        speaker_audio = normalize_speaker_audio(audio)
        if not speaker_audio.samples:
            raise ValueError("Cannot extract Speaker ID embedding from empty audio")
        if speaker_audio.sample_rate_hz != self.metadata.sample_rate_hz:
            raise ValueError(
                f"SpeechBrain ECAPA-TDNN expects {self.metadata.sample_rate_hz} Hz mono WAV audio; "
                f"got {speaker_audio.sample_rate_hz} Hz"
            )

        classifier = self._load_classifier()
        try:
            values = _speechbrain_embedding_values(classifier, speaker_audio, device=self._device)
            self._runtime_error = None
        except Exception as exc:
            self._runtime_error = str(exc)
            raise SpeakerIdProviderUnavailable(
                f"{self.metadata.display_name} embedding extraction failed: {exc}"
            ) from exc
        return SpeakerEmbedding(
            provider_id=self.metadata.provider_id,
            model_id=self.metadata.model_id,
            values=values,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            sample_rate_hz=speaker_audio.sample_rate_hz,
            audio_duration_ms=speaker_audio.duration_ms,
            metadata={
                "engine": "speechbrain_ecapa_tdnn",
                "source_path": speaker_audio.source_path,
                "device": self._device,
                "cache_dir": self._cache_dir.as_posix() if self._cache_dir is not None else None,
            },
        )

    def score_embeddings(
        self,
        reference: SpeakerEmbedding,
        candidate: SpeakerEmbedding,
        *,
        threshold: float | None = None,
    ) -> SpeakerScore:
        return score_embedding_pair(reference, candidate, threshold=threshold or SpeakerThresholds().verify_min_score)

    def status(self) -> dict[str, object]:
        dependencies = _speechbrain_dependency_status()
        dependencies_available = all(dependencies.values())
        reason = None
        if not dependencies_available:
            reason = "missing_optional_dependency"
        elif self._load_error:
            reason = "model_load_failed"
        elif self._runtime_error:
            reason = "implementation_error"
        elif self._classifier is None:
            reason = "model_not_loaded"
        return {
            "provider_id": self.metadata.provider_id,
            "healthy": dependencies_available
            and self._classifier is not None
            and self._load_error is None
            and self._runtime_error is None,
            "configured": dependencies_available,
            "available": dependencies_available and self._load_error is None,
            "loaded": self._classifier is not None,
            "model_id": self.metadata.model_id,
            "reason": reason,
            "optional_dependency": self.metadata.optional_dependency,
            "install_hint": self.metadata.install_hint,
            "device": self._device,
            "cache_dir": self._cache_dir.as_posix() if self._cache_dir is not None else None,
            "dependencies": dependencies,
            "metadata": self.metadata.to_dict(),
        }

    def _load_classifier(self) -> object:
        dependencies = _speechbrain_dependency_status()
        if not all(dependencies.values()):
            missing = ", ".join(name for name, available in dependencies.items() if not available)
            raise SpeakerIdProviderUnavailable(
                f"{self.metadata.display_name} is not installed. Missing: {missing}. {self.metadata.install_hint}"
            )
        if self._classifier is not None:
            return self._classifier
        try:
            classifier_cls = _speechbrain_classifier_class()
            kwargs: dict[str, object] = {
                "source": self.metadata.model_id,
                "run_opts": {"device": self._device},
            }
            if self._cache_dir is not None:
                savedir = self._cache_dir / _safe_model_cache_name(self.metadata.model_id)
                savedir.mkdir(parents=True, exist_ok=True)
                kwargs["savedir"] = savedir.as_posix()
            self._classifier = classifier_cls.from_hparams(**kwargs)
            self._load_error = None
            return self._classifier
        except Exception as exc:
            self._load_error = str(exc)
            raise SpeakerIdProviderUnavailable(
                f"{self.metadata.display_name} model is unavailable or failed to load: {exc}"
            ) from exc


PROVIDER_CATALOG: dict[str, SpeakerProviderMetadata] = {
    "deterministic_signal": DeterministicSignalSpeakerIdAdapter.metadata,
    "speechbrain_ecapa_tdnn": SpeakerProviderMetadata(
        provider_id="speechbrain_ecapa_tdnn",
        display_name="SpeechBrain ECAPA-TDNN",
        model_id="speechbrain/spkrec-ecapa-voxceleb",
        engine_family="speechbrain",
        license="Apache-2.0 for SpeechBrain code; model license must be verified before deployment",
        download_size_mb=85,
        memory_mb=600,
        embedding_dimensions=192,
        sample_rate_hz=16000,
        cpu_supported=True,
        cuda_supported=True,
        enrollment_min_seconds=3.0,
        quality_constraints=("16 kHz mono speech", "low background noise", "multiple enrollment samples recommended"),
        optional_dependency="speechbrain",
        install_hint="Install speechbrain and its torch-compatible dependencies.",
    ),
    "wespeaker": SpeakerProviderMetadata(
        provider_id="wespeaker",
        display_name="WeSpeaker",
        model_id="wespeaker/voxceleb-resnet34",
        engine_family="wespeaker",
        license="Apache-2.0 for WeSpeaker code; selected model license must be verified",
        download_size_mb=100,
        memory_mb=700,
        embedding_dimensions=256,
        sample_rate_hz=16000,
        cpu_supported=True,
        cuda_supported=True,
        enrollment_min_seconds=3.0,
        quality_constraints=("16 kHz mono speech", "speaker-dominant clip", "avoid music or TV bleed"),
        optional_dependency="wespeaker",
        install_hint="Install wespeaker and configure the selected pretrained model.",
    ),
    "pyannote_audio": SpeakerProviderMetadata(
        provider_id="pyannote_audio",
        display_name="pyannote.audio",
        model_id="pyannote/embedding",
        engine_family="pyannote",
        license="Model access and license vary by selected pyannote model",
        download_size_mb=120,
        memory_mb=900,
        embedding_dimensions=512,
        sample_rate_hz=16000,
        cpu_supported=True,
        cuda_supported=True,
        enrollment_min_seconds=3.0,
        quality_constraints=("16 kHz mono speech", "diarization path may need longer context", "operator model token may be required"),
        optional_dependency="pyannote.audio",
        install_hint="Install pyannote.audio and configure model access if required.",
    ),
    "nvidia_nemo_speaker": SpeakerProviderMetadata(
        provider_id="nvidia_nemo_speaker",
        display_name="NVIDIA NeMo speaker model",
        model_id="nvidia/speakerverification_en_titanet_large",
        engine_family="nemo",
        license="NVIDIA NeMo and model licenses vary by selected checkpoint",
        download_size_mb=90,
        memory_mb=900,
        embedding_dimensions=192,
        sample_rate_hz=16000,
        cpu_supported=True,
        cuda_supported=True,
        enrollment_min_seconds=3.0,
        quality_constraints=("16 kHz mono speech", "CUDA recommended for larger models", "selected checkpoint must support verification"),
        optional_dependency="nemo.collections.asr",
        install_hint="Install nvidia-nemo with ASR extras and a torch-compatible runtime.",
    ),
}


def available_provider_ids() -> list[str]:
    return list(PROVIDER_CATALOG)


def create_speaker_id_adapter(
    provider_id: str = "deterministic_signal",
    *,
    cache_dir: Path | None = None,
    device: str = "cpu",
) -> SpeakerIdAdapter:
    normalized = provider_id.strip().lower()
    if normalized == "deterministic":
        normalized = "deterministic_signal"
    metadata = PROVIDER_CATALOG.get(normalized)
    if metadata is None:
        supported = ", ".join(available_provider_ids())
        raise ValueError(f"Unsupported Speaker ID provider '{provider_id}'. Supported providers: {supported}")
    if normalized == "deterministic_signal":
        return DeterministicSignalSpeakerIdAdapter()
    if normalized == "speechbrain_ecapa_tdnn":
        return SpeechBrainEcapaTdnnSpeakerIdAdapter(metadata, cache_dir=cache_dir, device=device)
    return OptionalDependencySpeakerIdAdapter(metadata)


def dependency_available(import_name: str) -> bool:
    try:
        importlib.invalidate_caches()
        return importlib.util.find_spec(import_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _speechbrain_dependency_status() -> dict[str, bool]:
    return {
        "speechbrain": dependency_available("speechbrain"),
        "torch": dependency_available("torch"),
    }


def _speechbrain_classifier_class() -> object:
    try:
        module = importlib.import_module("speechbrain.inference.speaker")
    except ModuleNotFoundError:
        module = importlib.import_module("speechbrain.pretrained")
    return module.EncoderClassifier


def _speechbrain_embedding_values(classifier: object, audio: SpeakerAudio, *, device: str) -> tuple[float, ...]:
    torch = importlib.import_module("torch")
    waveform = torch.tensor(audio.samples, dtype=torch.float32, device=device).unsqueeze(0)
    with torch.no_grad():
        embedding = classifier.encode_batch(waveform)
    values = tuple(float(value) for value in embedding.detach().cpu().flatten().tolist())
    if not values:
        raise SpeakerIdProviderUnavailable("SpeechBrain ECAPA-TDNN returned an empty embedding")
    return values


def _safe_model_cache_name(model_id: str) -> str:
    return model_id.replace("/", "--").replace("\\", "--")


def score_embedding_pair(
    reference: SpeakerEmbedding,
    candidate: SpeakerEmbedding,
    *,
    threshold: float,
) -> SpeakerScore:
    if reference.provider_id != candidate.provider_id:
        raise ValueError("Cannot compare Speaker ID embeddings from different providers")
    score = cosine_similarity(reference.values, candidate.values)
    rounded_score = round(score, 6)
    rounded_threshold = round(threshold, 6)
    return SpeakerScore(
        provider_id=reference.provider_id,
        model_id=reference.model_id,
        score=rounded_score,
        threshold=rounded_threshold,
        accepted=rounded_score >= rounded_threshold,
        score_margin=round(rounded_score - rounded_threshold, 6),
    )


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("Cannot compare Speaker ID embeddings with different dimensions")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _deterministic_embedding_values(audio: SpeakerAudio) -> tuple[float, ...]:
    quantized = bytearray()
    for sample in audio.samples:
        value = int(max(-1.0, min(1.0, sample)) * 32767)
        quantized.extend(value.to_bytes(2, byteorder="little", signed=True))
    digest = hashlib.blake2s(bytes(quantized), digest_size=24).digest()
    digest_values = tuple((byte - 127.5) / 127.5 for byte in digest)
    signal_values = (
        _rms(audio.samples),
        _mean_abs(audio.samples),
        _zero_crossing_rate(audio.samples),
        _peak(audio.samples),
        min(1.0, audio.duration_ms / 10000.0),
        min(1.0, audio.sample_rate_hz / 48000.0),
        _autocorrelation_lag(audio.samples, lag=16),
        _autocorrelation_lag(audio.samples, lag=64),
    )
    values = signal_values + digest_values
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        return tuple(0.0 for _ in values)
    return tuple(round(value / norm, 8) for value in values)


def _rms(samples: tuple[float, ...]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def _mean_abs(samples: tuple[float, ...]) -> float:
    if not samples:
        return 0.0
    return sum(abs(sample) for sample in samples) / len(samples)


def _peak(samples: tuple[float, ...]) -> float:
    if not samples:
        return 0.0
    return max(abs(sample) for sample in samples)


def _zero_crossing_rate(samples: tuple[float, ...]) -> float:
    if len(samples) < 2:
        return 0.0
    crossings = 0
    previous = samples[0]
    for sample in samples[1:]:
        if (previous < 0 <= sample) or (previous >= 0 > sample):
            crossings += 1
        previous = sample
    return crossings / (len(samples) - 1)


def _autocorrelation_lag(samples: tuple[float, ...], *, lag: int) -> float:
    if len(samples) <= lag:
        return 0.0
    numerator = sum(samples[index] * samples[index - lag] for index in range(lag, len(samples)))
    denominator = sum(sample * sample for sample in samples)
    if denominator <= 0:
        return 0.0
    return max(-1.0, min(1.0, numerator / denominator))
