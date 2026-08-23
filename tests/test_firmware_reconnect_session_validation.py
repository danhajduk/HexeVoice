from __future__ import annotations

import json
from pathlib import Path
import socketserver
import subprocess
import sys
import threading


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT_DIR / "scripts" / "firmware-reconnect-session-validation.py"
RESULTS_PATH = ROOT_DIR / "docs" / "firmware-reconnect-session-results.json"
PROCEDURE_PATH = ROOT_DIR / "docs" / "firmware-reconnect-session-validation.md"
MATRIX_PATH = ROOT_DIR / "docs" / "firmware-validation-matrix.json"
MATRIX_DOC_PATH = ROOT_DIR / "docs" / "firmware-validation-matrix.md"

SCENARIOS = [
    "backend_restart_idle",
    "endpoint_power_cycle",
    "wifi_loss_rejoin",
    "active_session_disconnect",
    "post_tts_cooldown",
    "wake_retry",
    "duplicate_session_prevention",
]


class _ReconnectHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = self.request.recv(4096).decode("utf-8", errors="replace")
        path = request.split(" ", 2)[1]
        if path == "/api/endpoint/status/esp-box-1":
            payload = {
                "endpoint_id": "esp-box-1",
                "device_state": "idle",
                "connection_state": "online",
                "firmware_version": "0.2.0",
                "last_seen_at": "2026-08-22T22:30:00+00:00",
                "capabilities": {"firmware": {"board_profile": "esp_box_3"}},
            }
            status = "200 OK"
        elif path == "/api/endpoint/status/esp-pe-1":
            payload = {
                "endpoint_id": "esp-pe-1",
                "device_state": "idle",
                "connection_state": "online",
                "firmware_version": "0.2.0",
                "last_seen_at": "2026-08-22T22:30:00+00:00",
                "capabilities": {"firmware": {"board_profile": "ha_voice_pe"}},
            }
            status = "200 OK"
        elif path == "/api/voice/status":
            payload = {
                "connection_count": 2,
                "connected_endpoint_ids": ["esp-box-1", "esp-pe-1"],
                "state_projection": {"session_state": None},
            }
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


def test_firmware_reconnect_session_runner_records_passing_field_results(tmp_path):
    server = _ThreadedTcpServer(("127.0.0.1", 0), _ReconnectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result_args = [
        f"--result={profile}:{scenario}=pass:bench_observed"
        for profile in ("esp_box_3", "ha_voice_pe")
        for scenario in SCENARIOS
    ]
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--backend-url",
                f"http://127.0.0.1:{server.server_address[1]}",
                "--non-interactive",
                "--json",
                *result_args,
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
    assert payload["overall_status"] == "pass"
    assert {profile["profile"] for profile in payload["profiles"]} == {"esp_box_3", "ha_voice_pe"}
    for profile in payload["profiles"]:
        assert profile["status"] == "pass"
        assert {scenario["id"] for scenario in profile["scenarios"]} == set(SCENARIOS)
        assert {scenario["status"] for scenario in profile["scenarios"]} == {"pass"}
        assert all(scenario["endpoint_observation"]["online"] for scenario in profile["scenarios"])


def test_firmware_reconnect_session_seed_artifact_is_release_gating():
    payload = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["overall_status"] == "blocked"
    assert {profile["profile"] for profile in payload["profiles"]} == {"esp_box_3", "ha_voice_pe"}
    for profile in payload["profiles"]:
        assert profile["status"] == "blocked"
        assert {scenario["id"] for scenario in profile["scenarios"]} == set(SCENARIOS)
        assert {scenario["status"] for scenario in profile["scenarios"]} == {"blocked"}
    assert "repo task" in payload["follow_up_policy"].lower()


def test_firmware_validation_matrix_links_reconnect_session_rig_and_results():
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    doc = MATRIX_DOC_PATH.read_text(encoding="utf-8")
    procedure = PROCEDURE_PATH.read_text(encoding="utf-8")

    field_validation = matrix["field_validation"]
    assert field_validation["runner"] == "scripts/firmware-reconnect-session-validation.py"
    assert field_validation["reconnect_session_results"] == "docs/firmware-reconnect-session-results.json"
    assert field_validation["reconnect_session_procedure"] == "docs/firmware-reconnect-session-validation.md"
    assert "firmware-reconnect-session-validation.py" in doc
    assert "firmware-reconnect-session-results.json" in doc
    for phrase in (
        "backend restart while idle",
        "endpoint power cycle",
        "wi-fi loss and rejoin",
        "active-session disconnect",
        "post-tts cooldown",
        "wake retry",
        "duplicate-session prevention",
    ):
        assert phrase in procedure.lower()
