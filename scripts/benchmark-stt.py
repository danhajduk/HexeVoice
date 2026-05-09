#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soxr


@dataclass
class ProviderResult:
    provider: str
    duration_ms: float
    text: str


@dataclass
class ClipResult:
    path: str
    audio_duration_ms: int
    faster_whisper: ProviderResult
    whisper: ProviderResult
    exact_match: bool
    normalized_match: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark OpenAI Whisper against faster-whisper on local WAV clips.")
    parser.add_argument(
        "clips",
        nargs="*",
        type=Path,
        help="WAV files to benchmark. Defaults to newest accepted wake recordings.",
    )
    parser.add_argument("--samples", type=int, default=5, help="Number of newest wake clips to use when clips are omitted.")
    parser.add_argument("--recordings-dir", type=Path, default=Path("runtime/wake_recordings"))
    parser.add_argument("--model", default="base.en", help="Whisper model name for both providers.")
    parser.add_argument("--device", default="cpu", help="Device for both providers.")
    parser.add_argument("--faster-compute-type", default="int8", help="faster-whisper compute type.")
    parser.add_argument("--json-output", type=Path, help="Optional path for machine-readable benchmark output.")
    return parser.parse_args()


def newest_wake_clips(recordings_dir: Path, samples: int) -> list[Path]:
    clips = sorted(
        recordings_dir.glob("*accepted_wake.wav"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return clips[:samples]


def load_wav_mono_16k(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frame_count = wav.getnframes()
        raw = wav.readframes(frame_count)

    if sample_width != 2:
        raise ValueError(f"Unsupported WAV sample width {sample_width} in {path}")

    pcm = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
    audio = pcm.astype(np.float32) / 32768.0
    if sample_rate != 16000:
        audio = soxr.resample(audio, sample_rate, 16000).astype(np.float32)
    duration_ms = int(round((frame_count / sample_rate) * 1000))
    return audio, duration_ms


def normalize_transcript(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def time_call(fn) -> tuple[float, Any]:
    started_at = time.perf_counter()
    result = fn()
    return round((time.perf_counter() - started_at) * 1000, 2), result


def transcribe_faster(model, path: Path) -> str:
    segments, _info = model.transcribe(str(path))
    return " ".join(str(getattr(segment, "text", "")).strip() for segment in segments).strip()


def transcribe_whisper(model, audio: np.ndarray) -> str:
    result = model.transcribe(audio, language="en", fp16=False, verbose=None)
    return str(result.get("text") or "").strip()


def print_table(results: list[ClipResult]) -> None:
    headers = ["clip", "audio_ms", "faster_ms", "whisper_ms", "same", "faster_text", "whisper_text"]
    rows = []
    for result in results:
        rows.append(
            [
                Path(result.path).name,
                str(result.audio_duration_ms),
                f"{result.faster_whisper.duration_ms:.2f}",
                f"{result.whisper.duration_ms:.2f}",
                "yes" if result.normalized_match else "no",
                result.faster_whisper.text,
                result.whisper.text,
            ]
        )
    widths = [max(len(row[index]) for row in [headers, *rows]) for index in range(len(headers))]
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def summary(values: list[float]) -> dict[str, float]:
    return {
        "mean_ms": round(statistics.fmean(values), 2),
        "median_ms": round(statistics.median(values), 2),
        "min_ms": round(min(values), 2),
        "max_ms": round(max(values), 2),
    }


def main() -> int:
    args = parse_args()
    clips = args.clips or newest_wake_clips(args.recordings_dir, args.samples)
    if not clips:
        raise SystemExit("No WAV clips found to benchmark.")

    from faster_whisper import WhisperModel
    import torch
    import whisper

    faster_load_ms, faster_model = time_call(
        lambda: WhisperModel(args.model, device=args.device, compute_type=args.faster_compute_type)
    )
    whisper_load_ms, whisper_model = time_call(lambda: whisper.load_model(args.model, device=args.device))

    results: list[ClipResult] = []
    for path in clips:
        audio, audio_duration_ms = load_wav_mono_16k(path)
        faster_ms, faster_text = time_call(lambda path=path: transcribe_faster(faster_model, path))
        whisper_ms, whisper_text = time_call(lambda audio=audio: transcribe_whisper(whisper_model, audio))
        results.append(
            ClipResult(
                path=str(path),
                audio_duration_ms=audio_duration_ms,
                faster_whisper=ProviderResult("faster_whisper", faster_ms, faster_text),
                whisper=ProviderResult("whisper", whisper_ms, whisper_text),
                exact_match=faster_text == whisper_text,
                normalized_match=normalize_transcript(faster_text) == normalize_transcript(whisper_text),
            )
        )

    print(f"model={args.model} device={args.device} torch={torch.__version__} cuda_available={torch.cuda.is_available()}")
    print(f"load_ms faster_whisper={faster_load_ms:.2f} whisper={whisper_load_ms:.2f}")
    print_table(results)
    print(
        "summary "
        + json.dumps(
            {
                "faster_whisper": summary([result.faster_whisper.duration_ms for result in results]),
                "whisper": summary([result.whisper.duration_ms for result in results]),
                "normalized_matches": sum(1 for result in results if result.normalized_match),
                "clip_count": len(results),
            },
            sort_keys=True,
        )
    )

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(
                {
                    "model": args.model,
                    "device": args.device,
                    "torch": torch.__version__,
                    "cuda_available": torch.cuda.is_available(),
                    "load_ms": {"faster_whisper": faster_load_ms, "whisper": whisper_load_ms},
                    "results": [asdict(result) for result in results],
                },
                indent=2,
            )
            + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
