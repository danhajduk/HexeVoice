from __future__ import annotations

from pathlib import Path
import socketserver
import subprocess
import sys
import threading


class _TcpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.recv(1)


class _ThreadedTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def test_openwakeword_control_sync_models_copies_default_hexe_model(tmp_path):
    model_dir = tmp_path / "models"

    result = subprocess.run(
        ["bash", "scripts/openwakeword-control.sh", "sync-models"],
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHON_BIN": sys.executable,
            "OPENWAKEWORD_ENV_FILE": str(tmp_path / "missing.env"),
            "OPENWAKEWORD_MODEL_DIR": str(model_dir),
            "OPENWAKEWORD_LEGACY_MODEL_DIR": str(tmp_path / "missing-legacy"),
        },
        text=True,
        capture_output=True,
        check=True,
    )

    assert "hexe.tflite" in result.stdout
    assert (model_dir / "hexe.tflite").exists()


def test_openwakeword_control_ready_syncs_starts_and_waits_for_health(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    server = _ThreadedTcpServer(("127.0.0.1", 0), _TcpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model_dir = tmp_path / "models"
        docker_log = tmp_path / "docker.log"
        fake_docker = tmp_path / "docker"
        fake_docker.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        fake_docker.chmod(0o755)

        result = subprocess.run(
            ["bash", "scripts/openwakeword-control.sh", "ready"],
            cwd=repo_root,
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHON_BIN": sys.executable,
                "DOCKER_BIN": str(fake_docker),
                "DOCKER_LOG": str(docker_log),
                "OPENWAKEWORD_ENV_FILE": str(tmp_path / "missing.env"),
                "OPENWAKEWORD_MODEL_DIR": str(model_dir),
                "OPENWAKEWORD_LEGACY_MODEL_DIR": str(tmp_path / "missing-legacy"),
                "OPENWAKEWORD_HEALTH_HOST": "127.0.0.1",
                "OPENWAKEWORD_HEALTH_PORT": str(server.server_address[1]),
                "OPENWAKEWORD_HEALTH_TIMEOUT_S": "5",
                "OPENWAKEWORD_HEALTH_INTERVAL_S": "0",
            },
            text=True,
            capture_output=True,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert "hexe.tflite" in result.stdout
    assert '"reachable": true' in result.stdout
    assert docker_log.read_text(encoding="utf-8").splitlines() == [
        f"compose -f {repo_root / 'compose.openwakeword.yaml'} up -d"
    ]
    assert (model_dir / "hexe.tflite").exists()
