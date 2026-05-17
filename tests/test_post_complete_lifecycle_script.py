import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import subprocess
import threading


ROOT = Path(__file__).resolve().parents[1]


class LifecycleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/setup/ready/status":
            self._json(
                {
                    "completed": True,
                    "operational_ready": True,
                    "setup_root_redirect_active": False,
                }
            )
            return
        if self.path == "/api/services/status":
            self._json(
                {
                    "components": [
                        {"component_id": "backend", "status": "running", "healthy": True},
                        {
                            "component_id": "stt",
                            "status": "running",
                            "healthy": True,
                            "resource_scope": "systemd_user_service",
                        },
                        {
                            "component_id": "tts",
                            "status": "running",
                            "healthy": True,
                            "resource_scope": "docker_container",
                        },
                        {
                            "component_id": "wake",
                            "status": "running",
                            "healthy": True,
                            "resource_scope": "docker_container",
                        },
                    ],
                    "supervisor": {"configured": True, "registered": True, "last_error": None},
                }
            )
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<html>ok</html>")

    def _json(self, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


def unused_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_post_complete_lifecycle_script_verifies_completed_node(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), LifecycleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts" / "verify-post-complete-lifecycle.py"),
                "--backend-url",
                base_url,
                "--frontend-url",
                f"{base_url}/",
                "--temp-url",
                f"http://127.0.0.1:{unused_port()}/setup/host",
                "--skip-systemd",
                "--skip-docker",
                "--json",
            ],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    finally:
        server.shutdown()

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    checks = {check["id"]: check for check in payload["checks"]}
    assert payload["ok"] is True
    assert checks["setup_completed_state"]["status"] == "pass"
    assert checks["production_service:backend"]["status"] == "pass"
    assert checks["supervisor_registration"]["status"] == "pass"
    assert checks["temporary_runner_shutdown"]["status"] == "pass"
