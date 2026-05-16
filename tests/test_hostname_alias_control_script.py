from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _run_alias_script(hosts_path: Path, action: str, *, enabled: bool = False) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "HEXEVOICE_HOSTS_PATH": str(hosts_path),
        "HEXEVOICE_CURRENT_HOSTNAME": "hexe-ai",
    }
    if enabled:
        env["HEXEVOICE_ENABLE_HOST_ALIAS"] = "true"
    return subprocess.run(
        ["bash", "scripts/hostname-alias-control.sh", action],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_hostname_alias_dry_run_does_not_write_hosts(tmp_path):
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")

    result = _run_alias_script(hosts_path, "dry-run")

    assert result.returncode == 0
    assert "would_append:127.0.1.1 hexe-ai HexeVoice HexeVoice.local" in result.stdout
    assert hosts_path.read_text(encoding="utf-8") == "127.0.0.1 localhost\n"


def test_hostname_alias_install_requires_explicit_enable(tmp_path):
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")

    result = _run_alias_script(hosts_path, "install")

    assert result.returncode == 2
    assert "host_alias_not_enabled" in result.stderr
    assert "HexeVoice" not in hosts_path.read_text(encoding="utf-8")


def test_hostname_alias_install_appends_alias_and_backup(tmp_path):
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")

    result = _run_alias_script(hosts_path, "install", enabled=True)
    second = _run_alias_script(hosts_path, "install", enabled=True)

    assert result.returncode == 0
    assert "host_alias_installed" in result.stdout
    hosts_text = hosts_path.read_text(encoding="utf-8")
    assert "127.0.1.1 hexe-ai HexeVoice HexeVoice.local" in hosts_text
    assert second.returncode == 0
    assert "host_alias_already_present" in second.stdout
    assert hosts_text.count("HexeVoice") == 2
    assert list(tmp_path.glob("hosts.hexevoice-backup-*"))
