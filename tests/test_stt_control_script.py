from __future__ import annotations

import http.server
import json
from pathlib import Path
import socketserver
import subprocess
import sys
import threading


class _SttHandler(http.server.BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self.requests.append(("GET", self.path))
        if self.path == "/health":
            self._send_json({"provider": "external_faster_whisper", "loaded": False})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        self.requests.append(("POST", self.path))
        if self.path == "/preload":
            self._send_json({"provider": "external_faster_whisper", "loaded": True})
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return


class _ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def test_faster_whisper_stt_control_ready_installs_restarts_waits_and_preloads(tmp_path):
    _SttHandler.requests = []
    server = _ThreadedServer(("127.0.0.1", 0), _SttHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        repo_root = Path(__file__).resolve().parents[1]
        docker_log = tmp_path / "docker.log"
        fake_docker = tmp_path / "docker"
        fake_docker.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        fake_docker.chmod(0o755)

        service_url = f"http://127.0.0.1:{server.server_address[1]}"
        result = subprocess.run(
            ["bash", "scripts/faster-whisper-stt-control.sh", "ready"],
            cwd=repo_root,
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHON_BIN": sys.executable,
                "DOCKER_BIN": str(fake_docker),
                "DOCKER_LOG": str(docker_log),
                "STT_ENV_FILE": str(tmp_path / "missing.env"),
                "RUNTIME_DIR": str(tmp_path / "runtime"),
                "STT_CUDA_MODE": "cpu",
                "HEXEVOICE_SOCKET_DIR": str(tmp_path / "sockets"),
                "HEXEVOICE_STT_CACHE_DIR": str(tmp_path / "stt-cache"),
                "STT_HEALTH_URL": service_url,
                "STT_HEALTH_TIMEOUT_S": "5",
                "STT_HEALTH_INTERVAL_S": "0",
            },
            text=True,
            capture_output=True,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert "loaded" in result.stdout
    assert docker_log.read_text(encoding="utf-8").splitlines() == [
        f"compose -f {repo_root / 'compose.faster-whisper-stt.yaml'} up -d --build"
    ]
    assert ("GET", "/health") in _SttHandler.requests
    assert ("POST", "/preload") in _SttHandler.requests


def test_faster_whisper_stt_control_auto_cuda_falls_back_to_cpu_when_smoke_fails(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do printf "%q " "$arg" >> "$DOCKER_LOG"; done\n'
        'printf "STT_IMAGE=%q DEVICE=%q COMPUTE=%q\\n" "$STT_IMAGE" "$VOICE_STT_FASTER_WHISPER_DEVICE" "$VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE" >> "$DOCKER_LOG"\n'
        'if [[ "$1" == "run" ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["bash", "scripts/faster-whisper-stt-control.sh", "build"],
        cwd=repo_root,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": sys.executable,
            "DOCKER_BIN": str(fake_docker),
            "DOCKER_LOG": str(docker_log),
            "STT_ENV_FILE": str(tmp_path / "missing.env"),
            "RUNTIME_DIR": str(tmp_path / "runtime"),
            "HEXEVOICE_SOCKET_DIR": str(tmp_path / "sockets"),
            "HEXEVOICE_STT_CACHE_DIR": str(tmp_path / "stt-cache"),
            "STT_CUDA_MODE": "auto",
            "STT_CUDA_CHECK_TIMEOUT_S": "2",
        },
        text=True,
        capture_output=True,
        check=True,
    )

    lines = docker_log.read_text(encoding="utf-8").splitlines()
    assert "Docker GPU passthrough unavailable; using CPU runtime" in result.stdout
    assert lines == [
        "run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi STT_IMAGE='' DEVICE='' COMPUTE=''",
        f"compose -f {repo_root / 'compose.faster-whisper-stt.yaml'} build STT_IMAGE=hexevoice/faster-whisper-stt:local DEVICE=cpu COMPUTE=int8",
    ]


def test_faster_whisper_stt_control_forced_cuda_uses_cuda_compose_profile(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do printf "%q " "$arg" >> "$DOCKER_LOG"; done\n'
        'printf "STT_IMAGE=%q DEVICE=%q COMPUTE=%q\\n" "$STT_IMAGE" "$VOICE_STT_FASTER_WHISPER_DEVICE" "$VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE" >> "$DOCKER_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    subprocess.run(
        ["bash", "scripts/faster-whisper-stt-control.sh", "build"],
        cwd=repo_root,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": sys.executable,
            "DOCKER_BIN": str(fake_docker),
            "DOCKER_LOG": str(docker_log),
            "STT_ENV_FILE": str(tmp_path / "missing.env"),
            "RUNTIME_DIR": str(tmp_path / "runtime"),
            "HEXEVOICE_SOCKET_DIR": str(tmp_path / "sockets"),
            "HEXEVOICE_STT_CACHE_DIR": str(tmp_path / "stt-cache"),
            "STT_CUDA_MODE": "cuda",
            "STT_CUDA_CHECK_TIMEOUT_S": "2",
        },
        text=True,
        capture_output=True,
        check=True,
    )

    lines = docker_log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi STT_IMAGE='' DEVICE='' COMPUTE=''"
    assert lines[1] == (
        f"compose -f {repo_root / 'compose.faster-whisper-stt.yaml'} -f "
        f"{repo_root / 'compose.faster-whisper-stt.cuda.yaml'} build "
        "STT_IMAGE=hexevoice/faster-whisper-stt:cuda DEVICE=cuda COMPUTE=float16"
    )
    assert lines[2].startswith(
        "run --rm --gpus all hexevoice/faster-whisper-stt:cuda python -c"
    )
    assert lines[2].endswith(
        "STT_IMAGE=hexevoice/faster-whisper-stt:cuda DEVICE=cuda COMPUTE=float16"
    )
    assert len(lines) == 3


