import json

from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app


def test_setup_bootstrap_status_defaults_to_idle(tmp_path):
    client = TestClient(create_app(Settings(runtime_dir=tmp_path)))

    response = client.get("/api/setup/bootstrap/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == "idle"
    assert payload["completed_actions"] == []
    assert payload["pending_downloads"] == []
    assert payload["failures"] == []
    assert payload["retryable_failures"] == []


def test_setup_bootstrap_status_reports_progress_and_retryable_failures(tmp_path):
    status_path = tmp_path / "setup" / "bootstrap-status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(
        json.dumps(
            {
                "phase": "running",
                "current_action": "downloading-default-models",
                "completed_actions": ["temporary-backend-started", "temporary-frontend-started"],
                "pending_downloads": ["stt:base", "tts:en_US-kathleen-low.onnx"],
                "failures": [
                    {
                        "id": "firmware_download_failed",
                        "message": "Firmware download failed.",
                        "retryable": True,
                    },
                    {
                        "id": "systemd_unavailable",
                        "message": "systemd user services are unavailable.",
                        "retryable": False,
                    },
                ],
                "temporary_setup_url": "http://10.0.0.55:8180/setup/host",
                "production_setup_url": "http://10.0.0.55:8084/setup/host",
                "lifecycle_mode": "systemd",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(Settings(runtime_dir=tmp_path)))

    response = client.get("/api/setup/bootstrap/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == "running"
    assert payload["current_action"] == "downloading-default-models"
    assert payload["completed_actions"] == ["temporary-backend-started", "temporary-frontend-started"]
    assert payload["pending_downloads"] == ["stt:base", "tts:en_US-kathleen-low.onnx"]
    assert [failure["id"] for failure in payload["failures"]] == ["firmware_download_failed", "systemd_unavailable"]
    assert [failure["id"] for failure in payload["retryable_failures"]] == ["firmware_download_failed"]
    assert payload["temporary_setup_url"] == "http://10.0.0.55:8180/setup/host"
    assert payload["production_setup_url"] == "http://10.0.0.55:8084/setup/host"


def test_setup_bootstrap_status_reports_unreadable_status_file(tmp_path):
    status_path = tmp_path / "setup" / "bootstrap-status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text("{not-json", encoding="utf-8")
    client = TestClient(create_app(Settings(runtime_dir=tmp_path)))

    response = client.get("/api/setup/bootstrap/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == "error"
    assert payload["failures"][0]["id"] == "bootstrap_status_unreadable"
    assert payload["retryable_failures"][0]["id"] == "bootstrap_status_unreadable"
