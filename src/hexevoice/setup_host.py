from __future__ import annotations

from datetime import UTC, datetime
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any

from hexevoice.api.models import (
    SetupHostReadinessActionRequest,
    SetupHostReadinessActionResponse,
    SetupHostReadinessCheck,
    SetupHostReadinessResponse,
)
from hexevoice.config.settings import Settings


SUPPORTED_ACTIONS = [
    "prepare-runtime-dirs",
    "check-cuda",
    "install-host-alias",
    "install-standalone-supervisor",
    "install-joined-supervisor",
    "continue",
]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SetupHostReadinessService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._state_path = settings.runtime_dir / "setup" / "host-state.json"

    def readiness_payload(self) -> SetupHostReadinessResponse:
        state = self._read_state()
        hostname = socket.gethostname() or "localhost"
        lan_host = self._lan_host()
        api_base_url = self._settings.public_api_base_url or f"http://{lan_host}:{self._settings.api_port}"
        ui_base_url = self._settings.public_ui_base_url or f"http://{lan_host}:8084"
        production_setup_url = f"{ui_base_url.rstrip('/')}/setup/host"
        temporary_setup_url = f"http://{lan_host}:8180/setup"
        checks = self._checks(lan_host=lan_host, api_base_url=api_base_url, ui_base_url=ui_base_url)
        blockers = [check.id for check in checks if check.required and check.status == "fail"]
        warnings = [check.id for check in checks if check.status == "warn"]
        core_url = state.get("core_base_url") or self._core_url_from_state()
        return SetupHostReadinessResponse(
            ok=not blockers,
            hostname=hostname,
            lan_host=lan_host,
            temporary_setup_url=temporary_setup_url,
            production_setup_url=production_setup_url,
            api_base_url=api_base_url,
            ui_base_url=ui_base_url,
            setup_mode=str(state.get("setup_mode") or "new_node"),
            lifecycle_mode=str(state.get("lifecycle_mode") or self._default_lifecycle_mode()),
            supervisor_detected=self._supervisor_detected(),
            checks=checks,
            blockers=blockers,
            warnings=warnings,
            supported_actions=SUPPORTED_ACTIONS,
            enrollment_token_url=self._enrollment_token_url(core_url),
            updated_at=_utc_now(),
        )

    def run_action(self, action: str, payload: SetupHostReadinessActionRequest) -> SetupHostReadinessActionResponse:
        if action not in SUPPORTED_ACTIONS:
            return SetupHostReadinessActionResponse(
                accepted=False,
                action=action,
                message="unsupported_setup_host_action",
                readiness=self.readiness_payload(),
            )

        if action == "continue":
            self._write_state(payload.model_dump(mode="json", exclude_none=True))
            return SetupHostReadinessActionResponse(
                accepted=True,
                action=action,
                message="setup_host_selection_saved",
                retryable=False,
                readiness=self.readiness_payload(),
            )

        if action == "prepare-runtime-dirs":
            return self._run_helper(action, ["bash", "scripts/prepare-runtime-dirs.sh"])
        if action == "check-cuda":
            return self._run_helper(action, ["bash", "scripts/faster-whisper-stt-control.sh", "cuda-preflight"])
        if action == "install-host-alias":
            env = {"HEXEVOICE_ENABLE_HOST_ALIAS": "true"}
            return self._run_helper(action, ["bash", "scripts/hostname-alias-control.sh", "install"], extra_env=env)
        if action == "install-standalone-supervisor":
            installer = self._supervisor_installer()
            if installer is None:
                return SetupHostReadinessActionResponse(
                    accepted=False,
                    action=action,
                    message="supervisor_installer_missing",
                    readiness=self.readiness_payload(),
                )
            return self._run_helper(action, [str(installer), "--standalone"])
        if action == "install-joined-supervisor":
            if not payload.core_base_url or not payload.enrollment_token:
                return SetupHostReadinessActionResponse(
                    accepted=False,
                    action=action,
                    message="joined_supervisor_requires_core_url_and_enrollment_token",
                    readiness=self.readiness_payload(),
                )
            installer = self._supervisor_installer()
            if installer is None:
                return SetupHostReadinessActionResponse(
                    accepted=False,
                    action=action,
                    message="supervisor_installer_missing",
                    readiness=self.readiness_payload(),
                )
            supervisor_id = payload.supervisor_id or f"{socket.gethostname() or 'hexevoice'}-supervisor"
            return self._run_helper(
                action,
                [
                    str(installer),
                    "--join-core",
                    "--core-url",
                    payload.core_base_url,
                    "--enrollment-token",
                    payload.enrollment_token,
                    "--supervisor-id",
                    supervisor_id,
                ],
            )

        return SetupHostReadinessActionResponse(
            accepted=False,
            action=action,
            message="unsupported_setup_host_action",
            readiness=self.readiness_payload(),
        )

    def _checks(self, *, lan_host: str, api_base_url: str, ui_base_url: str) -> list[SetupHostReadinessCheck]:
        checks: list[SetupHostReadinessCheck] = []

        def add(
            check_id: str,
            label: str,
            status: str,
            message: str,
            *,
            required: bool = False,
            detail: dict[str, Any] | None = None,
        ) -> None:
            checks.append(
                SetupHostReadinessCheck(
                    id=check_id,
                    label=label,
                    status=status,  # type: ignore[arg-type]
                    required=required,
                    message=message,
                    detail=detail or {},
                )
            )

        add("backend", "Backend API", "pass", "Backend API is serving host readiness.", required=True)
        add("lan_url", "LAN URL", "pass" if lan_host else "fail", f"LAN host is {lan_host}.", required=True)

        runtime_missing = [path for path in self._runtime_dirs() if not (self._settings.runtime_dir / path).is_dir()]
        add(
            "runtime_dirs",
            "Runtime directories",
            "pass" if not runtime_missing else "fail",
            "Runtime directory skeleton is ready." if not runtime_missing else "Runtime directories are missing.",
            required=True,
            detail={"missing": runtime_missing[:20]},
        )

        add("frontend", "Frontend URL", "pass", f"Production UI target is {ui_base_url}.", detail={"ui_base_url": ui_base_url})
        add("api_url", "API URL", "pass", f"Production API target is {api_base_url}.", detail={"api_base_url": api_base_url})

        docker = shutil.which("docker")
        add("docker", "Docker", "pass" if docker else "warn", "Docker executable is available." if docker else "Docker executable was not found.")
        systemctl = shutil.which("systemctl")
        add(
            "systemd",
            "systemd user services",
            "pass" if systemctl else "warn",
            "systemctl is available." if systemctl else "systemctl was not found.",
        )
        add(
            "supervisor",
            "Host Supervisor",
            "pass" if self._supervisor_detected() else "warn",
            "Supervisor socket is visible." if self._supervisor_detected() else "Supervisor socket was not detected.",
            detail={"socket": os.environ.get("HEXE_SUPERVISOR_API_SOCKET", "/run/hexe/supervisor.sock")},
        )
        add(
            "host_alias",
            "HexeVoice host alias",
            "pass" if self._host_alias_present() else "warn",
            "HexeVoice alias is present." if self._host_alias_present() else "HexeVoice alias is not configured.",
        )
        add(
            "cuda",
            "CUDA",
            "pass" if shutil.which("nvidia-smi") else "warn",
            "nvidia-smi is available." if shutil.which("nvidia-smi") else "CUDA was not detected by the cheap host check.",
        )
        add(
            "disk_space",
            "Disk space",
            *self._disk_space_status(),
            required=True,
        )
        add("firmware", "Firmware artifacts", *self._artifact_status(self._settings.resolved_firmware_artifact_dir(), ["manifest.json"]))
        add("stt_model", "STT model cache", *self._artifact_status(self._settings.runtime_dir / "stt" / "faster-whisper", []))
        add("tts_model", "Piper TTS model", *self._artifact_status(self._settings.resolved_piper_tts_model_dir(), ["en_US-kathleen-low.onnx"]))
        add("wake_model", "Wake model", *self._artifact_status(self._settings.runtime_dir / "openwakeword" / "models", ["hexe.tflite"]))
        return checks

    def _disk_space_status(self) -> tuple[str, str]:
        try:
            target = self._settings.runtime_dir if self._settings.runtime_dir.exists() else self._settings.runtime_dir.parent
            usage = shutil.disk_usage(target)
            free_gib = round(usage.free / (1024**3), 2)
            return ("pass" if usage.free >= 1024**3 else "fail", f"{free_gib} GiB free near runtime directory.")
        except Exception as exc:
            return ("fail", f"Could not check disk space: {exc}")

    @staticmethod
    def _artifact_status(path: Path, expected_files: list[str]) -> tuple[str, str]:
        if not path.exists():
            return ("warn", f"{path} is missing.")
        if expected_files:
            missing = [name for name in expected_files if not (path / name).exists()]
            if missing:
                return ("warn", f"Missing expected files: {', '.join(missing)}.")
        return ("pass", f"{path} is present.")

    def _runtime_dirs(self) -> list[str]:
        config_path = Path("config/runtime-dirs.json")
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        dirs = payload.get("runtime_dirs")
        return [str(item) for item in dirs] if isinstance(dirs, list) else []

    def _read_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_state(self, payload: dict[str, Any]) -> None:
        payload["updated_at"] = _utc_now()
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._state_path.with_suffix(f"{self._state_path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self._state_path)

    @staticmethod
    def _lan_host() -> str:
        for candidate in (
            SetupHostReadinessService._route_lan_host(),
            *SetupHostReadinessService._hostname_lan_hosts(),
        ):
            if SetupHostReadinessService._usable_lan_host(candidate):
                return candidate
        return "127.0.0.1"

    @staticmethod
    def _route_lan_host() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.settimeout(1)
                probe.connect(("8.8.8.8", 80))
                return str(probe.getsockname()[0])
        except OSError:
            return ""

    @staticmethod
    def _hostname_lan_hosts() -> list[str]:
        candidates: list[str] = []
        try:
            candidates.append(socket.gethostbyname(socket.gethostname()))
        except OSError:
            pass
        try:
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                candidates.append(str(item[4][0]))
        except OSError:
            pass
        try:
            completed = subprocess.run(
                ["hostname", "-I"],
                text=True,
                capture_output=True,
                timeout=2,
                check=False,
            )
            candidates.extend((completed.stdout or "").split())
        except Exception:
            pass
        return candidates

    @staticmethod
    def _usable_lan_host(value: str) -> bool:
        try:
            address = ipaddress.ip_address(str(value).strip())
        except ValueError:
            return False
        return not (address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast)

    @staticmethod
    def _supervisor_detected() -> bool:
        return Path(os.environ.get("HEXE_SUPERVISOR_API_SOCKET", "/run/hexe/supervisor.sock")).exists()

    @staticmethod
    def _host_alias_present() -> bool:
        try:
            hosts = Path("/etc/hosts").read_text(encoding="utf-8")
        except OSError:
            return False
        return "HexeVoice" in hosts or "HexeVoice.local" in hosts

    def _default_lifecycle_mode(self) -> str:
        return "existing_supervisor" if self._supervisor_detected() else "unsupervised_systemd"

    def _core_url_from_state(self) -> str | None:
        try:
            payload = json.loads(self._settings.resolved_onboarding_state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        pre_trust = payload.get("pre_trust") if isinstance(payload, dict) else None
        if isinstance(pre_trust, dict) and pre_trust.get("core_base_url"):
            return str(pre_trust["core_base_url"])
        return None

    @staticmethod
    def _enrollment_token_url(core_base_url: str | None) -> str | None:
        if not core_base_url:
            return None
        return f"{core_base_url.rstrip('/')}/system/supervisors/enrollment-tokens"

    @staticmethod
    def _supervisor_installer() -> Path | None:
        local = Path("docs/Core-Documents/scripts/install-supervisor.sh")
        if local.exists() and os.access(local, os.X_OK):
            return local
        found = shutil.which("install-supervisor.sh")
        return Path(found) if found else None

    def _run_helper(
        self,
        action: str,
        command: list[str],
        *,
        extra_env: dict[str, str] | None = None,
    ) -> SetupHostReadinessActionResponse:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
                env=env,
            )
        except Exception as exc:
            return SetupHostReadinessActionResponse(
                accepted=False,
                action=action,
                message=str(exc),
                readiness=self.readiness_payload(),
            )
        output = (completed.stdout or completed.stderr or "").strip()
        return SetupHostReadinessActionResponse(
            accepted=completed.returncode == 0,
            action=action,
            message=output or ("ok" if completed.returncode == 0 else f"exit_code_{completed.returncode}"),
            retryable=completed.returncode != 0,
            readiness=self.readiness_payload(),
        )
