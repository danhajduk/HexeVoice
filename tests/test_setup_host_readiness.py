import json
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient

from hexevoice.api.models import SetupHostReadinessActionRequest, SetupHostReadinessActionResponse
from hexevoice.config.settings import Settings
from hexevoice.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_setup_host_readiness_reports_required_runtime_dirs(tmp_path):
    runtime_dir = tmp_path / "runtime"
    client = TestClient(create_app(Settings(runtime_dir=runtime_dir, api_port=9004)))

    response = client.get("/api/setup/host-readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["production_setup_url"].endswith(":8084/setup/host")
    assert "continue" in payload["supported_actions"]
    assert "download-default-stt-model" in payload["supported_actions"]
    runtime_check = next(check for check in payload["checks"] if check["id"] == "runtime_dirs")
    assert runtime_check["status"] == "fail"
    assert "runtime_dirs" in payload["blockers"]
    stt_check = next(check for check in payload["checks"] if check["id"] == "stt_model")
    assert stt_check["detail"]["action"] == "download-default-stt-model"


def test_setup_host_stt_model_check_requires_non_empty_cache(tmp_path):
    runtime_dir = tmp_path / "runtime"
    for path in json.loads((ROOT / "config" / "runtime-dirs.json").read_text()).get("runtime_dirs", []):
        (runtime_dir / path).mkdir(parents=True, exist_ok=True)
    client = TestClient(create_app(Settings(runtime_dir=runtime_dir, api_port=9004)))

    response = client.get("/api/setup/host-readiness")

    assert response.status_code == 200
    stt_check = next(check for check in response.json()["checks"] if check["id"] == "stt_model")
    assert stt_check["status"] == "warn"
    assert "empty" in stt_check["message"]


def test_setup_host_continue_saves_setup_and_lifecycle_mode(tmp_path):
    runtime_dir = tmp_path / "runtime"
    for path in json.loads((ROOT / "config" / "runtime-dirs.json").read_text()).get("runtime_dirs", []):
        (runtime_dir / path).mkdir(parents=True, exist_ok=True)
    client = TestClient(create_app(Settings(runtime_dir=runtime_dir)))

    response = client.post(
        "/api/setup/host-readiness/actions/continue",
        json={
            "setup_mode": "migrate_existing",
            "lifecycle_mode": "joined_supervisor",
            "core_base_url": "http://10.0.0.100",
            "supervisor_id": "lab-supervisor",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["readiness"]["setup_mode"] == "migrate_existing"
    assert payload["readiness"]["lifecycle_mode"] == "joined_supervisor"
    assert payload["readiness"]["core_base_url"] == "http://10.0.0.100:9001"
    assert payload["readiness"]["enrollment_token_url"] == "http://10.0.0.100:9001/api/system/supervisors/enrollment-tokens"
    assert payload["readiness"]["enrollment_page_url"] == "http://10.0.0.100:9001/system/supervisors/enrollment?supervisor_id=lab-supervisor"


def test_setup_host_joined_supervisor_requires_token(tmp_path):
    client = TestClient(create_app(Settings(runtime_dir=tmp_path / "runtime")))

    response = client.post(
        "/api/setup/host-readiness/actions/install-joined-supervisor",
        json={
            "setup_mode": "new_node",
            "lifecycle_mode": "joined_supervisor",
            "core_base_url": "http://10.0.0.100:9001",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is False
    assert payload["message"] == "joined_supervisor_requires_core_url_and_enrollment_token"


def test_setup_host_lan_host_ignores_loopback_alias(monkeypatch):
    from hexevoice.setup_host import SetupHostReadinessService

    monkeypatch.setattr(SetupHostReadinessService, "_route_lan_host", staticmethod(lambda: ""))
    monkeypatch.setattr(
        SetupHostReadinessService,
        "_hostname_lan_hosts",
        staticmethod(lambda: ["127.0.1.1", "127.0.0.1", "10.0.0.55"]),
    )

    assert SetupHostReadinessService._lan_host() == "10.0.0.55"


def test_setup_host_actions_run_helpers_from_project_root(monkeypatch, tmp_path):
    from hexevoice.setup_host import SetupHostReadinessService

    recorded = {}
    service = SetupHostReadinessService(settings=Settings(runtime_dir=tmp_path / "runtime"))

    monkeypatch.setattr(SetupHostReadinessService, "_lan_host", staticmethod(lambda: "10.0.0.55"))

    def fake_run(command, **kwargs):
        recorded["command"] = command
        recorded["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("hexevoice.setup_host.subprocess.run", fake_run)

    response = service.run_action("install-host-alias", SetupHostReadinessActionRequest())

    assert response.accepted is True
    assert recorded["cwd"] == ROOT
    assert recorded["command"][1] == str(ROOT / "scripts" / "hostname-alias-control.sh")


def test_setup_host_default_asset_actions_use_control_scripts(monkeypatch, tmp_path):
    from hexevoice.setup_host import SetupHostReadinessService

    recorded = []
    service = SetupHostReadinessService(settings=Settings(runtime_dir=tmp_path / "runtime"))

    monkeypatch.setattr(SetupHostReadinessService, "_lan_host", staticmethod(lambda: "10.0.0.55"))

    def fake_run(command, **kwargs):
        recorded.append(
            {
                "command": command,
                "env": kwargs.get("env", {}),
                "timeout": kwargs.get("timeout"),
                "cwd": kwargs.get("cwd"),
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("hexevoice.setup_host.subprocess.run", fake_run)

    for action in (
        "download-default-stt-model",
        "download-default-tts-model",
        "download-default-wake-model",
        "download-firmware",
    ):
        response = service.run_action(action, SetupHostReadinessActionRequest())
        assert response.accepted is True

    assert recorded[0]["command"][-1] == "download-model"
    assert recorded[0]["env"]["VOICE_STT_FASTER_WHISPER_MODEL"] == "base"
    assert recorded[1]["command"][-1] == "download-models"
    assert recorded[1]["env"]["PIPER_TTS_MODEL_PATH"] == "/models/en_US-kathleen-low.onnx"
    assert recorded[2]["command"][-1] == "sync-models"
    assert recorded[2]["env"]["OPENWAKEWORD_DEFAULT_MODEL"] == "Hexe"
    assert recorded[3]["command"][-1] == "download"
    assert all(item["cwd"] == ROOT for item in recorded)
    assert all(item["timeout"] == 1800 for item in recorded)


def test_setup_host_joined_supervisor_defaults_id_to_hostname(monkeypatch, tmp_path):
    from hexevoice.setup_host import SetupHostReadinessService

    recorded = {}
    service = SetupHostReadinessService(settings=Settings(runtime_dir=tmp_path / "runtime"))

    monkeypatch.setattr("hexevoice.setup_host.socket.gethostname", lambda: "hexe-ai")
    monkeypatch.setattr(service, "_supervisor_installer", lambda: ROOT / "fake-install-supervisor.sh")

    def fake_run_helper(action, command, *, extra_env=None, timeout_s=120):
        recorded["action"] = action
        recorded["command"] = command
        return SetupHostReadinessActionResponse(
            accepted=True,
            action=action,
            message="ok",
            readiness=service.readiness_payload(),
        )

    monkeypatch.setattr(service, "_run_helper", fake_run_helper)

    response = service.run_action(
        "install-joined-supervisor",
        SetupHostReadinessActionRequest(
            lifecycle_mode="joined_supervisor",
            core_base_url="http://10.0.0.100:9001",
            enrollment_token="token-123",
        ),
    )

    assert response.accepted is True
    assert response.readiness.hostname == "hexe-ai"
    supervisor_index = recorded["command"].index("--supervisor-id") + 1
    assert recorded["command"][supervisor_index] == "hexe-ai-hexe-supervisor"
