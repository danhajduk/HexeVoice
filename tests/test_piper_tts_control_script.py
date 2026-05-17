from __future__ import annotations

import http.server
import json
from pathlib import Path
import socketserver
import subprocess
import sys
import threading


class _TtsHandler(http.server.BaseHTTPRequestHandler):
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
            self._send_json({"provider": "piper", "model_exists": True})
            return
        self.send_error(404)

    def do_PUT(self) -> None:  # noqa: N802
        self.requests.append(("PUT", self.path))
        if self.path == "/config":
            self._send_json({"provider": "piper", "config_applied": True})
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return


class _ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def _write_voice_fixture(repo_root: Path, voice: str) -> None:
    locale, rest = voice.split("-", 1)
    speaker, quality = rest.rsplit("-", 1)
    language = locale.split("_", 1)[0]
    voice_dir = repo_root / language / locale / speaker / quality
    voice_dir.mkdir(parents=True)
    (voice_dir / f"{voice}.onnx").write_bytes(b"model")
    (voice_dir / f"{voice}.onnx.json").write_text('{"audio":{"sample_rate":22050}}', encoding="utf-8")


def test_piper_tts_control_download_models_uses_configurable_voice_repo(tmp_path):
    source_root = tmp_path / "source"
    model_dir = tmp_path / "models"
    _write_voice_fixture(source_root, "en_US-lessac-medium")

    result = subprocess.run(
        ["bash", "scripts/piper-tts-control.sh", "download-models"],
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": sys.executable,
            "PIPER_TTS_MODEL_DIR": str(model_dir),
            "RUNTIME_DIR": str(tmp_path / "runtime"),
            "PIPER_TTS_ENV_FILE": str(tmp_path / "missing.env"),
            "PIPER_TTS_MODEL_PATH": "/models/en_US-lessac-medium.onnx",
            "PIPER_TTS_DOWNLOAD_VOICES": "en_US-lessac-medium",
            "PIPER_TTS_WARM_VOICES": "",
            "PIPER_TTS_VOICE_REPO_URL": source_root.as_uri(),
        },
        text=True,
        capture_output=True,
        check=True,
    )

    assert "download en_US-lessac-medium.onnx" in result.stdout
    assert (model_dir / "en_US-lessac-medium.onnx").read_bytes() == b"model"
    assert (model_dir / "en_US-lessac-medium.onnx.json").exists()


def test_piper_tts_control_ready_downloads_starts_waits_and_preloads(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    _TtsHandler.requests = []
    server = _ThreadedServer(("127.0.0.1", 0), _TtsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_root = tmp_path / "source"
        model_dir = tmp_path / "models"
        _write_voice_fixture(source_root, "en_US-lessac-medium")
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
            ["bash", "scripts/piper-tts-control.sh", "ready"],
            cwd=repo_root,
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHON_BIN": sys.executable,
                "DOCKER_BIN": str(fake_docker),
                "DOCKER_LOG": str(docker_log),
                "PIPER_TTS_MODEL_DIR": str(model_dir),
                "RUNTIME_DIR": str(tmp_path / "runtime"),
                "PIPER_TTS_ENV_FILE": str(tmp_path / "missing.env"),
                "PIPER_TTS_MODEL_PATH": "/models/en_US-lessac-medium.onnx",
                "PIPER_TTS_DOWNLOAD_VOICES": "en_US-lessac-medium",
                "PIPER_TTS_WARM_VOICES": "",
                "PIPER_TTS_VOICE_REPO_URL": source_root.as_uri(),
                "PIPER_TTS_HEALTH_URL": service_url,
                "PIPER_TTS_HEALTH_TIMEOUT_S": "5",
                "PIPER_TTS_HEALTH_INTERVAL_S": "0",
            },
            text=True,
            capture_output=True,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert "download en_US-lessac-medium.onnx" in result.stdout
    assert "model_exists" in result.stdout
    assert docker_log.read_text(encoding="utf-8").splitlines() == [
        f"compose -f {repo_root / 'compose.piper-tts.yaml'} up -d --build"
    ]
    assert ("GET", "/health") in _TtsHandler.requests
    assert ("PUT", "/config") in _TtsHandler.requests
