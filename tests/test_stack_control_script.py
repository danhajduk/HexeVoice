import os
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
