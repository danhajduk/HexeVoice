from __future__ import annotations

import json
from pathlib import Path
import socketserver
import subprocess
import sys
import threading


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT_DIR / "scripts" / "provider-lifecycle-validation.py"


class _LifecycleHandler(socketserver.BaseRequestHandler):
    services_payload = {
        "components": [
            {"component_id": "stt", "status": "running", "healthy": True, "warm_model_health": {"loaded": True}},
            {"component_id": "tts", "status": "running", "healthy": True},
            {"component_id": "wake", "status": "running", "healthy": True},
        ]
    }
    voice_payload = {
        "voice_tts_warmup": {"enabled": True, "last_run_at": "2026-08-22T21:00:00+00:00", "last_error": None},
        "voice_artifact_cleanup": {"last_run_at": "2026-08-22T21:00:00+00:00", "last_error": None},
        "voice_orphan_cleanup": {"last_run_at": "2026-08-22T21:00:00+00:00", "last_error": None},
    }

    def handle(self) -> None:
        request = self.request.recv(4096).decode("utf-8", errors="replace")
        path = request.split(" ", 2)[1]
        if path == "/api/services/status":
            payload = self.services_payload
            status = "200 OK"
        elif path == "/api/voice/status":
            payload = self.voice_payload
            status = "200 OK"
        elif path == "/api/endpoint/media":
            payload = {"assets": []}
            status = "200 OK"
        elif path == "/api/firmware/manifest":
            payload = {"filename": "hexe_firmware.bin", "size_bytes": 12}
            status = "200 OK"
        else:
            payload = {"detail": "not_found"}
            status = "404 Not Found"
        body = json.dumps(payload).encode("utf-8")
        self.request.sendall(
            b"HTTP/1.1 "
            + status.encode("ascii")
            + b"\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode("ascii")
            + b"\r\n\r\n"
            + body
        )


class _ThreadedTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _write_health_script(path: Path, provider: str) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$1\" in\n"
        f"  health) printf '{{\"provider\":\"{provider}\",\"healthy\":true}}\\n' ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_provider_lifecycle_validation_passes_repeated_cycles(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    _write_health_script(scripts_dir / "faster-whisper-stt-control.sh", "external_faster_whisper")
    _write_health_script(scripts_dir / "piper-tts-control.sh", "piper")
    _write_health_script(scripts_dir / "openwakeword-control.sh", "supervised_openwakeword")
    for rel in ("runtime/voice_tts", "runtime/wake_recordings", "runtime/endpoint_media", "runtime/firmware"):
        (tmp_path / rel).mkdir(parents=True)

    server = _ThreadedTcpServer(("127.0.0.1", 0), _LifecycleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(tmp_path),
                "--backend-url",
                f"http://127.0.0.1:{server.server_address[1]}",
                "--cycles",
                "2",
                "--interval-s",
                "0",
                "--json",
            ],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["summary"]["cycles"] == 2
    check_ids = {check["id"] for cycle in payload["cycles"] for check in cycle["checks"]}
    assert {"provider:stt", "provider:tts", "provider:wake", "tts_warmup", "endpoint_media_api", "firmware_manifest"} <= check_ids


def test_provider_lifecycle_validation_exits_nonzero_on_required_failure(tmp_path):
    server = _ThreadedTcpServer(("127.0.0.1", 0), _LifecycleHandler)
    original = _LifecycleHandler.services_payload
    _LifecycleHandler.services_payload = {
        "components": [
            {"component_id": "stt", "status": "running", "healthy": True},
            {"component_id": "tts", "status": "failed", "healthy": False},
            {"component_id": "wake", "status": "running", "healthy": True},
        ]
    }
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(tmp_path),
                "--backend-url",
                f"http://127.0.0.1:{server.server_address[1]}",
                "--cycles",
                "1",
                "--interval-s",
                "0",
                "--skip-control-scripts",
                "--json",
            ],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        _LifecycleHandler.services_payload = original
        server.shutdown()
        server.server_close()

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    failures = [check for check in payload["cycles"][0]["checks"] if check["status"] == "fail"]
    assert any(check["id"] == "provider:tts" for check in failures)
