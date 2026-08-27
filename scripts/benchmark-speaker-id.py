#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import wave

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from hexevoice.speaker_id.benchmark import run_speaker_id_benchmark
from hexevoice.speaker_id.adapters import available_provider_ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark HexeVoice Speaker ID provider adapters on local WAV clips.")
    parser.add_argument("clips", nargs="*", type=Path, help="16-bit PCM WAV clips to benchmark.")
    parser.add_argument(
        "--providers",
        default="deterministic_signal,speechbrain_ecapa_tdnn,wespeaker,pyannote_audio,nvidia_nemo_speaker",
        help="Comma-separated provider IDs. Use 'all' for every catalog provider.",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Embedding runs per clip.")
    parser.add_argument("--threshold", type=float, default=0.75, help="Verification score threshold.")
    parser.add_argument(
        "--device-label",
        default="default",
        help="Label this benchmark run, for example 'cpu' or 'cuda', after configuring provider dependencies.",
    )
    parser.add_argument("--json-output", type=Path, help="Optional path for machine-readable benchmark output.")
    parser.add_argument(
        "--generate-fixtures",
        action="store_true",
        help="Generate two small WAV fixtures when no clips are provided.",
    )
    parser.add_argument("--fixture-dir", type=Path, default=Path("runtime/speaker_id/fixtures"))
    return parser.parse_args()


def provider_ids_from_arg(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return available_provider_ids()
    return [item.strip() for item in value.split(",") if item.strip()]


def generate_fixture(path: Path, *, frequency_hz: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    frame_count = sample_rate
    amplitude = 0.18
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            sample = int(math.sin(2 * math.pi * frequency_hz * (index / sample_rate)) * amplitude * 32767)
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))
    return path


def ensure_clips(args: argparse.Namespace) -> list[Path]:
    if args.clips:
        return args.clips
    if not args.generate_fixtures:
        raise SystemExit("No clips supplied. Pass WAV paths or use --generate-fixtures.")
    return [
        generate_fixture(args.fixture_dir / "speaker-fixture-a.wav", frequency_hz=220),
        generate_fixture(args.fixture_dir / "speaker-fixture-b.wav", frequency_hz=440),
    ]


def print_summary(result: dict[str, object]) -> None:
    print(f"Speaker ID benchmark: {result['clip_count']} clip(s)")
    for provider in result["providers"]:
        assert isinstance(provider, dict)
        status = provider.get("status") if isinstance(provider.get("status"), dict) else {}
        metadata = provider.get("metadata") if isinstance(provider.get("metadata"), dict) else {}
        print()
        print(f"{provider['provider_id']}:")
        print(f"  available: {status.get('available')}  healthy: {status.get('healthy')}  model: {metadata.get('model_id')}")
        print(
            "  dimensions: "
            f"{metadata.get('embedding_dimensions')}  sample_rate: {metadata.get('sample_rate_hz')}  "
            f"cpu: {metadata.get('cpu_supported')}  cuda: {metadata.get('cuda_supported')}"
        )
        print(
            "  memory_rss_kb: "
            f"before={provider.get('memory_rss_kb_before')} after={provider.get('memory_rss_kb_after')} "
            f"delta={provider.get('memory_rss_kb_delta')}"
        )
        if provider.get("error"):
            print(f"  error: {provider['error']}")
            continue
        for clip in provider.get("clips", []):
            assert isinstance(clip, dict)
            print(
                "  clip: "
                f"{Path(str(clip['path'])).name}  mean_ms: {clip.get('duration_ms_mean')}  "
                f"audio_ms: {clip.get('audio_duration_ms')}  error: {clip.get('error')}"
            )
        for score in provider.get("scores", []):
            assert isinstance(score, dict)
            left = Path(str(score["left_path"])).name
            right = Path(str(score["right_path"])).name
            print(f"  score: {left} vs {right} = {score['score']} accepted={score['accepted']}")


def main() -> int:
    args = parse_args()
    clips = ensure_clips(args)
    result = run_speaker_id_benchmark(
        clips=clips,
        provider_ids=provider_ids_from_arg(args.providers),
        repeat=args.repeat,
        threshold=args.threshold,
        device_label=args.device_label,
    )
    print_summary(result)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print()
        print(f"Wrote {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
