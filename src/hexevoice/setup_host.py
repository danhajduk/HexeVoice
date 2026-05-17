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
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit, urlunsplit

from hexevoice.api.models import (
    SetupHostReadinessActionRequest,
    SetupHostReadinessActionResponse,
    SetupHostReadinessCheck,
    SetupHostReadinessResponse,
)
from hexevoice.config.settings import Settings


SUPPORTED_ACTIONS = [
    "prepare-runtime-dirs",
    "download-default-stt-model",
    "download-default-tts-model",
    "download-default-wake-model",
    "download-firmware",
    "check-cuda",
    "redetect-lan-ip",
    "recheck-supervisor",
    "restart-temporary-services",
    "restart-production-services",
    "rerun-supervisor-registration",
    "rebuild-systemd-services",
    "install-host-alias",
    "install-standalone-supervisor",
    "install-joined-supervisor",
    "continue",
]

DEFAULT_STT_MODEL = "base"
DEFAULT_PIPER_VOICE = "en_US-kathleen-low"
DEFAULT_WAKE_MODEL = "Hexe"
ASSET_ACTION_TIMEOUT_S = 1800
SUPERVISOR_LIFECYCLE_MODES = {"existing_supervisor", "joined_supervisor", "standalone_supervisor"}

READINESS_POLICY: dict[str, dict[str, str]] = {
    "backend": {"severity": "hard_blocker", "reason": "Temporary or production backend must answer setup APIs."},
    "frontend": {"severity": "hard_blocker", "reason": "Production setup UI must be reachable before temp redirect."},
    "api_url": {"severity": "hard_blocker", "reason": "Final API URL must be known before handoff."},
    "lan_url": {"severity": "hard_blocker", "reason": "LAN identity cannot be loopback or empty."},
    "node_identity": {"severity": "hard_blocker", "reason": "Node display name and nonce are required before handoff."},
    "runtime_dirs": {"severity": "hard_blocker", "reason": "Runtime directory skeleton must exist before services start."},
    "disk_space": {"severity": "hard_blocker", "reason": "Install/runtime directory must have minimum free disk space."},
    "docker": {"severity": "warning", "reason": "Docker is required by selected runtime providers, but provider setup enforces that later."},
    "systemd": {"severity": "warning", "reason": "systemd is required for unsupervised lifecycle service management."},
    "supervisor": {"severity": "conditional_blocker", "reason": "Supervisor is required only for Supervisor lifecycle modes."},
    "supervisor_registration": {"severity": "warning", "reason": "Registration is deferred until Core trust provides node ID."},
    "host_alias": {"severity": "warning", "reason": "Alias improves LAN access but is not required."},
    "cuda": {"severity": "warning", "reason": "CUDA is optional and can be configured later."},
    "firmware": {"severity": "warning", "reason": "Firmware can be downloaded or built later."},
    "stt_model": {"severity": "warning", "reason": "Default STT asset can be retried before provider setup."},
    "tts_model": {"severity": "warning", "reason": "Default TTS asset can be retried before provider setup."},
    "wake_model": {"severity": "warning", "reason": "Default wake model can be retried before provider setup."},
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SetupHostReadinessService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._project_root = self._find_project_root()
        self._state_path = settings.runtime_dir / "setup" / "host-state.json"

    def readiness_payload(self) -> SetupHostReadinessResponse:
        state = self._read_state()
        hostname = socket.gethostname() or "localhost"
        lan_host = self._lan_host()
        api_base_url = self._settings.public_api_base_url or f"http://{lan_host}:{self._settings.api_port}"
        ui_base_url = self._settings.public_ui_base_url or f"http://{lan_host}:8084"
        production_setup_url = f"{ui_base_url.rstrip('/')}/setup/host"
        temporary_setup_url = f"http://{lan_host}:8180/setup"
        node_identity = self._node_identity(api_base_url=api_base_url, ui_base_url=ui_base_url, hostname=hostname, lan_host=lan_host)
        lifecycle_mode = str(state.get("lifecycle_mode") or self._default_lifecycle_mode())
        checks = self._checks(
            lan_host=lan_host,
            api_base_url=api_base_url,
            ui_base_url=ui_base_url,
            node_identity=node_identity,
            lifecycle_mode=lifecycle_mode,
        )
        blockers = [check.id for check in checks if check.required and check.status == "fail"]
        warnings = [check.id for check in checks if check.status == "warn"]
        core_url = self._normalize_core_base_url(state.get("core_base_url") or self._core_url_from_state())
        return SetupHostReadinessResponse(
            ok=not blockers,
            hostname=hostname,
            lan_host=lan_host,
            node_identity=node_identity,
            temporary_setup_url=temporary_setup_url,
            production_setup_url=production_setup_url,
            api_base_url=api_base_url,
            ui_base_url=ui_base_url,
            core_base_url=core_url,
            setup_mode=str(state.get("setup_mode") or "new_node"),
            lifecycle_mode=lifecycle_mode,
            supervisor_detected=self._supervisor_detected(),
            checks=checks,
            blockers=blockers,
            warnings=warnings,
            supported_actions=SUPPORTED_ACTIONS,
            enrollment_token_url=self._enrollment_token_url(core_url),
            enrollment_page_url=self._enrollment_page_url(
                core_url,
                supervisor_id=str(state.get("supervisor_id") or self._default_supervisor_id()),
            ),
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
            selection = payload.model_dump(mode="json", exclude_none=True)
            if not selection.get("supervisor_id"):
                selection["supervisor_id"] = self._default_supervisor_id()
            self._write_state(selection)
            return SetupHostReadinessActionResponse(
                accepted=True,
                action=action,
                message="setup_host_selection_saved",
                retryable=False,
                readiness=self.readiness_payload(),
            )

        if action == "prepare-runtime-dirs":
            return self._run_helper(action, ["bash", str(self._project_root / "scripts" / "prepare-runtime-dirs.sh")])
        if action == "download-default-stt-model":
            return self._run_helper(
                action,
                ["bash", str(self._project_root / "scripts" / "faster-whisper-stt-control.sh"), "download-model"],
                extra_env={"VOICE_STT_FASTER_WHISPER_MODEL": DEFAULT_STT_MODEL},
                timeout_s=ASSET_ACTION_TIMEOUT_S,
            )
        if action == "download-default-tts-model":
            return self._run_helper(
                action,
                ["bash", str(self._project_root / "scripts" / "piper-tts-control.sh"), "download-models"],
                extra_env={
                    "PIPER_TTS_MODEL_PATH": f"/models/{DEFAULT_PIPER_VOICE}.onnx",
                    "PIPER_TTS_DOWNLOAD_VOICES": DEFAULT_PIPER_VOICE,
                },
                timeout_s=ASSET_ACTION_TIMEOUT_S,
            )
        if action == "download-default-wake-model":
            return self._run_helper(
                action,
                ["bash", str(self._project_root / "scripts" / "openwakeword-control.sh"), "sync-models"],
                extra_env={"OPENWAKEWORD_DEFAULT_MODEL": DEFAULT_WAKE_MODEL},
                timeout_s=ASSET_ACTION_TIMEOUT_S,
            )
        if action == "download-firmware":
            return self._run_helper(
                action,
                ["bash", str(self._project_root / "scripts" / "firmware-artifacts-control.sh"), "download"],
                timeout_s=ASSET_ACTION_TIMEOUT_S,
            )
        if action == "check-cuda":
            return self._run_helper(action, ["bash", str(self._project_root / "scripts" / "faster-whisper-stt-control.sh"), "cuda-preflight"])
        if action == "redetect-lan-ip":
            lan_host = self._lan_host()
            return SetupHostReadinessActionResponse(
                accepted=True,
                action=action,
                message=f"lan_host:{lan_host}",
                retryable=False,
                readiness=self.readiness_payload(),
            )
        if action == "recheck-supervisor":
            detected = self._supervisor_detected()
            return SetupHostReadinessActionResponse(
                accepted=True,
                action=action,
                message="supervisor_detected" if detected else "supervisor_not_detected",
                retryable=not detected,
                readiness=self.readiness_payload(),
            )
        if action == "restart-temporary-services":
            return SetupHostReadinessActionResponse(
                accepted=False,
                action=action,
                message="temporary_service_restart_requires_restarting_setup_runner",
                retryable=True,
                readiness=self.readiness_payload(),
            )
        if action == "restart-production-services":
            return self._run_helper(action, ["bash", str(self._project_root / "scripts" / "restart-stack.sh")], timeout_s=180)
        if action == "rerun-supervisor-registration":
            return self._post_supervisor_registration(action)
        if action == "rebuild-systemd-services":
            return self._run_helper(action, ["bash", str(self._project_root / "scripts" / "bootstrap.sh")], timeout_s=180)
        if action == "install-host-alias":
            env = {"HEXEVOICE_ENABLE_HOST_ALIAS": "true"}
            return self._run_helper(action, ["bash", str(self._project_root / "scripts" / "hostname-alias-control.sh"), "install"], extra_env=env)
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
            supervisor_id = payload.supervisor_id or self._default_supervisor_id()
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

    def _checks(
        self,
        *,
        lan_host: str,
        api_base_url: str,
        ui_base_url: str,
        node_identity: dict[str, Any],
        lifecycle_mode: str,
    ) -> list[SetupHostReadinessCheck]:
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
            policy = READINESS_POLICY.get(check_id, {"severity": "warning", "reason": ""})
            merged_detail = {"policy": policy, **(detail or {})}
            checks.append(
                SetupHostReadinessCheck(
                    id=check_id,
                    label=label,
                    status=status,  # type: ignore[arg-type]
                    required=required,
                    message=message,
                    detail=merged_detail,
                )
            )

        add("backend", "Backend API", "pass", "Backend API is serving host readiness.", required=True)
        add("lan_url", "LAN URL", "pass" if lan_host else "fail", f"LAN host is {lan_host}.", required=True)
        add(
            "node_identity",
            "Node identity",
            "pass" if node_identity.get("configured") else "fail",
            "Node identity is saved."
            if node_identity.get("configured")
            else "Node display name and nonce must be saved before production handoff.",
            required=True,
            detail={
                "node_name": node_identity.get("node_name"),
                "node_type": node_identity.get("node_type"),
                "node_id": node_identity.get("node_id"),
                "api_base_url": node_identity.get("api_base_url"),
                "ui_endpoint": node_identity.get("ui_endpoint"),
            },
        )

        runtime_missing = [path for path in self._runtime_dirs() if not (self._settings.runtime_dir / path).is_dir()]
        add(
            "runtime_dirs",
            "Runtime directories",
            "pass" if not runtime_missing else "fail",
            "Runtime directory skeleton is ready." if not runtime_missing else "Runtime directories are missing.",
            required=True,
            detail={"missing": runtime_missing[:20]},
        )

        add("frontend", "Frontend URL", "pass", f"Production UI target is {ui_base_url}.", required=True, detail={"ui_base_url": ui_base_url})
        add("api_url", "API URL", "pass", f"Production API target is {api_base_url}.", required=True, detail={"api_base_url": api_base_url})

        docker = shutil.which("docker")
        add("docker", "Docker", "pass" if docker else "warn", "Docker executable is available." if docker else "Docker executable was not found.")
        systemctl = shutil.which("systemctl")
        add(
            "systemd",
            "systemd user services",
            "pass" if systemctl else "warn",
            "systemctl is available." if systemctl else "systemctl was not found.",
        )
        supervisor_required = lifecycle_mode in SUPERVISOR_LIFECYCLE_MODES
        supervisor_detected = self._supervisor_detected()
        add(
            "supervisor",
            "Host Supervisor",
            "pass" if supervisor_detected else "fail" if supervisor_required else "warn",
            "Supervisor socket is visible."
            if supervisor_detected
            else "Supervisor socket is required by the selected lifecycle mode."
            if supervisor_required
            else "Supervisor socket was not detected.",
            required=supervisor_required,
            detail={
                "socket": os.environ.get("HEXE_SUPERVISOR_API_SOCKET", "/run/hexe/supervisor.sock"),
                "lifecycle_mode": lifecycle_mode,
            },
        )
        add(
            "supervisor_registration",
            "Supervisor registration",
            *self._supervisor_registration_status(node_identity),
            detail={
                "node_id": node_identity.get("node_id"),
                "deferred_until_trusted": not bool(node_identity.get("node_id")),
            },
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
        add(
            "firmware",
            "Firmware artifacts",
            *self._artifact_status(self._settings.resolved_firmware_artifact_dir(), ["manifest.json"]),
            detail={"action": "download-firmware", "path": str(self._settings.resolved_firmware_artifact_dir())},
        )
        add(
            "stt_model",
            "STT base model",
            *self._artifact_status(self._settings.runtime_dir / "stt" / "faster-whisper", []),
            detail={
                "action": "download-default-stt-model",
                "model": DEFAULT_STT_MODEL,
                "path": str(self._settings.runtime_dir / "stt" / "faster-whisper"),
            },
        )
        add(
            "tts_model",
            "Piper Kathleen voice",
            *self._artifact_status(self._settings.resolved_piper_tts_model_dir(), [f"{DEFAULT_PIPER_VOICE}.onnx"]),
            detail={
                "action": "download-default-tts-model",
                "voice": DEFAULT_PIPER_VOICE,
                "path": str(self._settings.resolved_piper_tts_model_dir()),
            },
        )
        add(
            "wake_model",
            "Hexe wake model",
            *self._artifact_status(self._settings.runtime_dir / "openwakeword" / "models", ["hexe.tflite"]),
            detail={
                "action": "download-default-wake-model",
                "model": DEFAULT_WAKE_MODEL,
                "path": str(self._settings.runtime_dir / "openwakeword" / "models"),
            },
        )
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
        elif path.is_dir() and not any(path.iterdir()):
            return ("warn", f"{path} is empty.")
        return ("pass", f"{path} is present.")

    def _runtime_dirs(self) -> list[str]:
        config_path = self._project_root / "config" / "runtime-dirs.json"
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

    def _supervisor_registration_status(self, node_identity: dict[str, Any]) -> tuple[str, str]:
        if not self._supervisor_detected():
            return ("warn", "Supervisor registration is unavailable until a Supervisor socket is detected.")
        if not node_identity.get("node_id"):
            return ("warn", "Supervisor runtime registration is deferred until Core trust provides a node ID.")
        return ("pass", "Supervisor runtime registration can use the trusted node ID.")

    @staticmethod
    def _default_supervisor_id() -> str:
        return f"{socket.gethostname() or 'hexevoice'}-hexe-supervisor"

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

    def _node_identity(self, *, api_base_url: str, ui_base_url: str, hostname: str, lan_host: str) -> dict[str, Any]:
        try:
            payload = json.loads(self._settings.resolved_onboarding_state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        pre_trust = payload.get("pre_trust") if isinstance(payload.get("pre_trust"), dict) else {}
        trust_activation = payload.get("trust_activation") if isinstance(payload.get("trust_activation"), dict) else {}
        node_name = str(pre_trust.get("node_name") or self._settings.node_name or hostname)
        protocol_version = str(pre_trust.get("protocol_version") or "1.0")
        node_nonce = str(pre_trust.get("node_nonce") or "")
        return {
            "configured": bool(pre_trust.get("node_name") and protocol_version and node_nonce),
            "node_name": node_name,
            "node_type": trust_activation.get("node_type") or self._settings.node_type,
            "node_id": trust_activation.get("node_id"),
            "protocol_version": protocol_version,
            "node_nonce": node_nonce,
            "requested_node_id": pre_trust.get("requested_node_id") or "",
            "hostname": pre_trust.get("hostname") or hostname,
            "lan_host": lan_host,
            "api_base_url": pre_trust.get("api_base_url") or api_base_url,
            "ui_endpoint": pre_trust.get("ui_endpoint") or ui_base_url,
        }

    @staticmethod
    def _normalize_core_base_url(core_base_url: object) -> str | None:
        raw = str(core_base_url or "").strip().rstrip("/")
        if not raw:
            return None
        if "://" not in raw:
            raw = f"http://{raw}"
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return raw
        if not parsed.hostname:
            return raw
        netloc = parsed.netloc
        if parsed.scheme == "http" and parsed.port is None:
            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            auth = ""
            if parsed.username:
                auth = parsed.username
                if parsed.password:
                    auth += f":{parsed.password}"
                auth += "@"
            netloc = f"{auth}{host}:9001"
        return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", "")).rstrip("/")

    @staticmethod
    def _enrollment_token_url(core_base_url: str | None) -> str | None:
        if not core_base_url:
            return None
        return f"{core_base_url.rstrip('/')}/api/system/supervisors/enrollment-tokens"

    @staticmethod
    def _enrollment_page_url(core_base_url: str | None, *, supervisor_id: str | None = None) -> str | None:
        if not core_base_url:
            return None
        base = f"{core_base_url.rstrip('/')}/system/supervisors/enrollment"
        supervisor_id = str(supervisor_id or "").strip()
        if not supervisor_id:
            return base
        return f"{base}?{urlencode({'supervisor_id': supervisor_id})}"

    def _supervisor_installer(self) -> Path | None:
        for local in (
            self._project_root / "docs" / "Core-Documents" / "core" / "scripts" / "install-supervisor.sh",
            self._project_root / "docs" / "Core-Documents" / "supervisor" / "scripts" / "install-supervisor.sh",
            self._project_root / "docs" / "Core-Documents" / "scripts" / "install-supervisor.sh",
        ):
            if local.exists() and os.access(local, os.X_OK):
                return local
        found = shutil.which("install-supervisor.sh")
        return Path(found) if found else None

    @staticmethod
    def _find_project_root() -> Path:
        env_root = os.environ.get("HEXEVOICE_PROJECT_ROOT")
        if env_root:
            return Path(env_root).expanduser().resolve()

        package_root = Path(__file__).resolve().parents[2]
        if (package_root / "scripts").is_dir():
            return package_root

        try:
            return Path.cwd()
        except OSError:
            return package_root

    def _run_helper(
        self,
        action: str,
        command: list[str],
        *,
        extra_env: dict[str, str] | None = None,
        timeout_s: int = 120,
    ) -> SetupHostReadinessActionResponse:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout_s,
                check=False,
                env=env,
                cwd=self._project_root,
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

    def _post_supervisor_registration(self, action: str) -> SetupHostReadinessActionResponse:
        lan_host = self._lan_host()
        api_base_url = self._settings.public_api_base_url or f"http://{lan_host}:{self._settings.api_port}"
        url = f"{api_base_url.rstrip('/')}/api/setup/supervisor/register-runtime"
        request = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return SetupHostReadinessActionResponse(
                accepted=False,
                action=action,
                message=body or f"http_error_{exc.code}",
                retryable=True,
                readiness=self.readiness_payload(),
            )
        except Exception as exc:
            return SetupHostReadinessActionResponse(
                accepted=False,
                action=action,
                message=str(exc),
                retryable=True,
                readiness=self.readiness_payload(),
            )

        accepted = True
        retryable = False
        message = body.strip() or "supervisor_registration_requested"
        try:
            payload = json.loads(body or "{}")
            status = str(payload.get("status") or "")
            reason = str(payload.get("reason") or "")
            if status and status not in {"ok", "skipped"}:
                accepted = False
                retryable = True
            message = reason or status or message
        except json.JSONDecodeError:
            pass

        return SetupHostReadinessActionResponse(
            accepted=accepted,
            action=action,
            message=message,
            retryable=retryable,
            readiness=self.readiness_payload(),
        )
