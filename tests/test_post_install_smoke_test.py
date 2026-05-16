from __future__ import annotations

import http.server
import json
from pathlib import Path
import socketserver
import subprocess
import sys
import threading


class _SmokeHandler(http.server.BaseHTTPRequestHandler):
    service_status: dict = {}
    frontend_ok = True

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/services/status":
            self._json(self.service_status)
            return
        if self.path == "/" and self.frontend_ok:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_error(404)

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def _write_health_script(path: Path, payload: dict, exit_code: int = 0) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"print(json.dumps({payload!r}))\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _smoke_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    for rel in ("runtime/sockets", "runtime/firmware", "runtime/stt/faster-whisper", "runtime/piper-tts/models", "runtime/openwakeword/models"):
        (root / rel).mkdir(parents=True)
    _write_health_script(root / "scripts" / "faster-whisper-stt-control.sh", {"provider": "external_faster_whisper", "healthy": True})
    _write_health_script(root / "scripts" / "piper-tts-control.sh", {"provider": "piper", "status": "ok"})
    _write_health_script(root / "scripts" / "openwakeword-control.sh", {"provider": "supervised_openwakeword", "reachable": True})
    return root


def test_post_install_smoke_test_passes_with_healthy_stack(tmp_path):
    _SmokeHandler.service_status = {
        "backend": "running",
        "components": [
            {"component_id": "stt", "status": "running", "healthy": True},
            {"component_id": "tts", "status": "running", "healthy": True},
            {"component_id": "wake", "status": "running", "healthy": True},
        ],
    }
    server = _ThreadedServer(("127.0.0.1", 0), _SmokeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/post-install-smoke-test.py",
                "--root",
                str(_smoke_root(tmp_path)),
                "--backend-url",
                url,
                "--frontend-url",
                f"{url}/",
                "--skip-docker",
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["summary"]["failed"] == 0


def test_post_install_smoke_test_reports_engine_failure(tmp_path):
    _SmokeHandler.service_status = {
        "backend": "running",
        "components": [
            {"component_id": "stt", "status": "running", "healthy": False},
            {"component_id": "tts", "status": "running", "healthy": True},
            {"component_id": "wake", "status": "running", "healthy": True},
        ],
    }
    root = _smoke_root(tmp_path)
    _write_health_script(root / "scripts" / "faster-whisper-stt-control.sh", {"provider": "external_faster_whisper", "healthy": False}, 1)
    server = _ThreadedServer(("127.0.0.1", 0), _SmokeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/post-install-smoke-test.py",
                "--root",
                str(root),
                "--backend-url",
                url,
                "--frontend-url",
                f"{url}/",
                "--skip-docker",
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["ok"] is False
    failed = [check["id"] for check in payload["checks"] if check["status"] == "fail"]
    assert "stt_status" in failed
    assert "stt_health" in failed


def test_post_install_smoke_test_can_check_host_alias(tmp_path):
    _SmokeHandler.service_status = {
        "backend": "running",
        "components": [
            {"component_id": "stt", "status": "running", "healthy": True},
            {"component_id": "tts", "status": "running", "healthy": True},
            {"component_id": "wake", "status": "running", "healthy": True},
        ],
    }
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.1.1 hexe-ai HexeVoice HexeVoice.local\n", encoding="utf-8")
    server = _ThreadedServer(("127.0.0.1", 0), _SmokeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/post-install-smoke-test.py",
                "--root",
                str(_smoke_root(tmp_path)),
                "--backend-url",
                url,
                "--frontend-url",
                f"{url}/",
                "--skip-docker",
                "--check-host-alias",
                "--hosts-path",
                str(hosts_path),
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    payload = json.loads(result.stdout)
    host_alias = next(check for check in payload["checks"] if check["id"] == "host_alias")
    assert host_alias["status"] == "pass"
