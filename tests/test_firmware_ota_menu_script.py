import os
import subprocess
import textwrap


def test_firmware_ota_menu_lists_running_ota_status(tmp_path):
    curl = tmp_path / "curl"
    curl.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            url="${@: -1}"
            case "$url" in
              */api/endpoints)
                cat <<'JSON'
            {"endpoints":[{"endpoint_id":"esp-box-1","display_name":"Box","device_state":"ota","firmware_version":"old","connection_state":"online","capabilities":{"firmware":{"board_profile":"esp_box_3","ota":{"active":true,"status":"running","progress_percent":42}}},"firmware_update":{"latest_version":"new","update_available":true,"filename":"hexe_firmware_esp_box_3.bin","profile":"esp_box_3"}}]}
            JSON
                ;;
              */api/voice/status)
                cat <<'JSON'
            {"commands":[{"request_id":"cmd-1","endpoint_id":"esp-box-1","command_type":"ota.update","status":"accepted","terminal":false,"created_at":"2026-08-23T19:00:00Z"}]}
            JSON
                ;;
              *)
                echo "unexpected URL: $url" >&2
                exit 2
                ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    curl.chmod(0o755)

    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "API_BASE_URL": "http://hexe.local:9004"}
    result = subprocess.run(
        ["bash", "scripts/firmware-ota-menu.sh", "--list"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    assert "esp-box-1" in result.stdout
    assert "running:42%" in result.stdout
    assert "new/esp_box_3" in result.stdout
