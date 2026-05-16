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
