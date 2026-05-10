from datetime import UTC, datetime
import socket
import asyncio
from collections.abc import Callable
import json
import logging
import os
from pathlib import Path
import subprocess
import time

from hexevoice.api.models import (
    ApiHealthResponse,
    CapabilitySetupProviderSelectionResponse,
    CapabilitySetupReadinessFlags,
    CapabilitySetupResponse,
    CapabilitySetupTaskSelectionResponse,
    CapabilitySummaryResponse,
    GovernanceReadinessResponse,
    NodeStatusResponse,
    OnboardingStepResponse,
    OnboardingStatusResponse,
    ProviderStatusResponse,
    ServiceActionResponse,
    ServiceStatusResponse,
)
from hexevoice.capabilities.service import VOICE_NODE_CAPABILITIES, capability_summary, normalize_capability_selection
from hexevoice.config.settings import Settings
from hexevoice.onboarding import CANONICAL_ONBOARDING_STEPS, initial_onboarding_step
from hexevoice.persistence import OnboardingStateStore
from hexevoice.piper_models import piper_model_display_name, read_piper_model_config
from hexevoice.supervisor.client import SupervisorApiClient


log = logging.getLogger(__name__)


class NodeRuntimeService:
    def __init__(
        self,
        *,
        settings: Settings,
        onboarding_state_store: OnboardingStateStore | None = None,
        supervisor_client: SupervisorApiClient | None = None,
        service_command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._settings = settings
        self._onboarding_state_store = onboarding_state_store or OnboardingStateStore(
            path=settings.resolved_onboarding_state_path()
        )
        self._supervisor_client = supervisor_client
        self._supervisor_registered = False
        self._supervisor_last_error: str | None = None
        self._supervisor_last_seen: str | None = None
        self._service_command_runner = service_command_runner or self._run_service_command
        self._started_at_monotonic = time.monotonic()

    def api_health_payload(self) -> ApiHealthResponse:
        return ApiHealthResponse(status="ok", version=self._settings.node_software_version)

    def _state(self):
        return self._onboarding_state_store.load()

    def _current_step(self, state=None):
        onboarding_state = state or self._state()
        current_step_id = onboarding_state.normalized_current_step_id()
        for step in CANONICAL_ONBOARDING_STEPS:
            if step.step_id == current_step_id:
                return step
        return initial_onboarding_step()

    def _trust_state(self, state=None) -> str:
        onboarding_state = state or self._state()
        return onboarding_state.trust_activation.trust_status or "untrusted"

    def _node_id(self, state=None) -> str | None:
        onboarding_state = state or self._state()
        return onboarding_state.trust_activation.node_id

    def _node_name(self, state=None) -> str:
        onboarding_state = state or self._state()
        return onboarding_state.pre_trust.node_name or self._settings.node_name

    def _blocking_reasons(self, current_step_id: str) -> list[str]:
        onboarding_state = self._state()
        support_state = onboarding_state.trust_activation.support_state
        trust_state = onboarding_state.trust_activation.trust_status
        if trust_state == "revoked":
            if support_state == "removed":
                return ["node_removed_by_core", "re_onboarding_required"]
            return ["trust_revoked_by_core", "re_onboarding_required"]

        blockers_by_step = {
            "node_identity": [
                "node_identity_not_configured",
                "core_connection_not_configured",
                "bootstrap_discovery_not_started",
                "onboarding_session_not_started",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "core_connection": [
                "core_connection_not_configured",
                "bootstrap_discovery_not_started",
                "onboarding_session_not_started",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "bootstrap_discovery": [
                "bootstrap_discovery_not_started",
                "onboarding_session_not_started",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "registration": [
                "onboarding_session_not_started",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "approval": [
                "approval_pending",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "trust_activation": [
                "trust_activation_pending",
                "provider_setup_not_started",
                "governance_sync_not_started",
            ],
            "provider_setup": [
                *(
                    onboarding_state.provider_setup.blocking_reasons
                    or ["provider_selection_required"]
                ),
                "capability_declaration_not_started",
                "governance_sync_not_started",
            ],
            "capability_declaration": [
                "capability_declaration_not_started",
                "governance_sync_not_started",
            ],
            "governance_sync": [
                "governance_sync_not_started",
            ],
            "ready": [],
        }
        return blockers_by_step.get(current_step_id, blockers_by_step[initial_onboarding_step().step_id])

    def _onboarding_state_label(self, current_step_id: str) -> tuple[str, str]:
        labels = {
            "node_identity": ("waiting_for_local_setup", "configure_node_identity"),
            "core_connection": ("waiting_for_local_setup", "configure_core_connection"),
            "bootstrap_discovery": ("bootstrap_pending", "run_bootstrap_discovery"),
            "registration": ("ready_to_register", "start_onboarding_session"),
            "approval": ("pending_approval", "review_approval_in_core"),
            "trust_activation": ("approval_granted", "finalize_trust_activation"),
            "provider_setup": ("trust_activated", "configure_provider_setup"),
            "capability_declaration": ("capability_setup_pending", "declare_node_capabilities"),
            "governance_sync": ("governance_pending", "refresh_governance"),
            "ready": ("operational", "monitor_operational_state"),
        }
        return labels.get(current_step_id, labels[initial_onboarding_step().step_id])

    def _capability_setup_payload(self, state=None) -> CapabilitySetupResponse:
        onboarding_state = state or self._state()
        enabled_providers = onboarding_state.provider_setup.enabled_providers
        supported_providers = onboarding_state.provider_setup.supported_providers or [self._settings.provider_id]
        selected_task_families = (
            normalize_capability_selection(onboarding_state.capability_declaration.declared_task_families)
            or VOICE_NODE_CAPABILITIES
        )
        available_task_families = VOICE_NODE_CAPABILITIES
        readiness_flags = CapabilitySetupReadinessFlags(
            trust_state_valid=onboarding_state.trust_activation.trust_status == "trusted",
            node_identity_valid=bool(onboarding_state.trust_activation.node_id),
            provider_selection_valid=bool(enabled_providers),
            task_capability_selection_valid=bool(selected_task_families) or onboarding_state.capability_declaration.capability_status != "missing",
            core_runtime_context_valid=bool(onboarding_state.pre_trust.core_base_url),
        )
        blocking_reasons = list(dict.fromkeys(onboarding_state.provider_setup.blocking_reasons or self._blocking_reasons("provider_setup")))
        declaration_allowed = (
            onboarding_state.provider_setup.declaration_allowed
            or (
                readiness_flags.trust_state_valid
                and readiness_flags.node_identity_valid
                and readiness_flags.provider_selection_valid
            )
        )
        return CapabilitySetupResponse(
            readiness_flags=readiness_flags,
            provider_selection=CapabilitySetupProviderSelectionResponse(
                configured=bool(enabled_providers),
                enabled_count=len(enabled_providers),
                enabled=enabled_providers,
                supported={
                    "cloud": supported_providers,
                    "local": [],
                    "future": [],
                },
            ),
            task_capability_selection=CapabilitySetupTaskSelectionResponse(
                configured=bool(selected_task_families) or onboarding_state.capability_declaration.capability_status != "missing",
                selected_count=len(selected_task_families),
                selected=selected_task_families,
                available=available_task_families,
            ),
            blocking_reasons=blocking_reasons,
            declaration_allowed=declaration_allowed,
        )

    def _step_payloads(self) -> list[OnboardingStepResponse]:
        onboarding_state = self._state()
        current_step = self._current_step(onboarding_state)
        step_ids = [step.step_id for step in CANONICAL_ONBOARDING_STEPS]
        current_index = step_ids.index(current_step.step_id)
        return [
            OnboardingStepResponse(
                step_id=step.step_id,
                label=step.label,
                lifecycle_state=step.lifecycle_state,
                phase=step.phase,
                complete=step_ids.index(step.step_id) < current_index,
                current=step.step_id == current_step.step_id,
            )
            for step in CANONICAL_ONBOARDING_STEPS
        ]

    def status_payload(self) -> NodeStatusResponse:
        onboarding_state = self._state()
        current_step = self._current_step(onboarding_state)
        trust_state = self._trust_state(onboarding_state)
        blockers = self._blocking_reasons(current_step.step_id)
        return NodeStatusResponse(
            node_name=self._node_name(onboarding_state),
            node_type=self._settings.node_type,
            node_id=self._node_id(onboarding_state),
            lifecycle_state=current_step.lifecycle_state,
            current_step_id=current_step.step_id,
            current_step_label=current_step.label,
            trust_state=trust_state,
            capability_status=onboarding_state.capability_declaration.capability_status,
            governance_sync_status=onboarding_state.governance_sync.governance_sync_status,
            active_governance_version=onboarding_state.operational_status.active_governance_version,
            governance_freshness_state=onboarding_state.operational_status.governance_freshness_state,
            operational_ready=onboarding_state.operational_status.operational_ready,
            blocking_reasons=blockers,
        )

    def onboarding_payload(self) -> OnboardingStatusResponse:
        persisted_state = self._state()
        current_step = self._current_step(persisted_state)
        onboarding_state_label, next_action = self._onboarding_state_label(current_step.step_id)
        return OnboardingStatusResponse(
            onboarding_state=onboarding_state_label,
            lifecycle_state=current_step.lifecycle_state,
            trust_state=self._trust_state(persisted_state),
            current_step_id=current_step.step_id,
            current_step_label=current_step.label,
            next_action=next_action,
            session_id=persisted_state.onboarding_session.session_id,
            approval_url=persisted_state.onboarding_session.approval_url,
            expires_at=persisted_state.onboarding_session.expires_at,
            finalize_url=persisted_state.onboarding_session.finalize_url,
            session_state=persisted_state.onboarding_session.session_state,
            last_polled_at=persisted_state.onboarding_session.last_polled_at,
            last_terminal_outcome=persisted_state.onboarding_session.last_terminal_outcome,
            support_state=persisted_state.trust_activation.support_state,
            trust_last_checked_at=persisted_state.trust_activation.trust_last_checked_at,
            trust_message=persisted_state.trust_activation.support_message,
            capability_status=persisted_state.capability_declaration.capability_status,
            governance_sync_status=persisted_state.governance_sync.governance_sync_status,
            operational_ready=persisted_state.operational_status.operational_ready,
            active_governance_version=persisted_state.operational_status.active_governance_version,
            governance_freshness_state=persisted_state.operational_status.governance_freshness_state,
            capability_setup=self._capability_setup_payload(persisted_state),
            last_error=persisted_state.onboarding_session.last_error,
            steps=self._step_payloads(),
        )

    def capabilities_payload(self) -> CapabilitySummaryResponse:
        return capability_summary(self._state())

    def readiness_payload(self) -> GovernanceReadinessResponse:
        return GovernanceReadinessResponse(
            operational_ready=self._state().operational_status.operational_ready,
            degraded=bool(self._state().operational_status.governance_outdated),
            blocking_reasons=self._blocking_reasons(self._current_step(self._state()).step_id),
        )

    def service_status_payload(self) -> ServiceStatusResponse:
        openwakeword_state = self._openwakeword_state()
        piper_tts_state = self._piper_tts_state() if self._piper_tts_enabled() else "disabled"
        stt_state = self._external_stt_state() if self._external_stt_enabled() else self._settings.voice_stt_provider
        backend_usage = self._resource_usage_payload()
        piper_usage = (
            self._docker_resource_usage(self._settings.piper_tts_container_name)
            if self._piper_tts_enabled()
            else {}
        )
        stt_usage = (
            self._systemd_service_resource_usage(self._settings.voice_stt_service_name)
            if self._external_stt_enabled()
            else backend_usage
        )
        return ServiceStatusResponse(
            backend="running",
            frontend="defined",
            scheduler="not_started",
            openwakeword=openwakeword_state,
            piper_tts=piper_tts_state,
            components=[
                {
                    "component_id": "backend",
                    "label": "Backend",
                    "status": "running",
                    "healthy": True,
                    "restart_target": "backend",
                    "restart_supported": True,
                    "restart_detail": "Backend restart is queued through the user systemd service.",
                    "resource_usage": backend_usage,
                },
                {
                    "component_id": "stt",
                    "label": "STT Engine",
                    "status": stt_state,
                    "healthy": stt_state in {"active", "running"} if self._external_stt_enabled() else True,
                    "provider": self._settings.voice_stt_provider,
                    "model": self._stt_component_model(),
                    "restart_target": self._settings.voice_stt_service_id if self._external_stt_enabled() else "stt",
                    "restart_supported": self._external_stt_enabled(),
                    "restart_detail": "External faster-whisper STT is supervisor-proxied."
                    if self._external_stt_enabled()
                    else "STT currently runs in the backend process.",
                    "resource_scope": "systemd_user_service" if self._external_stt_enabled() else "backend_process",
                    "resource_usage": stt_usage,
                },
                {
                    "component_id": "tts",
                    "label": "TTS Engine",
                    "status": piper_tts_state if self._piper_tts_enabled() else self._settings.voice_tts_provider,
                    "healthy": piper_tts_state == "running" if self._piper_tts_enabled() else True,
                    "provider": self._settings.voice_tts_provider,
                    "model": self._tts_component_model(),
                    "model_display_name": self._tts_component_model_display_name(),
                    "restart_target": self._settings.piper_tts_service_id if self._piper_tts_enabled() else "tts",
                    "restart_supported": self._piper_tts_enabled(),
                    "restart_detail": "Piper TTS is supervisor-proxied."
                    if self._piper_tts_enabled()
                    else "TTS currently runs in the backend process.",
                    "resource_scope": "docker_container" if self._piper_tts_enabled() else "backend_process",
                    "resource_usage": piper_usage if self._piper_tts_enabled() else backend_usage,
                },
            ],
            resource_usage=backend_usage,
            supervisor={
                "configured": self._supervisor_client is not None,
                "registered": self._supervisor_registered,
                "last_seen_at": self._supervisor_last_seen,
                "last_error": self._supervisor_last_error,
            },
        )

    def _tts_component_model(self) -> str:
        if self._settings.voice_tts_provider == "deterministic":
            return "deterministic"
        if not self._piper_tts_enabled():
            return self._settings.voice_tts_model

        candidates: list[str | None] = [
            self._settings.voice_tts_piper_voice,
            *self._settings.resolved_voice_tts_endpoint_voices().values(),
            *self._settings.resolved_piper_tts_warm_voices(),
        ]
        for candidate in candidates:
            model = str(candidate or "").strip()
            if model:
                return model
        return "piper-default"

    def _tts_component_model_display_name(self) -> str:
        model = self._tts_component_model()
        if not self._piper_tts_enabled():
            return model
        config = read_piper_model_config(self._settings.resolved_piper_tts_model_dir() / f"{model}.onnx")
        return piper_model_display_name(config, fallback=model)

    def _stt_component_model(self) -> str:
        if self._settings.voice_stt_provider == "deterministic":
            return "deterministic"
        if self._settings.voice_stt_provider in {"faster_whisper", "external_faster_whisper"}:
            return self._settings.voice_stt_faster_whisper_model
        return self._settings.voice_stt_model

    def _read_proc_status_value_kb(self, key: str) -> int | None:
        try:
            with Path("/proc/self/status").open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith(f"{key}:"):
                        parts = line.split()
                        return int(parts[1]) if len(parts) >= 2 else None
        except (OSError, ValueError):
            return None
        return None

    def _read_meminfo_kb(self) -> dict[str, int]:
        values: dict[str, int] = {}
        try:
            with Path("/proc/meminfo").open("r", encoding="utf-8") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    parts = rest.split()
                    if parts:
                        values[key] = int(parts[0])
        except (OSError, ValueError):
            return {}
        return values

    def _docker_resource_usage(self, container_name: str) -> dict[str, object]:
        result = self._service_command_runner(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                container_name,
            ]
        )
        if result.returncode != 0:
            return {"available": False, "error": (result.stderr or "").strip() or "docker_stats_failed"}
        try:
            payload = json.loads((result.stdout or "").strip())
        except ValueError:
            return {"available": False, "error": "invalid_docker_stats_payload"}
        process = self._docker_container_process_payload(container_name)
        return {
            "available": True,
            "sampled_at": datetime.now(UTC).isoformat(),
            "pid": process.get("pid"),
            "main_pid": process.get("main_pid"),
            "cpu_percent": self._parse_percent(payload.get("CPUPerc")),
            "memory_percent": self._parse_percent(payload.get("MemPerc")),
            "memory_usage": payload.get("MemUsage"),
            "network_io": payload.get("NetIO"),
            "block_io": payload.get("BlockIO"),
            "pids": payload.get("PIDs"),
            "process": process,
        }

    def _systemd_service_resource_usage(self, service_name: str) -> dict[str, object]:
        return self._systemd_service_process_payload(service_name)

    def _current_process_payload(self, *, kind: str = "backend_process") -> dict[str, object]:
        payload = self._process_resource_usage_payload(os.getpid())
        payload.update(
            {
                "kind": kind,
                "pid": os.getpid(),
                "main_pid": os.getpid(),
            }
        )
        return payload

    def _docker_container_process_payload(self, container_name: str) -> dict[str, object]:
        result = self._service_command_runner(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .State}}",
                container_name,
            ]
        )
        if result.returncode != 0:
            return {
                "available": False,
                "kind": "docker_container",
                "container_name": container_name,
                "error": (result.stderr or "").strip() or "docker_inspect_failed",
            }
        try:
            payload = json.loads((result.stdout or "").strip())
        except ValueError:
            return {
                "available": False,
                "kind": "docker_container",
                "container_name": container_name,
                "error": "invalid_docker_inspect_payload",
            }
        try:
            pid = int(payload.get("Pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        status = str(payload.get("Status") or "").strip().lower() or None
        process: dict[str, object] = {
            "available": pid > 0,
            "kind": "docker_container",
            "container_name": container_name,
            "status": status,
            "pid": pid,
            "main_pid": pid,
        }
        if pid > 0:
            usage = self._process_resource_usage_payload(pid)
            process.update(
                {
                    key: value
                    for key, value in usage.items()
                    if key not in {"available", "error", "pid"}
                }
            )
            process.update(
                {
                    "available": True,
                    "kind": "docker_container",
                    "container_name": container_name,
                    "status": status,
                    "pid": pid,
                    "main_pid": pid,
                    "resource_available": usage.get("available"),
                }
            )
            if usage.get("error"):
                process["resource_error"] = usage["error"]
        else:
            process["error"] = "container_not_running"
        return process

    def _systemd_service_process_payload(self, service_name: str) -> dict[str, object]:
        result = self._service_command_runner(
            [
                "systemctl",
                "--user",
                "show",
                service_name,
                "--property=MainPID",
                "--value",
            ]
        )
        if result.returncode != 0:
            return {"available": False, "error": (result.stderr or "").strip() or "systemctl_show_failed"}
        try:
            pid = int((result.stdout or "").strip() or "0")
        except ValueError:
            pid = 0
        if pid <= 0:
            return {
                "available": False,
                "kind": "systemd_user_service",
                "systemd_service": service_name,
                "error": "service_not_running",
                "pid": pid,
                "main_pid": pid,
                "child_pids": [],
            }
        child_pids = self._child_pids(pid)
        monitor_pid = child_pids[0] if child_pids else pid
        process = self._process_resource_usage_payload(monitor_pid)
        process.update(
            {
                "kind": "systemd_user_service",
                "systemd_service": service_name,
                "pid": monitor_pid,
                "main_pid": pid,
                "child_pids": child_pids,
            }
        )
        return process

    def _child_pids(self, pid: int) -> list[int]:
        children_path = Path(f"/proc/{pid}/task/{pid}/children")
        try:
            return [
                int(value)
                for value in children_path.read_text(encoding="utf-8").split()
                if value.isdigit()
            ]
        except (OSError, ValueError):
            return []

    def _service_process_fields(self, process: dict[str, object]) -> dict[str, object]:
        fields: dict[str, object] = {"process": process}
        for key in ("pid", "main_pid", "child_pids", "pids"):
            value = process.get(key)
            if value is not None:
                fields[key] = value
        return fields

    def _process_resource_usage_payload(self, pid: int) -> dict[str, object]:
        status_path = Path(f"/proc/{pid}/status")
        stat_path = Path(f"/proc/{pid}/stat")
        rss_kb: int | None = None
        try:
            with status_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        rss_kb = int(parts[1]) if len(parts) >= 2 else None
                        break
        except (OSError, ValueError):
            return {"available": False, "error": "proc_status_unavailable", "pid": pid}

        cpu_seconds: float | None = None
        try:
            stat_parts = stat_path.read_text(encoding="utf-8").split()
            clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
            cpu_seconds = round((int(stat_parts[13]) + int(stat_parts[14])) / clock_ticks, 2)
        except (OSError, ValueError, IndexError, KeyError):
            cpu_seconds = None

        meminfo = self._read_meminfo_kb()
        total_kb = meminfo.get("MemTotal")
        rss_bytes = rss_kb * 1024 if rss_kb is not None else None
        process_memory_percent = (
            round((rss_kb / total_kb) * 100.0, 2) if rss_kb is not None and total_kb else None
        )
        return {
            "available": True,
            "sampled_at": datetime.now(UTC).isoformat(),
            "pid": pid,
            "process_cpu_seconds": cpu_seconds,
            "process_memory_rss_bytes": rss_bytes,
            "process_memory_percent": process_memory_percent,
        }

    def _parse_percent(self, value: object) -> float | None:
        try:
            return round(float(str(value).strip().rstrip("%")), 2)
        except (TypeError, ValueError):
            return None

    def _resource_usage_payload(self) -> dict[str, object]:
        pid = os.getpid()
        cpu_count = os.cpu_count() or 1
        uptime_s = max(time.monotonic() - self._started_at_monotonic, 1.0)
        process_cpu_percent = min(100.0, (time.process_time() / uptime_s) * 100.0 / cpu_count)
        rss_kb = self._read_proc_status_value_kb("VmRSS")
        meminfo = self._read_meminfo_kb()
        total_kb = meminfo.get("MemTotal")
        available_kb = meminfo.get("MemAvailable")
        rss_bytes = rss_kb * 1024 if rss_kb is not None else None
        total_bytes = total_kb * 1024 if total_kb is not None else None
        available_bytes = available_kb * 1024 if available_kb is not None else None
        process_memory_percent = (
            round((rss_kb / total_kb) * 100.0, 2) if rss_kb is not None and total_kb else None
        )
        system_memory_percent = (
            round(((total_kb - available_kb) / total_kb) * 100.0, 2)
            if total_kb and available_kb is not None
            else None
        )
        try:
            load_1m, load_5m, load_15m = os.getloadavg()
        except OSError:
            load_1m = load_5m = load_15m = None

        return {
            "sampled_at": datetime.now(UTC).isoformat(),
            "pid": pid,
            "main_pid": pid,
            "process_cpu_percent": round(process_cpu_percent, 2),
            "system_load_1m": round(load_1m, 2) if load_1m is not None else None,
            "system_load_5m": round(load_5m, 2) if load_5m is not None else None,
            "system_load_15m": round(load_15m, 2) if load_15m is not None else None,
            "system_cpu_count": cpu_count,
            "process_memory_rss_bytes": rss_bytes,
            "process_memory_percent": process_memory_percent,
            "system_memory_total_bytes": total_bytes,
            "system_memory_available_bytes": available_bytes,
            "system_memory_percent": system_memory_percent,
        }

    def _run_service_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, check=False, text=True, timeout=10)

    def _backend_service_name(self) -> str:
        return os.getenv("BACKEND_SERVICE_NAME", "hexevoice-backend.service")

    def _openwakeword_control_script(self) -> Path:
        return self._resolve_control_script(self._settings.openwakeword_control_script)

    def _piper_tts_control_script(self) -> Path:
        return self._resolve_control_script(self._settings.piper_tts_control_script)

    def _stt_control_script(self) -> Path:
        return self._resolve_control_script(self._settings.voice_stt_control_script)

    def _resolve_control_script(self, script: Path) -> Path:
        if script.is_absolute():
            return script
        return Path.cwd() / script

    def _openwakeword_state(self) -> str:
        return self._docker_container_state(
            container_name=self._settings.openwakeword_container_name,
            service_label="openWakeWord",
        )

    def _docker_container_state(self, *, container_name: str, service_label: str) -> str:
        result = self._service_command_runner(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}}",
                container_name,
            ]
        )
        if result.returncode != 0:
            log.debug(
                "%s container inspect failed: container=%s stderr=%s",
                service_label,
                container_name,
                (result.stderr or "").strip(),
            )
            return "not_created"
        state = (result.stdout or "").strip().lower()
        return state or "unknown"

    def _openwakeword_service_summary(self) -> dict[str, object]:
        state = self._openwakeword_state()
        resource_usage = self._docker_resource_usage(self._settings.openwakeword_container_name)
        process = resource_usage.get("process")
        if not isinstance(process, dict):
            process = self._docker_container_process_payload(self._settings.openwakeword_container_name)
        return {
            "service_id": self._settings.openwakeword_service_id,
            "service_name": "openWakeWord",
            "state": state,
            "boot_order": 15,
            "managed_by": "core_supervisor_service_action_proxy",
            "container_name": self._settings.openwakeword_container_name,
            "control_script": str(self._settings.openwakeword_control_script),
            "resource_usage": resource_usage,
            **self._service_process_fields(process),
        }

    def _piper_tts_enabled(self) -> bool:
        return self._settings.voice_tts_provider == "piper"

    def _external_stt_enabled(self) -> bool:
        return self._settings.voice_stt_provider == "external_faster_whisper"

    def _piper_tts_state(self) -> str:
        return self._docker_container_state(
            container_name=self._settings.piper_tts_container_name,
            service_label="Piper TTS",
        )

    def _external_stt_state(self) -> str:
        script = self._stt_control_script()
        if not script.exists():
            return "control_script_missing"
        result = self._service_command_runner([str(script), "status"])
        if result.returncode != 0:
            return "unknown"
        state = (result.stdout or "").strip().splitlines()
        return (state[0].strip().lower() if state else "") or "unknown"

    def _piper_tts_service_summary(self) -> dict[str, object]:
        state = self._piper_tts_state()
        process = self._docker_container_process_payload(self._settings.piper_tts_container_name)
        return {
            "service_id": self._settings.piper_tts_service_id,
            "service_name": "Piper TTS",
            "state": state,
            "boot_order": 18,
            "managed_by": "core_supervisor_service_action_proxy",
            "container_name": self._settings.piper_tts_container_name,
            "control_script": str(self._settings.piper_tts_control_script),
            "base_url": self._settings.resolved_voice_tts_piper_base_url(),
            "synthesize_path": self._settings.voice_tts_piper_synthesize_path,
            "voice": self._settings.voice_tts_piper_voice,
            "warm_voices": self._settings.resolved_piper_tts_warm_voices(),
            **self._service_process_fields(process),
        }

    def _external_stt_service_summary(self) -> dict[str, object]:
        state = self._external_stt_state()
        process = self._systemd_service_process_payload(self._settings.voice_stt_service_name)
        return {
            "service_id": self._settings.voice_stt_service_id,
            "service_name": "faster-whisper STT",
            "state": state,
            "boot_order": 17,
            "managed_by": "core_supervisor_service_action_proxy",
            "systemd_service": self._settings.voice_stt_service_name,
            "systemd_scope": "user",
            "systemd_unit_template": "scripts/systemd/hexevoice-stt.service.in",
            "systemd_env_file": "scripts/stack.env",
            "control_script": str(self._settings.voice_stt_control_script),
            "install_supported": True,
            "install_action": "install",
            "base_url": self._settings.resolved_voice_stt_service_base_url(),
            "model": self._settings.voice_stt_faster_whisper_model,
            "device": self._settings.voice_stt_faster_whisper_device,
            "compute_type": self._settings.voice_stt_faster_whisper_compute_type,
            **self._service_process_fields(process),
        }

    def _stt_engine_service_summary(
        self,
        *,
        runtime_state: str,
        backend_process: dict[str, object],
    ) -> dict[str, object]:
        if self._external_stt_enabled():
            service = self._external_stt_service_summary()
            healthy = service.get("state") in {"active", "running"}
            service.update(
                {
                    "service_id": "stt_engine",
                    "service_name": "STT Engine",
                    "service_role": "stt_engine",
                    "implementation_service_id": self._settings.voice_stt_service_id,
                    "implementation_name": "faster-whisper STT",
                    "implementation": "external_faster_whisper",
                    "provider": self._settings.voice_stt_provider,
                    "control_target": self._settings.voice_stt_service_id,
                    "restart_supported": True,
                    "resource_scope": "systemd_user_service",
                    "implementation_health": {
                        "engine_role": "stt_engine",
                        "active_implementation": "external_faster_whisper",
                        "provider": self._settings.voice_stt_provider,
                        "model": self._settings.voice_stt_faster_whisper_model,
                        "healthy": healthy,
                        "configured": True,
                        "last_error": None if healthy else service.get("state"),
                    },
                }
            )
            return service

        model = self._stt_component_model()
        return {
            "service_id": "stt_engine",
            "service_name": "STT Engine",
            "service_role": "stt_engine",
            "state": runtime_state,
            "boot_order": 17,
            "managed_by": "backend_process",
            "implementation": "backend_process",
            "provider": self._settings.voice_stt_provider,
            "model": model,
            "control_target": "stt",
            "restart_supported": False,
            "resource_scope": "backend_process",
            "implementation_health": {
                "engine_role": "stt_engine",
                "active_implementation": self._settings.voice_stt_provider,
                "provider": self._settings.voice_stt_provider,
                "model": model,
                "healthy": True,
                "configured": True,
                "last_error": None,
            },
            **self._service_process_fields(backend_process),
        }

    def _tts_engine_service_summary(
        self,
        *,
        runtime_state: str,
        backend_process: dict[str, object],
    ) -> dict[str, object]:
        if self._piper_tts_enabled():
            service = self._piper_tts_service_summary()
            healthy = service.get("state") == "running"
            service.update(
                {
                    "service_id": "tts_engine",
                    "service_name": "TTS Engine",
                    "service_role": "tts_engine",
                    "implementation_service_id": self._settings.piper_tts_service_id,
                    "implementation_name": "Piper TTS",
                    "implementation": "piper",
                    "provider": self._settings.voice_tts_provider,
                    "model": self._tts_component_model(),
                    "model_display_name": self._tts_component_model_display_name(),
                    "control_target": self._settings.piper_tts_service_id,
                    "restart_supported": True,
                    "resource_scope": "docker_container",
                    "implementation_health": {
                        "engine_role": "tts_engine",
                        "active_implementation": "piper",
                        "provider": self._settings.voice_tts_provider,
                        "model": self._tts_component_model(),
                        "healthy": healthy,
                        "configured": True,
                        "last_error": None if healthy else service.get("state"),
                    },
                }
            )
            return service

        return {
            "service_id": "tts_engine",
            "service_name": "TTS Engine",
            "service_role": "tts_engine",
            "state": runtime_state,
            "boot_order": 18,
            "managed_by": "backend_process",
            "implementation": "backend_process",
            "provider": self._settings.voice_tts_provider,
            "model": self._tts_component_model(),
            "model_display_name": self._tts_component_model_display_name(),
            "control_target": "tts",
            "restart_supported": False,
            "resource_scope": "backend_process",
            "implementation_health": {
                "engine_role": "tts_engine",
                "active_implementation": self._settings.voice_tts_provider,
                "provider": self._settings.voice_tts_provider,
                "model": self._tts_component_model(),
                "healthy": True,
                "configured": True,
                "last_error": None,
            },
            **self._service_process_fields(backend_process),
        }

    def service_action(self, *, target: str, action: str) -> ServiceActionResponse:
        normalized_target = str(target or "").strip()
        normalized_action = str(action or "").strip().lower()
        if normalized_target in {"tts", "tts_engine"} and self._piper_tts_enabled():
            normalized_target = self._settings.piper_tts_service_id
        if normalized_target in {"stt", "stt_engine"} and self._external_stt_enabled():
            normalized_target = self._settings.voice_stt_service_id
        if normalized_action not in {"install", "start", "stop", "restart"}:
            log.warning(
                "Rejected service action for unsupported action: target=%s action=%s",
                normalized_target,
                normalized_action,
            )
            return ServiceActionResponse(
                target=normalized_target,
                action=normalized_action,
                accepted=False,
                status="unsupported_action",
                detail="Supported actions are install, start, stop, and restart.",
            )
        if normalized_target == "backend":
            if normalized_action != "restart":
                return ServiceActionResponse(
                    target=normalized_target,
                    action=normalized_action,
                    accepted=False,
                    status="unsupported_action",
                    detail="Backend supports restart only.",
                )
            service_name = self._backend_service_name()
            result = self._service_command_runner(
                ["systemctl", "--user", "restart", "--no-block", service_name]
            )
            if result.returncode != 0:
                return ServiceActionResponse(
                    target=normalized_target,
                    action=normalized_action,
                    accepted=False,
                    status="action_failed",
                    detail=(result.stderr or result.stdout or "").strip() or None,
                )
            return ServiceActionResponse(
                target=normalized_target,
                action=normalized_action,
                accepted=True,
                status="restart_scheduled",
                detail=(result.stdout or "").strip() or f"Restart queued for {service_name}.",
            )
        service_scripts = {
            self._settings.openwakeword_service_id: self._openwakeword_control_script,
        }
        service_states = {
            self._settings.openwakeword_service_id: self._openwakeword_state,
        }
        if self._piper_tts_enabled():
            service_scripts[self._settings.piper_tts_service_id] = self._piper_tts_control_script
            service_states[self._settings.piper_tts_service_id] = self._piper_tts_state
        if self._external_stt_enabled():
            service_scripts[self._settings.voice_stt_service_id] = self._stt_control_script
            service_states[self._settings.voice_stt_service_id] = self._external_stt_state
        if normalized_target not in service_scripts:
            log.warning(
                "Rejected service action for unsupported target: target=%s action=%s",
                normalized_target,
                normalized_action,
            )
            return ServiceActionResponse(
                target=normalized_target,
                action=normalized_action,
                accepted=False,
                status="unsupported_service",
                detail="Supported services are backend, openwakeword, piper_tts when enabled, and faster_whisper_stt when enabled.",
            )
        script = service_scripts[normalized_target]()
        if not script.exists():
            log.error("Service control script missing: target=%s path=%s", normalized_target, script)
            return ServiceActionResponse(
                target=normalized_target,
                action=normalized_action,
                accepted=False,
                status="control_script_missing",
                detail=str(script),
            )
        log.info("Running service action: target=%s action=%s script=%s", normalized_target, normalized_action, script)
        result = self._service_command_runner([str(script), normalized_action])
        if result.returncode != 0:
            log.error(
                "Service action failed: target=%s action=%s returncode=%s detail=%s",
                normalized_target,
                normalized_action,
                result.returncode,
                (result.stderr or result.stdout or "").strip(),
            )
            return ServiceActionResponse(
                target=normalized_target,
                action=normalized_action,
                accepted=False,
                status="action_failed",
                detail=(result.stderr or result.stdout or "").strip() or None,
            )
        status = service_states[normalized_target]()
        log.info("Service action completed: target=%s action=%s status=%s", normalized_target, normalized_action, status)
        return ServiceActionResponse(
            target=normalized_target,
            action=normalized_action,
            accepted=True,
            status=status,
            detail=(result.stdout or "").strip() or None,
        )

    def _supervisor_runtime_payload(self) -> dict[str, object]:
        onboarding_state = self._state()
        node_id = onboarding_state.trust_activation.node_id
        if not node_id:
            return {}
        node_name = onboarding_state.pre_trust.node_name or self._settings.node_name or node_id
        host_id = socket.gethostname()
        api_base_url = self._settings.public_api_base_url or f"http://{self._settings.api_host}:{self._settings.api_port}"
        runtime_state = "running" if self.readiness_payload().operational_ready else "unknown"
        backend_process = self._current_process_payload()
        frontend_process = self._systemd_service_process_payload("hexevoice-frontend.service")
        services = [
            {
                "service_id": "backend",
                "service_name": "backend",
                "state": runtime_state,
                "boot_order": 10,
                "managed_by": "core_supervisor_service_action_proxy",
                "systemd_service": self._backend_service_name(),
                "systemd_scope": "user",
                "control_target": "backend",
                "restart_supported": True,
                **self._service_process_fields(backend_process),
            },
            self._openwakeword_service_summary(),
            self._stt_engine_service_summary(
                runtime_state=runtime_state,
                backend_process=backend_process,
            ),
            self._tts_engine_service_summary(
                runtime_state=runtime_state,
                backend_process=backend_process,
            ),
        ]
        services.append(
            {
                "service_id": "frontend",
                "service_name": "frontend",
                "state": runtime_state,
                "boot_order": 20,
                **self._service_process_fields(frontend_process),
            }
        )
        runtime_metadata = {
            "node_software_version": self._settings.node_software_version,
            "boot_order": 30,
            "node_dependencies": ["node_type:ai-node", "mqtt"],
            "services": services,
        }
        return {
            "node_id": node_id,
            "node_name": node_name,
            "node_type": self._settings.node_type,
            "host_id": host_id,
            "hostname": host_id,
            "api_base_url": api_base_url,
            "ui_base_url": self._settings.public_ui_base_url,
            "desired_state": "running",
            "runtime_state": runtime_state,
            "lifecycle_state": runtime_state,
            "health_status": "healthy" if runtime_state == "running" else "unknown",
            "running": runtime_state == "running",
            "resource_usage": backend_process,
            "runtime_metadata": runtime_metadata,
        }

    async def supervisor_heartbeat_once(self) -> dict | None:
        client = self._supervisor_client
        if client is None:
            log.debug("Supervisor heartbeat skipped: supervisor_client_not_configured")
            return {"status": "skipped", "reason": "supervisor_client_not_configured"}
        payload = self._supervisor_runtime_payload()
        node_id = str(payload.get("node_id") or "").strip()
        if not node_id:
            log.debug("Supervisor heartbeat skipped: missing_node_id")
            return {"status": "skipped", "reason": "missing_node_id"}
        health = await asyncio.to_thread(client.health)
        if not isinstance(health, dict):
            self._supervisor_registered = False
            self._supervisor_last_error = "supervisor_unreachable"
            log.warning("Supervisor heartbeat skipped: supervisor_unreachable")
            return {"status": "skipped", "reason": "supervisor_unreachable"}
        status = str(health.get("status") or "").strip().lower()
        ready = health.get("ready")
        if status not in {"ok", "healthy"} or (ready is not None and not bool(ready)):
            self._supervisor_registered = False
            self._supervisor_last_error = "supervisor_not_ready"
            log.warning("Supervisor heartbeat skipped: supervisor_not_ready status=%s ready=%s", status, ready)
            return {"status": "skipped", "reason": "supervisor_not_ready"}
        if not self._supervisor_registered:
            log.info("Registering runtime with supervisor: node_id=%s", node_id)
            registered = await asyncio.to_thread(client.register_runtime, payload)
            if not isinstance(registered, dict):
                self._supervisor_last_error = "supervisor_register_failed"
                log.error("Supervisor runtime registration failed: node_id=%s", node_id)
                return {"status": "error", "reason": "supervisor_register_failed"}
            self._supervisor_registered = True
            log.info("Supervisor runtime registration completed: node_id=%s", node_id)
        heartbeat_payload = {
            "node_id": payload.get("node_id"),
            "host_id": payload.get("host_id"),
            "hostname": payload.get("hostname"),
            "api_base_url": payload.get("api_base_url"),
            "ui_base_url": payload.get("ui_base_url"),
            "runtime_state": payload.get("runtime_state"),
            "lifecycle_state": payload.get("lifecycle_state"),
            "health_status": payload.get("health_status"),
            "running": payload.get("running"),
            "resource_usage": payload.get("resource_usage", {}),
            "runtime_metadata": payload.get("runtime_metadata", {}),
        }
        heartbeat = await asyncio.to_thread(client.heartbeat_runtime, heartbeat_payload)
        if not isinstance(heartbeat, dict):
            self._supervisor_registered = False
            self._supervisor_last_error = "supervisor_heartbeat_failed"
            log.error("Supervisor heartbeat failed: node_id=%s", node_id)
            return {"status": "error", "reason": "supervisor_heartbeat_failed"}
        self._supervisor_last_error = None
        self._supervisor_last_seen = datetime.now(UTC).replace(tzinfo=None).isoformat()
        log.debug("Supervisor heartbeat completed: node_id=%s last_seen_at=%s", node_id, self._supervisor_last_seen)
        return {"status": "ok", "supervisor": {"last_seen_at": self._supervisor_last_seen}}

    def provider_status_payload(self, *, provider_id: str) -> ProviderStatusResponse:
        state = self._state()
        supported_providers = state.provider_setup.supported_providers or [self._settings.provider_id]
        status = "pending_configuration"
        configured = provider_id in state.provider_setup.enabled_providers
        healthy = configured and state.trust_activation.trust_status == "trusted"

        if provider_id not in supported_providers:
            status = "unknown_provider"
            configured = False
            healthy = False
        elif state.trust_activation.trust_status != "trusted":
            status = "blocked_by_trust"
        elif configured:
            status = "ready_for_capability_declaration"

        return ProviderStatusResponse(
            provider_id=provider_id,
            configured=configured,
            healthy=healthy,
            status=status,
        )
