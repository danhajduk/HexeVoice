from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, NamedTuple
from urllib.parse import quote

from hexevoice.config.settings import Settings


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def as_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
        return payload if isinstance(payload, dict) else {}
    return value if isinstance(value, dict) else {}


def tone_for_state(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"ok", "ready", "running", "operational", "trusted", "healthy", "online", "connected", "success"}:
        return "success"
    if normalized in {"warning", "degraded", "stale", "pending", "review_due", "probation"}:
        return "warning"
    if normalized in {"error", "failed", "offline", "untrusted", "blocked", "missing", "not_created", "exited"}:
        return "danger"
    return "neutral"


def provider_state(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"ok", "ready", "running", "healthy", "online", "connected", "success", "fresh"}:
        return "ready"
    if normalized in {"disabled", "off"}:
        return "disabled"
    if normalized in {"warning", "degraded", "stale", "pending", "review_due", "probation", "restart_required"}:
        return "degraded"
    if normalized in {"missing", "not_created", "exited", "offline", "unavailable", "unknown"}:
        return "unavailable"
    if normalized in {"error", "failed", "untrusted", "blocked"}:
        return "error"
    return "unknown"


def yes_no(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "unknown" if value is None else str(value)


def text(value: object, fallback: str = "unknown") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def labelize(value: object) -> str:
    normalized = text(value, "").replace("_", " ").replace("-", " ").strip()
    if not normalized:
        return "Unknown"
    return " ".join(part.capitalize() for part in normalized.split())


def base_card(
    kind: str,
    *,
    empty: bool = False,
    stale: bool = False,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "updated_at": utc_now(),
        "stale": stale,
        "empty": empty,
        "errors": errors or [],
    }


NEAR_LIVE_10S = {"mode": "near_live", "interval_ms": 10000}
NEAR_LIVE_15S = {"mode": "near_live", "interval_ms": 15000}
NEAR_LIVE_30S = {"mode": "near_live", "interval_ms": 30000}
MANUAL_REFRESH = {"mode": "manual"}


def page_cache_ttl_seconds(refresh: dict[str, Any]) -> float:
    mode = str(refresh.get("mode") or "").strip().lower()
    if mode not in {"live", "near_live"}:
        return 0
    interval_ms = refresh.get("interval_ms")
    try:
        interval = float(interval_ms)
    except (TypeError, ValueError):
        return 0
    return max(interval / 1000, 0)


class PageSnapshotCache:
    def __init__(
        self,
        cache_dir: Path | None = None,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._cache_dir = cache_dir
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}
        self._invalidated_at: dict[str, float] = {}
        self._all_invalidated_at = 0.0
        self._refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._registered_pages: dict[str, RegisteredPageSnapshot] = {}

    def snapshot_path(self, key: str) -> Path | None:
        if self._cache_dir is None:
            return None
        safe_key = "".join(char if char.isalnum() or char in "._-" else "_" for char in key).strip("._")
        return self._cache_dir / f"{safe_key or 'page'}.json"

    def _read_disk(self, key: str) -> dict[str, Any] | None:
        path = self.snapshot_path(key)
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _snapshot_mtime(self, key: str) -> float | None:
        path = self.snapshot_path(key)
        if path is None:
            return None
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def _write_disk(self, key: str, payload: dict[str, Any]) -> None:
        path = self.snapshot_path(key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(f"{path.suffix}.tmp")
            tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp_path, path)
        except OSError:
            return

    async def get_or_build(
        self,
        key: str,
        refresh: dict[str, Any],
        builder: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        disk_payload = self._read_disk(key)
        if disk_payload is not None:
            mtime = self._snapshot_mtime(key)
            invalidated_at = max(self._all_invalidated_at, self._invalidated_at.get(key, 0.0))
            if mtime is None or mtime > invalidated_at:
                self.refresh_if_due(key, refresh, builder)
                return copy.deepcopy(disk_payload)

            payload = await self.refresh_now(key, refresh, builder)
            return payload

        payload = await self.refresh_now(key, refresh, builder)
        return payload

    async def refresh_now(
        self,
        key: str,
        refresh: dict[str, Any],
        builder: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        ttl = page_cache_ttl_seconds(refresh)
        payload = await builder()
        if ttl > 0:
            self._entries[key] = (self._clock() + ttl, copy.deepcopy(payload))
        self._write_disk(key, payload)
        return copy.deepcopy(payload)

    def refresh_if_due(
        self,
        key: str,
        refresh_policy: dict[str, Any],
        builder: Callable[[], Awaitable[dict[str, Any]]],
        *,
        interval_seconds: float | None = None,
    ) -> None:
        if not self.needs_refresh(key, refresh_policy, interval_seconds=interval_seconds):
            return
        existing = self._refresh_tasks.get(key)
        if existing is not None and not existing.done():
            return

        async def refresh_task() -> None:
            try:
                await self.refresh_now(key, refresh_policy, builder)
            finally:
                task = self._refresh_tasks.get(key)
                if task is asyncio.current_task():
                    self._refresh_tasks.pop(key, None)

        try:
            self._refresh_tasks[key] = asyncio.create_task(refresh_task())
        except RuntimeError:
            return

    def needs_refresh(
        self,
        key: str,
        refresh: dict[str, Any],
        *,
        interval_seconds: float | None = None,
    ) -> bool:
        mtime = self._snapshot_mtime(key)
        if mtime is None:
            return True
        invalidated_at = max(self._all_invalidated_at, self._invalidated_at.get(key, 0.0))
        if mtime <= invalidated_at:
            return True
        interval = interval_seconds if interval_seconds is not None else page_cache_ttl_seconds(refresh)
        if interval <= 0:
            return False
        return mtime + interval <= self._wall_clock()

    def register_page(
        self,
        key: str,
        refresh: dict[str, Any],
        builder: Callable[[], Awaitable[dict[str, Any]]],
        *,
        interval_seconds: float | None = None,
    ) -> None:
        interval = interval_seconds if interval_seconds is not None else page_cache_ttl_seconds(refresh)
        self._registered_pages[key] = RegisteredPageSnapshot(
            key=key,
            refresh=refresh,
            builder=builder,
            interval_seconds=interval,
        )

    async def maintain_registered_pages(self, *, poll_interval_seconds: float = 1.0) -> None:
        while True:
            for page in self._registered_pages.values():
                self.refresh_if_due(
                    page.key,
                    page.refresh,
                    page.builder,
                    interval_seconds=page.interval_seconds,
                )
            await asyncio.sleep(max(poll_interval_seconds, 0.1))

    def invalidate(self, key: str | None = None) -> None:
        now = self._wall_clock()
        if key is None:
            self._entries.clear()
            for task in self._refresh_tasks.values():
                task.cancel()
            self._refresh_tasks.clear()
            self._all_invalidated_at = now
            for page in self._registered_pages.values():
                self.refresh_if_due(
                    page.key,
                    page.refresh,
                    page.builder,
                    interval_seconds=page.interval_seconds,
                )
            return
        self._entries.pop(key, None)
        task = self._refresh_tasks.pop(key, None)
        if task is not None:
            task.cancel()
        self._invalidated_at[key] = now
        page = self._registered_pages.get(key)
        if page is not None:
            self.refresh_if_due(
                page.key,
                page.refresh,
                page.builder,
                interval_seconds=page.interval_seconds,
            )


class RegisteredPageSnapshot(NamedTuple):
    key: str
    refresh: dict[str, Any]
    builder: Callable[[], Awaitable[dict[str, Any]]]
    interval_seconds: float


def page_card(
    card_id: str,
    title: str,
    data: dict[str, Any],
    *,
    description: str | None = None,
    detail_endpoint_template: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    refresh: dict[str, Any] | None = None,
) -> dict[str, Any]:
    card = {
        "id": card_id,
        "kind": text(data.get("kind"), "unknown"),
        "title": title,
        "data": data,
    }
    if description:
        card["description"] = description
    if detail_endpoint_template:
        card["detail_endpoint_template"] = detail_endpoint_template
    if actions:
        card["actions"] = actions
    if refresh:
        card["refresh"] = refresh
    return card


def page_snapshot(
    page_id: str,
    refresh: dict[str, Any],
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "updated_at": utc_now(),
        "refresh": refresh,
        "cards": cards,
    }


def refresh_runtime_action() -> dict[str, Any]:
    return {
        "id": "refresh_runtime",
        "label": "Refresh Runtime",
        "method": "POST",
        "endpoint": "/api/node/ui/actions/refresh-status",
    }


def provider_setup_action_id(provider_id: object) -> str:
    return f"configure_provider_setup.{text(provider_id)}"


def provider_setup_action_definition(provider_id: object, *, label: str | None = None) -> dict[str, Any]:
    normalized_provider_id = text(provider_id)
    return {
        "id": provider_setup_action_id(normalized_provider_id),
        "label": label or "Save Setup",
        "method": "PUT",
        "endpoint": f"/api/node/ui/providers/{quote(normalized_provider_id, safe='')}/setup",
    }


def provider_setup_action_definitions(provider_card: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    providers = provider_card.get("providers") if isinstance(provider_card.get("providers"), list) else []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        setup = provider.get("setup") if isinstance(provider.get("setup"), dict) else {}
        for action_state in setup.get("actions") or []:
            if not isinstance(action_state, dict):
                continue
            action_id = text(action_state.get("id"), "")
            prefix = "configure_provider_setup."
            if not action_id.startswith(prefix) or action_id in seen:
                continue
            provider_id = action_id.removeprefix(prefix)
            actions.append(
                provider_setup_action_definition(
                    provider_id,
                    label=text(action_state.get("label"), "Save Setup"),
                )
            )
            seen.add(action_id)
    return actions


def service_control_action_id(target: object, action: str) -> str:
    return f"runtime_service.{text(target)}.{action}"


def service_control_action(target: object, action: str, *, label: str | None = None) -> dict[str, Any]:
    normalized_action = str(action or "").strip().lower()
    encoded_target = quote(text(target), safe="")
    payload: dict[str, Any] = {
        "id": service_control_action_id(target, normalized_action),
        "label": label or normalized_action.title(),
        "method": "POST",
        "endpoint": f"/api/node/ui/runtime/services/{encoded_target}/{normalized_action}",
    }
    if normalized_action == "stop":
        payload["destructive"] = True
        payload["confirmation"] = {"required": True, "message": f"Stop {text(target)}?"}
    elif normalized_action == "restart":
        payload["confirmation"] = {"required": True, "message": f"Restart {text(target)}?"}
    return payload


def runtime_service_action_definitions(runtime_card: dict[str, Any]) -> list[dict[str, Any]]:
    actions = [refresh_runtime_action()]
    seen = {actions[0]["id"]}
    services = runtime_card.get("services") if isinstance(runtime_card.get("services"), list) else []
    for service in services:
        if not isinstance(service, dict):
            continue
        target = service.get("restart_target")
        if not target:
            continue
        for action_state in service.get("actions") or []:
            if not isinstance(action_state, dict):
                continue
            action_id = text(action_state.get("id"))
            if action_id in seen:
                continue
            action_name = action_id.rsplit(".", 1)[-1]
            actions.append(
                service_control_action(
                    target,
                    action_name,
                    label=text(action_state.get("label"), action_name.title()),
                )
            )
            seen.add(action_id)
    return actions


def cancel_active_session_action() -> dict[str, Any]:
    return {
        "id": "cancel_active_session",
        "label": "Cancel Active Session",
        "method": "POST",
        "endpoint": "/api/voice/session/cancel",
        "confirmation": {"required": True, "message": "Cancel the active voice session?"},
    }


def test_assistant_turn_action() -> dict[str, Any]:
    return {
        "id": "test_assistant_turn",
        "label": "Test Assistant Turn",
        "method": "POST",
        "endpoint": "/api/node/ui/actions/test-assistant-turn",
    }


def test_intent_action() -> dict[str, Any]:
    return {
        "id": "test_intent",
        "label": "Test Intent",
        "method": "POST",
        "endpoint": "/api/voice/intents/dispatch",
    }


def invoke_intent_action() -> dict[str, Any]:
    return {
        "id": "invoke_intent",
        "label": "Invoke Intent",
        "method": "POST",
        "endpoint": "/api/voice/intents/invoke",
        "confirmation": {"required": True, "message": "Invoke this intent through the real dispatch path?"},
    }


def manifest(settings: Settings, node_status: dict[str, Any]) -> dict[str, Any]:
    node_id = text(node_status.get("node_id"), settings.node_name)
    display_name = text(node_status.get("node_name"), settings.node_name)
    return {
        "schema_version": "1.0",
        "manifest_revision": "hexevoice-core-rendered-ui-pilot-v3",
        "node_id": node_id,
        "node_type": "voice",
        "display_name": display_name,
        "health": {
            "id": "node.health",
            "kind": "health_strip",
            "title": "Node Health",
            "data_endpoint": "/api/node/ui/overview/health",
            "refresh": NEAR_LIVE_15S,
        },
        "pages": [
            {
                "id": "overview",
                "title": "Overview",
                "page_endpoint": "/api/node/ui/pages/overview",
                "refresh": NEAR_LIVE_15S,
            },
            {
                "id": "runtime",
                "title": "Runtime",
                "page_endpoint": "/api/node/ui/pages/runtime",
                "refresh": NEAR_LIVE_15S,
            },
            {
                "id": "voice.endpoints",
                "title": "Endpoints",
                "page_endpoint": "/api/node/ui/pages/voice/endpoints",
                "refresh": NEAR_LIVE_10S,
            },
            {
                "id": "voice.intents",
                "title": "Intents",
                "page_endpoint": "/api/node/ui/pages/voice/intents",
                "refresh": MANUAL_REFRESH,
            },
            {
                "id": "voice.tts",
                "title": "TTS",
                "page_endpoint": "/api/node/ui/pages/voice/tts",
                "refresh": NEAR_LIVE_30S,
            },
        ],
    }


def overview_node(settings: Settings, node_status: dict[str, Any], onboarding: dict[str, Any]) -> dict[str, Any]:
    card = base_card("node_overview")
    card.update(
        {
            "identity": {
                "node_id": node_status.get("node_id"),
                "node_name": node_status.get("node_name") or settings.node_name,
                "node_type": node_status.get("node_type") or settings.node_type,
                "software_version": settings.node_software_version,
                "local_ui_mode": settings.voice_local_ui_mode,
            },
            "lifecycle": {
                "state": node_status.get("lifecycle_state"),
                "current_step_id": node_status.get("current_step_id"),
                "current_step_label": node_status.get("current_step_label"),
                "operational_ready": bool(node_status.get("operational_ready")),
            },
            "trust": {
                "state": node_status.get("trust_state"),
                "session_state": onboarding.get("session_state"),
                "support_state": onboarding.get("support_state"),
            },
        }
    )
    return card


def overview_health(
    node_status: dict[str, Any],
    readiness: dict[str, Any],
    provider_setup: dict[str, Any],
    services_status: dict[str, Any],
    voice_status: dict[str, Any],
) -> dict[str, Any]:
    pipeline = voice_status.get("turn_pipeline") if isinstance(voice_status.get("turn_pipeline"), dict) else {}
    stt = pipeline.get("stt") if isinstance(pipeline.get("stt"), dict) else {}
    tts = pipeline.get("tts") if isinstance(pipeline.get("tts"), dict) else {}

    def engine_value(status: dict[str, Any]) -> str:
        return text(status.get("implementation") or status.get("provider") or status.get("model"), "unknown")

    def engine_tone(status: dict[str, Any]) -> str:
        if status.get("configured") is False:
            return "warning"
        if status.get("healthy") is False:
            return "danger"
        return tone_for_state(status.get("status") or "ready")

    governance_state = text(
        node_status.get("governance_freshness_state") or node_status.get("governance_sync_status"),
        "unknown",
    )
    governance_tone = "info" if governance_state.strip().lower() == "fresh" else tone_for_state(governance_state)

    items = [
        {
            "state_name": "Life cycle",
            "current_state": text(node_status.get("lifecycle_state")),
            "tone": tone_for_state(node_status.get("lifecycle_state")),
        },
        {
            "state_name": "Trust",
            "current_state": text(node_status.get("trust_state")),
            "tone": tone_for_state(node_status.get("trust_state")),
        },
        {
            "state_name": "Governance",
            "current_state": governance_state,
            "tone": governance_tone,
        },
        {
            "state_name": "Providers",
            "current_state": "configured" if provider_setup.get("configured") else "pending",
            "tone": "success" if provider_setup.get("configured") else "warning",
        },
        {
            "state_name": "STT engine",
            "current_state": engine_value(stt),
            "tone": engine_tone(stt),
        },
        {
            "state_name": "TTS engine",
            "current_state": engine_value(tts),
            "tone": engine_tone(tts),
        },
    ]
    card = base_card("health_strip", empty=False)
    card["items"] = items
    return card


def overview_warnings(
    node_status: dict[str, Any],
    onboarding: dict[str, Any],
    readiness: dict[str, Any],
    services_status: dict[str, Any],
    voice_status: dict[str, Any],
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    for reason in node_status.get("blocking_reasons") or readiness.get("blocking_reasons") or []:
        warnings.append({"id": f"blocker.{reason}", "title": "Readiness Blocker", "message": text(reason), "tone": "warning"})
    if onboarding.get("last_error"):
        warnings.append({"id": "onboarding.last_error", "title": "Onboarding", "message": text(onboarding.get("last_error")), "tone": "danger"})
    supervisor = services_status.get("supervisor") if isinstance(services_status.get("supervisor"), dict) else {}
    if supervisor.get("last_error"):
        warnings.append({"id": "supervisor.last_error", "title": "Supervisor", "message": text(supervisor.get("last_error")), "tone": "warning"})
    if voice_status.get("last_error"):
        last_error = voice_status.get("last_error")
        message = text(last_error.get("code") if isinstance(last_error, dict) else last_error)
        warnings.append({"id": "voice.last_error", "title": "Voice Pipeline", "message": message, "tone": "danger"})
    card = base_card("warning_banner", empty=not warnings)
    card["warnings"] = warnings
    return card


def overview_facts(
    node_status: dict[str, Any],
    onboarding: dict[str, Any],
    operational: dict[str, Any],
    provider_setup: dict[str, Any],
    voice_status: dict[str, Any],
) -> dict[str, Any]:
    facts = [
        {"id": "node_id", "label": "Node ID", "value": text(node_status.get("node_id"), "pending")},
        {"id": "capability_status", "label": "Capability", "value": text(node_status.get("capability_status"))},
        {"id": "governance_status", "label": "Governance", "value": text(node_status.get("governance_sync_status"))},
        {"id": "active_governance", "label": "Governance Version", "value": text(node_status.get("active_governance_version"), "none")},
        {"id": "approval", "label": "Approval", "value": text(onboarding.get("session_state"), "none")},
        {"id": "enabled_providers", "label": "Enabled Providers", "value": ", ".join(provider_setup.get("enabled_providers") or []) or "none"},
        {"id": "endpoint", "label": "Connected Endpoint", "value": text(voice_status.get("endpoint_id"), "none")},
        {"id": "updated_at", "label": "Operational Updated", "value": text(operational.get("updated_at"), "unknown")},
    ]
    card = base_card("facts_card")
    card["facts"] = facts
    return card


def runtime_services(services_status: dict[str, Any], voice_status: dict[str, Any]) -> dict[str, Any]:
    components = services_status.get("components") if isinstance(services_status.get("components"), list) else []
    services: list[dict[str, Any]] = []
    pipeline = voice_status.get("turn_pipeline") if isinstance(voice_status.get("turn_pipeline"), dict) else {}
    for component in components:
        if not isinstance(component, dict):
            continue
        restart_target = component.get("restart_target")
        restart_supported = bool(component.get("restart_supported"))
        control_actions: list[dict[str, Any]] = []
        if restart_target and restart_supported:
            supports_full_lifecycle = text(component.get("component_id")) != "backend"
            if supports_full_lifecycle:
                control_actions.extend(
                    [
                        {
                            "id": service_control_action_id(restart_target, "start"),
                            "label": "Start",
                            "enabled": True,
                            "tone": "success",
                        },
                        {
                            "id": service_control_action_id(restart_target, "stop"),
                            "label": "Stop",
                            "enabled": True,
                            "tone": "warning",
                        },
                    ]
                )
            control_actions.append(
                {
                    "id": service_control_action_id(restart_target, "restart"),
                    "label": "Restart",
                    "enabled": True,
                    "tone": "neutral",
                }
            )
        provider_status = pipeline.get(component.get("component_id")) if isinstance(pipeline.get(component.get("component_id")), dict) else {}
        services.append(
            {
                "id": text(component.get("component_id")),
                "label": text(component.get("label"), text(component.get("component_id"))),
                "state": text(provider_status.get("status") or component.get("status")),
                "healthy": bool(component.get("healthy", True)),
                "tone": "success" if component.get("healthy", True) else "danger",
                "restart_supported": restart_supported,
                "restart_target": restart_target,
                "provider": provider_status.get("provider") or component.get("provider"),
                "model": provider_status.get("model") or component.get("model") or component.get("model_display_name"),
                "resource_usage": component.get("resource_usage") if isinstance(component.get("resource_usage"), dict) else {},
                "last_error": provider_status.get("last_error") or component.get("last_error"),
                "actions": control_actions,
            }
        )
    card = base_card("runtime_service", empty=not services)
    card["services"] = services
    card["supervisor"] = services_status.get("supervisor") if isinstance(services_status.get("supervisor"), dict) else {}
    card["actions"] = [{"id": "refresh_runtime", "label": "Refresh Runtime", "enabled": True, "tone": "neutral"}]
    return card


def provider_setup_section(
    provider_id: str,
    provider_name: str | None,
    provider_setup: dict[str, Any],
    *,
    enabled_aliases: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config if isinstance(config, dict) else {}
    supported = [text(item) for item in provider_setup.get("supported_providers") or [] if item]
    enabled = [text(item) for item in provider_setup.get("enabled_providers") or [] if item]
    default_provider = text(provider_setup.get("default_provider"), "")
    candidate_ids = [provider_id, text(provider_name, ""), *(enabled_aliases or [])]
    candidate_set = set(candidate_ids)
    setup_provider_id, saved_config = provider_setup_target(provider_id, provider_name, provider_setup, enabled_aliases=enabled_aliases)
    is_enabled = any(candidate in enabled for candidate in candidate_ids if candidate)
    is_default = default_provider in candidate_set
    setup_action_enabled = setup_provider_id in supported
    action_id = provider_setup_action_id(setup_provider_id)
    provider_label = labelize(setup_provider_id)
    fields = [
        {
            "id": "enabled",
            "label": f"Enable {provider_label}",
            "type": "checkbox",
            "value": is_enabled,
        },
        {
            "id": "default",
            "label": "Use as default provider",
            "type": "checkbox",
            "value": is_default,
        },
    ]
    for field in provider_config_fields(config, saved_config):
        fields.append(field)
    facts = [
        {"id": "provider_id", "label": "Provider ID", "value": text(provider_name or provider_id)},
        {"id": "setup_provider_id", "label": "Setup Provider", "value": setup_provider_id},
        {"id": "enabled", "label": "Enabled", "value": yes_no(is_enabled)},
        {"id": "default", "label": "Default", "value": yes_no(is_default)},
        {"id": "declaration_allowed", "label": "Declaration Allowed", "value": yes_no(provider_setup.get("declaration_allowed"))},
        {"id": "enabled_providers", "label": "Enabled Providers", "value": ", ".join(enabled) or "none"},
        {"id": "supported_providers", "label": "Supported Providers", "value": ", ".join(supported) or "none"},
    ]
    blocking_reasons = [text(item) for item in provider_setup.get("blocking_reasons") or [] if item]
    return {
        "facts": facts,
        "errors": [
            {"code": f"setup.blocking_reason.{index}", "message": reason, "tone": "warning"}
            for index, reason in enumerate(blocking_reasons)
        ],
        "actions": [
            {
                "id": action_id,
                "label": "Save Setup",
                "enabled": setup_action_enabled,
                "disabled_reason": None if setup_action_enabled else f"{setup_provider_id} is not a supported provider.",
                "tone": "success",
            }
        ],
        "form": {
            "title": f"{provider_label} Setup",
            "submit_action_id": action_id,
            "fields": fields,
        },
    }


def provider_setup_target(
    provider_id: str,
    provider_name: str | None,
    provider_setup: dict[str, Any],
    *,
    enabled_aliases: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    supported = [text(item) for item in provider_setup.get("supported_providers") or [] if item]
    candidate_ids = [provider_id, text(provider_name, ""), *(enabled_aliases or [])]
    setup_provider_id = next((candidate for candidate in candidate_ids if candidate in supported), text(provider_name or provider_id))
    provider_configs = provider_setup.get("provider_configs") if isinstance(provider_setup.get("provider_configs"), dict) else {}
    saved_config = provider_configs.get(setup_provider_id) if isinstance(provider_configs.get(setup_provider_id), dict) else {}
    return setup_provider_id, saved_config


def provider_config_fields(config: dict[str, Any], saved_config: dict[str, Any]) -> list[dict[str, Any]]:
    kind = text(config.get("kind"), "")
    if kind == "tts":
        options = config.get("model_options") if isinstance(config.get("model_options"), list) else []
        if not options:
            return []
        default_voice = text(saved_config.get("default_voice") or config.get("default_voice"), "")
        warm_models = saved_config.get("warm_models") if isinstance(saved_config.get("warm_models"), list) else config.get("warm_models")
        return [
            {
                "id": "default_voice",
                "label": "Default Voice",
                "type": "select",
                "value": default_voice,
                "options": options,
                "required": True,
            },
            {
                "id": "warm_models",
                "label": "Warm Loaded Voices",
                "type": "multiselect",
                "value": warm_models if isinstance(warm_models, list) else [],
                "options": options,
            },
        ]
    if kind == "stt":
        options = config.get("model_options") if isinstance(config.get("model_options"), list) else []
        fields: list[dict[str, Any]] = []
        current_model = text(saved_config.get("model") or config.get("model"), "")
        if options:
            fields.append(
                {
                    "id": "model",
                    "label": "Default Model",
                    "type": "select",
                    "value": current_model,
                    "options": options,
                    "required": True,
                }
            )
        else:
            fields.append({"id": "model", "label": "Default Model", "type": "text", "value": current_model})
        fields.append(
            {
                "id": "warm_model",
                "label": "Download and preload default model",
                "type": "checkbox",
                "value": bool(saved_config.get("warm_model", config.get("warm_model"))),
            }
        )
        warm_models = saved_config.get("warm_models") if isinstance(saved_config.get("warm_models"), list) else config.get("warm_models")
        fields.append(
            {
                "id": "warm_models",
                "label": "Additional models to download and preload",
                "type": "multiselect",
                "value": warm_models if isinstance(warm_models, list) else [],
                "options": options,
            }
        )
        device_options = config.get("device_options") if isinstance(config.get("device_options"), list) else []
        fields.append(
            {
                "id": "device",
                "label": "Device",
                "type": "select",
                "value": text(saved_config.get("device") or config.get("device"), "cpu"),
                "options": device_options,
                "required": True,
            }
        )
        compute_options = config.get("compute_type_options") if isinstance(config.get("compute_type_options"), list) else []
        fields.append(
            {
                "id": "compute_type",
                "label": "Compute Type",
                "type": "select",
                "value": text(saved_config.get("compute_type") or config.get("compute_type"), "int8"),
                "options": compute_options,
                "required": True,
            }
        )
        return fields
    if kind == "wake":
        options = config.get("wakeword_options") if isinstance(config.get("wakeword_options"), list) else []
        value = text(saved_config.get("default_wakeword") or config.get("default_wakeword"), "")
        if options:
            return [
                {
                    "id": "default_wakeword",
                    "label": "Default Wake Word",
                    "type": "select",
                    "value": value,
                    "options": options,
                    "required": True,
                },
                {
                    "id": "warm_model",
                    "label": "Warm load wake model",
                    "type": "checkbox",
                    "value": bool(saved_config.get("warm_model", config.get("warm_model"))),
                },
            ]
        return [
            {"id": "default_wakeword", "label": "Default Wake Word", "type": "text", "value": value},
            {
                "id": "warm_model",
                "label": "Warm load wake model",
                "type": "checkbox",
                "value": bool(saved_config.get("warm_model", config.get("warm_model"))),
            },
        ]
    return []


def provider_status_facts(
    provider_id: str,
    provider_name: str | None,
    status: dict[str, Any],
    tts_settings: dict[str, Any],
    setup: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    active_model = text(status.get("model") or (tts_settings.get("provider") if provider_id == "tts" else None), "")
    if provider_id != "stt":
        return [
            {"id": "model", "label": "Model", "value": text(active_model, "unknown")},
            {"id": "last_error", "label": "Last Error", "value": text(status.get("last_error") or status.get("error"), "clear")},
        ]

    _, saved_config = provider_setup_target(provider_id, provider_name, setup)
    configured_model = text(saved_config.get("model") or config.get("model") or active_model, "")
    facts = [
        {"id": "model", "label": "Model", "value": text(configured_model or active_model, "unknown")},
        {"id": "last_error", "label": "Last Error", "value": text(status.get("last_error") or status.get("error"), "clear")},
    ]
    if configured_model and active_model and configured_model != active_model:
        facts.extend(
            [
                {"id": "active_model", "label": "Active Model", "value": active_model, "tone": "warning"},
                {"id": "restart_required", "label": "Restart Required", "value": "yes", "tone": "warning"},
            ]
        )
    return facts


def provider_display_state(status: dict[str, Any]) -> str:
    raw_state = status.get("status")
    if raw_state:
        return provider_state(raw_state)
    if status.get("healthy") is True and status.get("configured", True):
        return "ready"
    if status.get("configured") is False:
        return "disabled"
    return provider_state(raw_state)


def provider_status(
    services_status: dict[str, Any],
    voice_status: dict[str, Any],
    tts_settings: dict[str, Any],
    provider_setup: dict[str, Any] | None = None,
    provider_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pipeline = voice_status.get("turn_pipeline") if isinstance(voice_status.get("turn_pipeline"), dict) else {}
    setup = provider_setup if isinstance(provider_setup, dict) else {}
    config = provider_config if isinstance(provider_config, dict) else {}
    providers = []
    for provider_id, label in (("stt", "STT Provider"), ("tts", "TTS Provider")):
        status = pipeline.get(provider_id) if isinstance(pipeline.get(provider_id), dict) else {}
        provider_name = text(status.get("provider") or tts_settings.get("provider") if provider_id == "tts" else status.get("provider"))
        state = provider_display_state(status)
        providers.append(
            {
                "id": provider_id,
                "label": label,
                "provider": provider_name,
                "state": state,
                "tone": "success" if status.get("healthy") else tone_for_state(state),
                "facts": provider_status_facts(provider_id, provider_name, status, tts_settings, setup, config.get(provider_id) or {}),
                "setup": provider_setup_section(provider_id, provider_name, setup, config=config.get(provider_id)),
            }
        )
    wake_provider = text(voice_status.get("wake_provider", {}).get("provider") if isinstance(voice_status.get("wake_provider"), dict) else None)
    providers.append(
        {
            "id": "wake",
            "label": "Wake Runtime",
            "provider": wake_provider,
            "state": provider_state(services_status.get("openwakeword")),
            "tone": tone_for_state(services_status.get("openwakeword")),
            "facts": [{"id": "piper", "label": "Piper Runtime", "value": text(services_status.get("piper_tts"))}],
            "setup": provider_setup_section("wake", wake_provider, setup, enabled_aliases=["voice"], config=config.get("wake")),
        }
    )
    card = base_card("provider_status", empty=not providers)
    card["providers"] = providers
    return card


def endpoint_records(endpoints: list[dict[str, Any]], voice_status: dict[str, Any]) -> dict[str, Any]:
    records = []
    active_endpoint_id = voice_status.get("endpoint_id")
    for endpoint in endpoints:
        records.append(
            {
                "id": text(endpoint.get("endpoint_id")),
                "endpoint_id": endpoint.get("endpoint_id"),
                "name": endpoint.get("display_name") or endpoint.get("endpoint_id"),
                "status": endpoint.get("connection_state"),
                "device_state": endpoint.get("device_state"),
                "firmware_version": endpoint.get("firmware_version"),
                "last_seen_at": endpoint.get("last_seen_at"),
                "active": endpoint.get("endpoint_id") == active_endpoint_id,
                "tone": tone_for_state(endpoint.get("connection_state")),
                "detail_ref": {"endpoint": f"/api/endpoint/status/{endpoint.get('endpoint_id')}"},
            }
        )
    card = base_card("record_list", empty=not records)
    card["summary"] = {"endpoint_count": len(records), "active_endpoint_id": active_endpoint_id}
    card["columns"] = [
        {"id": "name", "label": "Name"},
        {"id": "status", "label": "Status"},
        {"id": "device_state", "label": "Device"},
        {"id": "firmware_version", "label": "Firmware"},
        {"id": "last_seen_at", "label": "Last Seen"},
    ]
    card["records"] = records
    return card


def endpoint_actions(voice_status: dict[str, Any]) -> dict[str, Any]:
    supported = voice_status.get("supported_actions") if isinstance(voice_status.get("supported_actions"), dict) else {}
    connected = bool(voice_status.get("endpoint_id"))
    card = base_card("action_panel")
    card["groups"] = [
        {
            "id": "session",
            "label": "Session",
            "actions": [
                {
                    "id": "cancel_active_session",
                    "label": "Cancel Active Session",
                    "enabled": bool(supported.get("stop_session")),
                    "tone": "warning",
                    "disabled_reason": None if supported.get("stop_session") else "No active session",
                },
                {
                    "id": "test_assistant_turn",
                    "label": "Test Assistant Turn",
                    "enabled": True,
                    "tone": "neutral",
                },
            ],
        },
        {
            "id": "endpoint",
            "label": "Endpoint",
            "actions": [
                {"id": "mute_endpoint", "label": "Mute Endpoint", "enabled": bool(supported.get("mute_endpoint")), "tone": "neutral"},
                {"id": "replay_response", "label": "Replay Response", "enabled": bool(supported.get("replay_response")), "tone": "neutral"},
                {"id": "send_media", "label": "Send Media", "enabled": connected and bool(supported.get("send_media")), "tone": "neutral"},
            ],
        },
    ]
    return card


def intent_records(snapshot: dict[str, Any]) -> dict[str, Any]:
    intents = snapshot.get("intents") if isinstance(snapshot.get("intents"), list) else []
    records = []
    for intent in intents:
        if not isinstance(intent, dict):
            continue
        definition = intent.get("definition") if isinstance(intent.get("definition"), dict) else {}
        dispatch = definition.get("dispatch") if isinstance(definition.get("dispatch"), dict) else {}
        matcher = definition.get("matcher") if isinstance(definition.get("matcher"), dict) else {}
        records.append(
            {
                "id": intent.get("intent_id"),
                "name": intent.get("intent_name") or intent.get("intent_id"),
                "status": intent.get("status"),
                "owner": intent.get("owner_service") or intent.get("service_id"),
                "dispatch": dispatch.get("command") or dispatch.get("event_type") or dispatch.get("type"),
                "matcher": matcher.get("type"),
                "updated_at": intent.get("updated_at"),
                "tone": tone_for_state(intent.get("status")),
                "detail_ref": {"endpoint": f"/api/voice/intents/{intent.get('intent_id')}"},
            }
        )
    card = base_card("record_list", empty=not records)
    card["summary"] = {
        "registered_count": snapshot.get("registered_count", len(records)),
        "active_count": snapshot.get("active_count"),
        "schema_version": snapshot.get("schema_version"),
    }
    card["columns"] = [
        {"id": "name", "label": "Name"},
        {"id": "status", "label": "Status"},
        {"id": "owner", "label": "Owner"},
        {"id": "dispatch", "label": "Dispatch"},
        {"id": "updated_at", "label": "Updated"},
    ]
    card["records"] = records
    return card


def intent_actions(snapshot: dict[str, Any]) -> dict[str, Any]:
    has_active = bool(snapshot.get("active_count"))
    card = base_card("action_panel")
    card["groups"] = [
        {
            "id": "intent_test",
            "label": "Intent Test",
            "actions": [
                {"id": "test_intent", "label": "Test Intent", "enabled": has_active, "tone": "neutral"},
                {"id": "invoke_intent", "label": "Invoke Intent", "enabled": has_active, "tone": "warning"},
            ],
        }
    ]
    return card


def tts_runtime(tts_settings: dict[str, Any], voice_status: dict[str, Any]) -> dict[str, Any]:
    pipeline = voice_status.get("turn_pipeline") if isinstance(voice_status.get("turn_pipeline"), dict) else {}
    tts = pipeline.get("tts") if isinstance(pipeline.get("tts"), dict) else {}
    card = base_card("provider_status")
    card["providers"] = [
        {
            "id": "tts",
            "label": "TTS Provider",
            "provider": text(tts_settings.get("provider")),
            "state": provider_state("restart_required" if tts_settings.get("restart_required") else tts.get("status")),
            "tone": "warning" if tts_settings.get("restart_required") else ("success" if tts.get("healthy", True) else "danger"),
            "facts": [
                {"id": "models", "label": "Models", "value": str(len(tts_settings.get("models") or []))},
                {"id": "warm_voices", "label": "Warm Voices", "value": ", ".join(tts_settings.get("warm_voices") or []) or "none"},
                {"id": "conversion_rates", "label": "Conversion Rates", "value": ", ".join(str(item) for item in tts_settings.get("conversion_sample_rates_hz") or []) or "none"},
                {"id": "policy", "label": "Conversion Policy", "value": text(tts_settings.get("conversion_policy"))},
                {"id": "restart_required", "label": "Restart Required", "value": yes_no(tts_settings.get("restart_required"))},
            ],
        }
    ]
    return card


def artifact_records(artifacts_payload: dict[str, Any]) -> dict[str, Any]:
    artifacts = artifacts_payload.get("artifacts") if isinstance(artifacts_payload.get("artifacts"), list) else []
    records = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        stream_id = text(artifact.get("stream_id") or artifact.get("artifact_id") or artifact.get("id"))
        records.append(
            {
                "id": stream_id,
                "name": artifact.get("filename") or stream_id,
                "status": text(artifact.get("status"), "available"),
                "stream_id": artifact.get("stream_id"),
                "endpoint_id": artifact.get("endpoint_id"),
                "voice": artifact.get("voice"),
                "created_at": artifact.get("created_at"),
                "duration_ms": artifact.get("duration_ms"),
                "detail_ref": {"endpoint": f"/api/voice/tts/{stream_id}"},
            }
        )
    card = base_card("record_list", empty=not records)
    card["summary"] = {"count": artifacts_payload.get("count", len(artifacts)), "limit": artifacts_payload.get("limit")}
    card["columns"] = [
        {"id": "name", "label": "Artifact"},
        {"id": "status", "label": "Status"},
        {"id": "voice", "label": "Voice"},
        {"id": "created_at", "label": "Created"},
    ]
    card["records"] = records
    return card


def session_records(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    records = []
    for session in sessions:
        records.append(
            {
                "id": session.get("session_id"),
                "session_id": session.get("session_id"),
                "endpoint_id": session.get("endpoint_id"),
                "status": session.get("status") or session.get("session_state"),
                "started_at": session.get("started_at") or session.get("created_at"),
                "completed_at": session.get("completed_at"),
                "detail_ref": {"endpoint": f"/api/voice/sessions/{session.get('session_id')}"},
            }
        )
    card = base_card("record_list", empty=not records)
    card["summary"] = {"session_count": len(records)}
    card["columns"] = [
        {"id": "session_id", "label": "Session"},
        {"id": "endpoint_id", "label": "Endpoint"},
        {"id": "status", "label": "Status"},
        {"id": "started_at", "label": "Started"},
    ]
    card["records"] = records
    return card


def media_records(assets_payload: dict[str, Any], endpoints: list[dict[str, Any]]) -> dict[str, Any]:
    assets = assets_payload.get("assets") if isinstance(assets_payload.get("assets"), list) else []
    records = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = text(asset.get("asset_id") or asset.get("filename") or asset.get("id"))
        records.append(
            {
                "id": f"asset.{asset_id}",
                "name": asset.get("filename") or asset_id,
                "status": text(asset.get("status"), "available"),
                "record_type": "asset",
                "asset_id": asset_id,
                "content_type": asset.get("content_type"),
                "bytes": asset.get("bytes"),
                "detail_ref": {"endpoint": f"/api/endpoint/media/files/{asset_id}"},
            }
        )
    for endpoint in endpoints:
        endpoint_id = text(endpoint.get("endpoint_id"))
        storage = endpoint.get("capabilities", {}).get("storage", {}) if isinstance(endpoint.get("capabilities"), dict) else {}
        records.append(
            {
                "id": f"endpoint.{endpoint_id}",
                "name": endpoint_id,
                "status": endpoint.get("connection_state"),
                "record_type": "endpoint_inventory",
                "endpoint_id": endpoint_id,
                "last_seen_at": endpoint.get("last_seen_at"),
                "storage": storage,
                "tone": tone_for_state(endpoint.get("connection_state")),
            }
        )
    card = base_card("record_list", empty=not records)
    card["summary"] = {"asset_count": len(assets), "endpoint_count": len(endpoints)}
    card["columns"] = [
        {"id": "name", "label": "Name"},
        {"id": "record_type", "label": "Type"},
        {"id": "status", "label": "Status"},
        {"id": "last_seen_at", "label": "Last Seen"},
    ]
    card["records"] = records
    return card
