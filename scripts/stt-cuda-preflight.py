#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalized_cuda_mode() -> str:
    if truthy(os.environ.get("STT_FORCE_CPU")) or truthy(os.environ.get("HEXEVOICE_STT_FORCE_CPU")):
        return "cpu"
    if truthy(os.environ.get("STT_FORCE_CUDA")) or truthy(os.environ.get("HEXEVOICE_STT_FORCE_CUDA")):
        return "cuda"
    if truthy(os.environ.get("STT_SKIP_CUDA_DETECTION")) or truthy(os.environ.get("HEXEVOICE_STT_SKIP_CUDA_DETECTION")):
        return "skip"
    mode = os.environ.get("STT_CUDA_MODE", "auto").strip().lower()
    return mode if mode in {"auto", "cpu", "cuda", "skip"} else "auto"


def run_command(command: list[str], timeout_s: int) -> dict[str, Any]:
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout_s, check=False)
    except FileNotFoundError as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": f"timed out after {timeout_s}s",
        }
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def main() -> int:
    docker_bin = os.environ.get("DOCKER_BIN", "docker")
    timeout_s = int(os.environ.get("STT_CUDA_CHECK_TIMEOUT_S", "45"))
    cuda_smoke_image = os.environ.get("STT_CUDA_SMOKE_IMAGE", "nvidia/cuda:12.4.1-base-ubuntu22.04")
    cuda_image = os.environ.get("STT_CUDA_IMAGE", "hexevoice/faster-whisper-stt:cuda")
    mode = normalized_cuda_mode()

    docker_version = run_command([docker_bin, "--version"], timeout_s)
    compose_version = run_command([docker_bin, "compose", "version"], timeout_s)
    host_nvidia_smi = (
        run_command(["nvidia-smi"], timeout_s)
        if shutil.which("nvidia-smi")
        else {"ok": False, "returncode": None, "stdout": "", "stderr": "nvidia-smi not found on host PATH"}
    )

    docker_gpu_smoke = {"ok": False, "returncode": None, "stdout": "", "stderr": "skipped"}
    cuda_image_capability = {"ok": False, "returncode": None, "stdout": "", "stderr": "skipped"}
    if mode not in {"cpu", "skip"} and docker_version["ok"]:
        docker_gpu_smoke = run_command(
            [docker_bin, "run", "--rm", "--gpus", "all", cuda_smoke_image, "nvidia-smi"],
            timeout_s,
        )
        if docker_gpu_smoke["ok"]:
            cuda_image_capability = run_command(
                [
                    docker_bin,
                    "run",
                    "--rm",
                    "--gpus",
                    "all",
                    cuda_image,
                    "python",
                    "-c",
                    (
                        "import json, sys\n"
                        "report={'faster_whisper': False, 'ctranslate2': False, 'cuda_supported': False}\n"
                        "try:\n"
                        " import faster_whisper\n"
                        " report['faster_whisper']=True\n"
                        " import ctranslate2\n"
                        " report['ctranslate2']=True\n"
                        " supported=sorted(ctranslate2.get_supported_compute_types('cuda'))\n"
                        " report['supported_compute_types']=supported\n"
                        " report['cuda_supported']=bool(supported)\n"
                        "except Exception as exc:\n"
                        " report['error']=str(exc)\n"
                        "print(json.dumps(report, sort_keys=True))\n"
                        "sys.exit(0 if report['cuda_supported'] else 1)\n"
                    ),
                ],
                timeout_s,
            )

    cuda_available = bool(docker_gpu_smoke["ok"] and cuda_image_capability["ok"])
    selected_profile = "cuda" if cuda_available and mode != "cpu" else "cpu"
    warnings: list[str] = []
    if mode == "cpu":
        warnings.append("CUDA detection forced to CPU mode.")
    elif mode == "skip":
        warnings.append("CUDA detection skipped; CPU fallback is selected.")
    elif not docker_gpu_smoke["ok"]:
        warnings.append("Docker GPU passthrough is unavailable; CPU fallback is selected.")
    elif not cuda_image_capability["ok"]:
        warnings.append("CUDA passthrough works, but the STT CUDA image did not prove faster-whisper CUDA support.")

    payload = {
        "mode": mode,
        "selected_profile": selected_profile,
        "cuda_available": cuda_available,
        "cpu_fallback": {
            "available": True,
            "device": "cpu",
            "compute_type": os.environ.get("STT_CPU_COMPUTE_TYPE", "int8"),
        },
        "configured_stt": {
            "model": os.environ.get("VOICE_STT_FASTER_WHISPER_MODEL", "base.en"),
            "device": os.environ.get("VOICE_STT_FASTER_WHISPER_DEVICE", "cpu"),
            "compute_type": os.environ.get("VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE", "int8"),
            "preload": os.environ.get("VOICE_STT_PRELOAD", "true"),
            "cache_dir": os.environ.get("HEXEVOICE_STT_CACHE_DIR"),
            "cache_dir_exists": Path(os.environ.get("HEXEVOICE_STT_CACHE_DIR", "")).exists()
            if os.environ.get("HEXEVOICE_STT_CACHE_DIR")
            else False,
        },
        "checks": {
            "docker": docker_version,
            "docker_compose": compose_version,
            "host_nvidia_smi": host_nvidia_smi,
            "docker_gpu_smoke": docker_gpu_smoke,
            "cuda_image_capability": cuda_image_capability,
        },
        "warnings": warnings,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if mode == "cuda" and not cuda_available else 0


if __name__ == "__main__":
    raise SystemExit(main())