def test_faster_whisper_stt_control_uses_saved_provider_model_before_restart(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "onboarding_state.json").write_text(
        json.dumps(
            {
                "provider_setup": {
                    "provider_configs": {
                        "external_faster_whisper": {
                            "model": "small.en",
                            "warm_model": True,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do printf "%q " "$arg" >> "$DOCKER_LOG"; done\n'
        'printf "MODEL=%q DEVICE=%q COMPUTE=%q PRELOAD=%q\\n" "$VOICE_STT_FASTER_WHISPER_MODEL" "$VOICE_STT_FASTER_WHISPER_DEVICE" "$VOICE_STT_FASTER_WHISPER_COMPUTE_TYPE" "$VOICE_STT_PRELOAD" >> "$DOCKER_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    subprocess.run(
        ["bash", "scripts/faster-whisper-stt-control.sh", "restart"],
        cwd=repo_root,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": sys.executable,
            "DOCKER_BIN": str(fake_docker),
            "DOCKER_LOG": str(docker_log),
            "STT_ENV_FILE": str(tmp_path / "missing.env"),
            "RUNTIME_DIR": str(runtime_dir),
            "HEXEVOICE_SOCKET_DIR": str(tmp_path / "sockets"),
            "HEXEVOICE_STT_CACHE_DIR": str(tmp_path / "stt-cache"),
            "STT_CUDA_MODE": "cpu",
        },
        text=True,
        capture_output=True,
        check=True,
    )

    assert docker_log.read_text(encoding="utf-8").splitlines() == [
        f"compose -f {repo_root / 'compose.faster-whisper-stt.yaml'} up -d --build --force-recreate MODEL=small.en DEVICE=cpu COMPUTE=int8 PRELOAD=true"
    ]


def test_faster_whisper_stt_preflight_reports_cpu_fallback_without_gpu(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        'if [[ "$1" == "--version" ]]; then echo "Docker version test"; exit 0; fi\n'
        'if [[ "$1" == "compose" ]]; then echo "Docker Compose version test"; exit 0; fi\n'
        'if [[ "$1" == "run" ]]; then echo "gpu unavailable" >&2; exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["bash", "scripts/faster-whisper-stt-control.sh", "cuda-preflight"],
        cwd=repo_root,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": sys.executable,
            "DOCKER_BIN": str(fake_docker),
            "DOCKER_LOG": str(docker_log),
            "STT_ENV_FILE": str(tmp_path / "missing.env"),
            "RUNTIME_DIR": str(tmp_path / "runtime"),
            "HEXEVOICE_STT_CACHE_DIR": str(tmp_path / "stt-cache"),
            "STT_CUDA_MODE": "auto",
            "STT_CUDA_CHECK_TIMEOUT_S": "2",
        },
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["cuda_available"] is False
    assert payload["selected_profile"] == "cpu"
    assert payload["cpu_fallback"]["compute_type"] == "int8"
    assert payload["checks"]["docker"]["ok"] is True
    assert payload["checks"]["docker_gpu_smoke"]["ok"] is False
    assert "CPU fallback" in " ".join(payload["warnings"])
