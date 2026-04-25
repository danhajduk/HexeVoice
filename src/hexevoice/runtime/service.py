from datetime import UTC, datetime
import socket
import asyncio
from collections.abc import Callable
from pathlib import Path
import subprocess

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
from hexevoice.config.settings import Settings
from hexevoice.onboarding import CANONICAL_ONBOARDING_STEPS, initial_onboarding_step
from hexevoice.persistence import OnboardingStateStore
from hexevoice.supervisor.client import SupervisorApiClient


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
        selected_task_families = onboarding_state.capability_declaration.declared_task_families
        available_task_families = ["voice.inference"]
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
        state = self._state()
        return CapabilitySummaryResponse(
            configured=state.provider_setup.enabled_providers,
            declared=state.capability_declaration.declared_capabilities,
            capability_status=state.capability_declaration.capability_status,
            capability_profile_id=state.capability_declaration.capability_profile_id,
            accepted_at=state.capability_declaration.accepted_at,
            governance_version=state.capability_declaration.governance_version,
        )

    def readiness_payload(self) -> GovernanceReadinessResponse:
        return GovernanceReadinessResponse(
            operational_ready=self._state().operational_status.operational_ready,
            degraded=bool(self._state().operational_status.governance_outdated),
            blocking_reasons=self._blocking_reasons(self._current_step(self._state()).step_id),
        )

    def service_status_payload(self) -> ServiceStatusResponse:
        return ServiceStatusResponse(
            backend="defined",
            frontend="defined",
            scheduler="not_started",
            openwakeword=self._openwakeword_state(),
        )

    def _run_service_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, check=False, text=True, timeout=10)

    def _openwakeword_control_script(self) -> Path:
        script = self._settings.openwakeword_control_script
        if script.is_absolute():
            return script
        return Path.cwd() / script

    def _openwakeword_state(self) -> str:
        result = self._service_command_runner(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}}",
                self._settings.openwakeword_container_name,
            ]
        )
        if result.returncode != 0:
            return "not_created"
        state = (result.stdout or "").strip().lower()
        return state or "unknown"

    def _openwakeword_service_summary(self) -> dict[str, object]:
        state = self._openwakeword_state()
        return {
            "service_id": self._settings.openwakeword_service_id,
            "service_name": "openWakeWord",
            "state": state,
            "boot_order": 15,
            "managed_by": "core_supervisor_service_action_proxy",
            "container_name": self._settings.openwakeword_container_name,
            "control_script": str(self._settings.openwakeword_control_script),
        }

    def service_action(self, *, target: str, action: str) -> ServiceActionResponse:
        normalized_target = str(target or "").strip()
        normalized_action = str(action or "").strip().lower()
        if normalized_target != self._settings.openwakeword_service_id:
            return ServiceActionResponse(
                target=normalized_target,
                action=normalized_action,
                accepted=False,
                status="unsupported_service",
                detail="Only the openwakeword service is controlled by this node API.",
            )
        if normalized_action not in {"start", "stop", "restart"}:
            return ServiceActionResponse(
                target=normalized_target,
                action=normalized_action,
                accepted=False,
                status="unsupported_action",
                detail="Supported actions are start, stop, and restart.",
            )
        script = self._openwakeword_control_script()
        if not script.exists():
            return ServiceActionResponse(
                target=normalized_target,
                action=normalized_action,
                accepted=False,
                status="control_script_missing",
                detail=str(script),
            )
        result = self._service_command_runner([str(script), normalized_action])
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
            status=self._openwakeword_state(),
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
        services = [
            {
                "service_id": "backend",
                "service_name": "backend",
                "state": runtime_state,
                "boot_order": 10,
            },
            self._openwakeword_service_summary(),
            {
                "service_id": "frontend",
                "service_name": "frontend",
                "state": runtime_state,
                "boot_order": 20,
            },
        ]
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
            "resource_usage": {},
            "runtime_metadata": runtime_metadata,
        }

    async def supervisor_heartbeat_once(self) -> dict | None:
        client = self._supervisor_client
        if client is None:
            return {"status": "skipped", "reason": "supervisor_client_not_configured"}
        payload = self._supervisor_runtime_payload()
        node_id = str(payload.get("node_id") or "").strip()
        if not node_id:
            return {"status": "skipped", "reason": "missing_node_id"}
        health = await asyncio.to_thread(client.health)
        if not isinstance(health, dict):
            self._supervisor_registered = False
            self._supervisor_last_error = "supervisor_unreachable"
            return {"status": "skipped", "reason": "supervisor_unreachable"}
        status = str(health.get("status") or "").strip().lower()
        ready = health.get("ready")
        if status not in {"ok", "healthy"} or (ready is not None and not bool(ready)):
            self._supervisor_registered = False
            self._supervisor_last_error = "supervisor_not_ready"
            return {"status": "skipped", "reason": "supervisor_not_ready"}
        if not self._supervisor_registered:
            registered = await asyncio.to_thread(client.register_runtime, payload)
            if not isinstance(registered, dict):
                self._supervisor_last_error = "supervisor_register_failed"
                return {"status": "error", "reason": "supervisor_register_failed"}
            self._supervisor_registered = True
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
            return {"status": "error", "reason": "supervisor_heartbeat_failed"}
        self._supervisor_last_error = None
        self._supervisor_last_seen = datetime.now(UTC).replace(tzinfo=None).isoformat()
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
