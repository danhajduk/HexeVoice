import os
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_systemd_templates_are_enableable_for_reboot_start():
    for template in [
        "hexevoice-backend.service.in",
        "hexevoice-frontend.service.in",
        "hexevoice-stt.service.in",
        "hexevoice-speaker-id.service.in",
        "hexevoice-openwakeword.service.in",
        "hexevoice-piper-tts.service.in",
    ]:
        content = (ROOT / "scripts" / "systemd" / template).read_text(encoding="utf-8")
        assert "[Install]" in content
        assert "WantedBy=default.target" in content

    for template in [
        "hexevoice-backend.service.in",
        "hexevoice-frontend.service.in",
        "hexevoice-stt.service.in",
        "hexevoice-speaker-id.service.in",
    ]:
        content = (ROOT / "scripts" / "systemd" / template).read_text(encoding="utf-8")
        assert "Restart=always" in content
        assert "RestartSec=5" in content


def test_bootstrap_installs_and_enables_full_runtime_stack():
    content = (ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")

    assert 'render_unit "hexevoice-openwakeword.service.in" "$OPENWAKEWORD_SERVICE_NAME"' in content
    assert 'render_unit "hexevoice-piper-tts.service.in" "$PIPER_TTS_SERVICE_NAME"' in content
    assert 'render_unit "hexevoice-speaker-id.service.in" "$SPEAKER_ID_SERVICE_NAME"' in content
    assert 'systemctl --user enable "${enabled_units[@]}"' in content
    assert 'loginctl enable-linger "$USER"' in content


def test_provider_compose_services_leave_restart_to_supervisor_lifecycle():
    for compose_file in [
        "compose.openwakeword.yaml",
        "compose.piper-tts.yaml",
        "compose.faster-whisper-stt.yaml",
    ]:
        content = (ROOT / compose_file).read_text(encoding="utf-8")
        assert 'restart: "no"' in content


def test_provider_runtime_units_retry_when_docker_is_not_ready():
    for template in [
        "hexevoice-openwakeword.service.in",
        "hexevoice-piper-tts.service.in",
    ]:
        content = (ROOT / "scripts" / "systemd" / template).read_text(encoding="utf-8")
        assert "Restart=on-failure" in content
        assert "RestartSec=10" in content


def test_stack_control_reports_missing_core_services(tmp_path):
    stack_env = tmp_path / "stack.env"
    stack_env.write_text(
        textwrap.dedent(
            """\
            BACKEND_CMD="echo backend"
            FRONTEND_CMD="echo frontend"
            BACKEND_SERVICE_NAME="hexevoice-backend.service"
            FRONTEND_SERVICE_NAME="hexevoice-frontend.service"
            STT_SERVICE_NAME="hexevoice-stt.service"
            """
        ),
        encoding="utf-8",
    )

    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [[ "${1:-}" == "--user" && "${2:-}" == "cat" ]]; then
              exit 1
            fi
            echo "unexpected systemctl call: $*" >&2
            exit 2
            """
        ),
        encoding="utf-8",
    )
    systemctl.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "STACK_ENV_FILE": str(stack_env),
    }
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "stack-control.sh"), "restart"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 3
    assert "Skipping hexevoice-stt.service: not installed" in result.stdout
    assert "Missing required user systemd service(s): hexevoice-backend.service hexevoice-frontend.service" in result.stderr
    assert "scripts/bootstrap.sh" in result.stderr
    assert "scripts/run-from-env.sh backend" in result.stderr
