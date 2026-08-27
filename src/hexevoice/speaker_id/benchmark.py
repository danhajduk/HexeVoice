from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import os
import platform
import resource
import statistics
import time
from pathlib import Path
from typing import Any

from hexevoice.speaker_id.adapters import SpeakerEmbedding
from hexevoice.speaker_id.adapters import SpeakerIdProviderUnavailable
from hexevoice.speaker_id.adapters import SpeakerScore
from hexevoice.speaker_id.adapters import available_provider_ids
from hexevoice.speaker_id.adapters import create_speaker_id_adapter
from hexevoice.speaker_id.adapters import score_embedding_pair


@dataclass(frozen=True)
class SpeakerIdClipBenchmark:
    path: str
    embedding_dimensions: int | None
    audio_duration_ms: int | None
    duration_ms_runs: list[float]
    duration_ms_mean: float | None
    duration_ms_min: float | None
    duration_ms_max: float | None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SpeakerIdProviderBenchmark:
    provider_id: str
    status: dict[str, object]
    metadata: dict[str, object]
    clips: list[SpeakerIdClipBenchmark]
    scores: list[dict[str, object]]
    memory_rss_kb_before: int | None
    memory_rss_kb_after: int | None
    memory_rss_kb_delta: int | None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "status": self.status,
            "metadata": self.metadata,
            "clips": [clip.to_dict() for clip in self.clips],
            "scores": self.scores,
            "memory_rss_kb_before": self.memory_rss_kb_before,
            "memory_rss_kb_after": self.memory_rss_kb_after,
            "memory_rss_kb_delta": self.memory_rss_kb_delta,
            "error": self.error,
        }


def run_speaker_id_benchmark(
    *,
    clips: list[Path],
    provider_ids: list[str] | None = None,
    repeat: int = 1,
    threshold: float = 0.75,
    device_label: str = "default",
) -> dict[str, object]:
    selected_provider_ids = provider_ids or available_provider_ids()
    provider_results = []
    for provider_id in selected_provider_ids:
        provider_results.append(
            run_provider_benchmark(
                provider_id=provider_id,
                clips=clips,
                repeat=max(1, repeat),
                threshold=threshold,
            )
        )
    return {
        "schema_version": 1,
        "device_label": device_label,
        "runtime": _runtime_metadata(),
        "clip_count": len(clips),
        "providers": [result.to_dict() for result in provider_results],
    }


def run_provider_benchmark(
    *,
    provider_id: str,
    clips: list[Path],
    repeat: int,
    threshold: float,
) -> SpeakerIdProviderBenchmark:
    adapter = create_speaker_id_adapter(provider_id)
    status = adapter.status()
    metadata = adapter.metadata.to_dict()
    embeddings: dict[str, SpeakerEmbedding] = {}
    clip_results = []
    memory_rss_kb_before = _max_rss_kb()

    for clip in clips:
        duration_runs = []
        embedding = None
        error = None
        for _index in range(repeat):
            started_at = time.perf_counter()
            try:
                embedding = adapter.extract_embedding(clip)
            except (SpeakerIdProviderUnavailable, ValueError) as exc:
                error = str(exc)
                break
            duration_runs.append(round((time.perf_counter() - started_at) * 1000, 2))
        if embedding is not None:
            embeddings[clip.as_posix()] = embedding
        clip_results.append(_clip_result(path=clip, embedding=embedding, duration_runs=duration_runs, error=error))

    scores = _score_embeddings(embeddings, threshold=threshold)
    provider_error = None
    memory_rss_kb_after = _max_rss_kb()
    if not embeddings and clip_results:
        provider_error = clip_results[0].error
    return SpeakerIdProviderBenchmark(
        provider_id=adapter.metadata.provider_id,
        status=status,
        metadata=metadata,
        clips=clip_results,
        scores=scores,
        memory_rss_kb_before=memory_rss_kb_before,
        memory_rss_kb_after=memory_rss_kb_after,
        memory_rss_kb_delta=_memory_delta(memory_rss_kb_before, memory_rss_kb_after),
        error=provider_error,
    )


def _clip_result(
    *,
    path: Path,
    embedding: SpeakerEmbedding | None,
    duration_runs: list[float],
    error: str | None,
) -> SpeakerIdClipBenchmark:
    return SpeakerIdClipBenchmark(
        path=path.as_posix(),
        embedding_dimensions=embedding.dimensions if embedding else None,
        audio_duration_ms=embedding.audio_duration_ms if embedding else None,
        duration_ms_runs=duration_runs,
        duration_ms_mean=round(statistics.fmean(duration_runs), 2) if duration_runs else None,
        duration_ms_min=round(min(duration_runs), 2) if duration_runs else None,
        duration_ms_max=round(max(duration_runs), 2) if duration_runs else None,
        error=error,
    )


def _score_embeddings(embeddings: dict[str, SpeakerEmbedding], *, threshold: float) -> list[dict[str, object]]:
    paths = list(embeddings)
    scores: list[dict[str, object]] = []
    for left_index, left_path in enumerate(paths):
        for right_path in paths[left_index:]:
            score = _score_pair(embeddings[left_path], embeddings[right_path], threshold=threshold)
            scores.append(
                {
                    "left_path": left_path,
                    "right_path": right_path,
                    **score.to_dict(),
                }
            )
    return scores


def _score_pair(left: SpeakerEmbedding, right: SpeakerEmbedding, *, threshold: float) -> SpeakerScore:
    return score_embedding_pair(left, right, threshold=threshold)


def _max_rss_kb() -> int | None:
    try:
        rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    if platform.system().lower() == "darwin":
        return round(rss / 1024)
    return rss


def _memory_delta(before: int | None, after: int | None) -> int | None:
    if before is None or after is None:
        return None
    return max(0, after - before)


def _runtime_metadata() -> dict[str, object]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "process_id": os.getpid(),
    }
