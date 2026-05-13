from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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


def yes_no(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "unknown" if value is None else str(value)


def text(value: object, fallback: str = "unknown") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def base_card(kind: str, *, empty: bool = False, stale: bool = False, errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "kind": kind,
        "updated_at": utc_now(),
        "stale": stale,
        "empty": empty,
        "errors": errors or [],
    }


def manifest(settings: Settings, node_status: dict[str, Any]) -> dict[str, Any]:
    node_id = text(node_status.get("node_id"), settings.node_name)
    display_name = text(node_status.get("node_name"), settings.node_name)
    return {
        "schema_version": "1.0",
        "manifest_revision": "hexevoice-core-rendered-ui-pilot-v1",
        "node_id": node_id,
        "node_type": "voice",
        "display_name": display_name,
        "pages": [
            {
                "id": "overview",
                "title": "Overview",
                "surfaces": [
                    {
                        "id": "node.overview",
                        "kind": "node_overview",
                        "title": "Node Overview",
                        "data_endpoint": "/api/node/ui/overview/node",
                        "refresh": {"mode": "near_live", "interval_ms": 15000},
                    },
                    {
                        "id": "node.health",
                        "kind": "health_strip",
                        "title": "Node Health",
                        "data_endpoint": "/api/node/ui/overview/health",
                        "refresh": {"mode": "near_live", "interval_ms": 15000},
                    },
                    {
                        "id": "node.warnings",
                        "kind": "warning_banner",
                        "title": "Operational Warnings",
                        "data_endpoint": "/api/node/ui/overview/warnings",
                        "refresh": {"mode": "manual"},
                    },
                    {
                        "id": "node.facts",
                        "kind": "facts_card",
                        "title": "Live Facts",
                        "data_endpoint": "/api/node/ui/overview/facts",
                        "refresh": {"mode": "near_live", "interval_ms": 30000},
                    },
                ],
            },
            {
                "id": "runtime",
                "title": "Runtime",
                "surfaces": [
                    {
                        "id": "runtime.services",
                        "kind": "runtime_service",
                        "title": "Runtime Services",
                        "data_endpoint": "/api/node/ui/runtime/services",
                        "actions": [
                            {
                                "id": "refresh_runtime",
                                "label": "Refresh Runtime",
                                "method": "POST",
                                "endpoint": "/api/node/ui/actions/refresh-status",
                            }
                        ],
                        "refresh": {"mode": "near_live", "interval_ms": 15000},
                    },
                    {
                        "id": "runtime.providers",
                        "kind": "provider_status",
                        "title": "Provider Status",
                        "data_endpoint": "/api/node/ui/providers/status",
                        "refresh": {"mode": "near_live", "interval_ms": 30000},
                    },
                ],
            },
            {
                "id": "voice.endpoints",
                "title": "Endpoints",
                "surfaces": [
                    {
                        "id": "voice.endpoints",
                        "kind": "record_list",
                        "title": "Voice Endpoints",
                        "data_endpoint": "/api/node/ui/voice/endpoints",
                        "detail_endpoint_template": "/api/endpoint/status/{endpoint_id}",
                        "refresh": {"mode": "near_live", "interval_ms": 10000},
                    },
                    {
                        "id": "voice.endpoint_actions",
                        "kind": "action_panel",
                        "title": "Endpoint Actions",
                        "data_endpoint": "/api/node/ui/voice/endpoint-actions",
                        "actions": [
                            {
                                "id": "cancel_active_session",
                                "label": "Cancel Active Session",
                                "method": "POST",
                                "endpoint": "/api/voice/session/cancel",
                                "confirmation": {"required": True, "message": "Cancel the active voice session?"},
                            },
                            {
                                "id": "test_assistant_turn",
                                "label": "Test Assistant Turn",
                                "method": "POST",
                                "endpoint": "/api/node/ui/actions/test-assistant-turn",
                            },
                        ],
                        "refresh": {"mode": "near_live", "interval_ms": 10000},
                    },
                    {
                        "id": "voice.sessions",
                        "kind": "record_list",
                        "title": "Recent Sessions",
                        "data_endpoint": "/api/node/ui/voice/sessions",
                        "detail_endpoint_template": "/api/voice/sessions/{session_id}",
                        "refresh": {"mode": "manual"},
                    },
                ],
            },
            {
                "id": "voice.intents",
                "title": "Intents",
                "surfaces": [
                    {
                        "id": "voice.intent_registry",
                        "kind": "record_list",
                        "title": "Registered Intents",
                        "data_endpoint": "/api/node/ui/voice/intents",
                        "detail_endpoint_template": "/api/voice/intents/{intent_id}",
                        "refresh": {"mode": "manual"},
                    },
                    {
                        "id": "voice.intent_actions",
                        "kind": "action_panel",
                        "title": "Intent Actions",
                        "data_endpoint": "/api/node/ui/voice/intent-actions",
                        "actions": [
                            {
                                "id": "test_intent",
                                "label": "Test Intent",
                                "method": "POST",
                                "endpoint": "/api/voice/intents/dispatch",
                            },
                            {
                                "id": "invoke_intent",
                                "label": "Invoke Intent",
                                "method": "POST",
                                "endpoint": "/api/voice/intents/invoke",
                                "confirmation": {"required": True, "message": "Invoke this intent through the real dispatch path?"},
                            },
                        ],
                        "refresh": {"mode": "manual"},
                    },
                ],
            },
            {
                "id": "voice.tts",
                "title": "TTS",
                "surfaces": [
                    {
                        "id": "voice.tts_runtime",
                        "kind": "provider_status",
                        "title": "TTS Runtime",
                        "data_endpoint": "/api/node/ui/voice/tts",
                        "refresh": {"mode": "near_live", "interval_ms": 30000},
                    },
                    {
                        "id": "voice.tts_artifacts",
                        "kind": "artifact_browser",
                        "title": "Generated TTS Artifacts",
                        "data_endpoint": "/api/node/ui/voice/tts-artifacts",
                        "refresh": {"mode": "manual"},
                    },
                    {
                        "id": "voice.media",
                        "kind": "artifact_browser",
                        "title": "Endpoint Media",
                        "data_endpoint": "/api/node/ui/voice/media",
                        "refresh": {"mode": "manual"},
                    },
                ],
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
    items = [
        {
            "id": "lifecycle",
            "label": "Lifecycle",
            "value": text(node_status.get("lifecycle_state")),
            "tone": tone_for_state(node_status.get("lifecycle_state")),
        },
        {
            "id": "trust",
            "label": "Trust",
            "value": text(node_status.get("trust_state")),
            "tone": tone_for_state(node_status.get("trust_state")),
        },
        {
            "id": "operational",
            "label": "Operational",
            "value": "ready" if readiness.get("operational_ready") else "blocked",
            "tone": "success" if readiness.get("operational_ready") else "warning",
        },
        {
            "id": "providers",
            "label": "Providers",
            "value": "configured" if provider_setup.get("configured") else "pending",
            "tone": "success" if provider_setup.get("configured") else "warning",
        },
        {
            "id": "backend",
            "label": "Backend",
            "value": text(services_status.get("backend")),
            "tone": tone_for_state(services_status.get("backend")),
        },
        {
            "id": "voice_transport",
            "label": "Voice Transport",
            "value": text(voice_status.get("transport_health"), "offline"),
            "tone": tone_for_state(voice_status.get("transport_health")),
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
        warnings.append({"id": f"blocker.{reason}", "label": "Readiness Blocker", "message": text(reason), "tone": "warning"})
    if onboarding.get("last_error"):
        warnings.append({"id": "onboarding.last_error", "label": "Onboarding", "message": text(onboarding.get("last_error")), "tone": "danger"})
    supervisor = services_status.get("supervisor") if isinstance(services_status.get("supervisor"), dict) else {}
    if supervisor.get("last_error"):
        warnings.append({"id": "supervisor.last_error", "label": "Supervisor", "message": text(supervisor.get("last_error")), "tone": "warning"})
    if voice_status.get("last_error"):
        last_error = voice_status.get("last_error")
        message = text(last_error.get("code") if isinstance(last_error, dict) else last_error)
        warnings.append({"id": "voice.last_error", "label": "Voice Pipeline", "message": message, "tone": "danger"})
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
        provider_status = pipeline.get(component.get("component_id")) if isinstance(pipeline.get(component.get("component_id")), dict) else {}
        services.append(
            {
                "id": text(component.get("component_id")),
                "label": text(component.get("label"), text(component.get("component_id"))),
                "state": text(provider_status.get("status") or component.get("status")),
                "healthy": bool(component.get("healthy", True)),
                "tone": "success" if component.get("healthy", True) else "danger",
                "restart_supported": bool(component.get("restart_supported")),
                "restart_target": component.get("restart_target"),
                "provider": provider_status.get("provider") or component.get("provider"),
                "model": provider_status.get("model") or component.get("model") or component.get("model_display_name"),
                "resource_usage": component.get("resource_usage") if isinstance(component.get("resource_usage"), dict) else {},
                "last_error": provider_status.get("last_error") or component.get("last_error"),
            }
        )
    card = base_card("runtime_service", empty=not services)
    card["services"] = services
    card["supervisor"] = services_status.get("supervisor") if isinstance(services_status.get("supervisor"), dict) else {}
    card["actions"] = [{"id": "refresh_runtime", "label": "Refresh Runtime", "enabled": True, "tone": "neutral"}]
    return card


def provider_status(services_status: dict[str, Any], voice_status: dict[str, Any], tts_settings: dict[str, Any]) -> dict[str, Any]:
    pipeline = voice_status.get("turn_pipeline") if isinstance(voice_status.get("turn_pipeline"), dict) else {}
    providers = []
    for provider_id, label in (("stt", "STT Provider"), ("tts", "TTS Provider")):
        status = pipeline.get(provider_id) if isinstance(pipeline.get(provider_id), dict) else {}
        providers.append(
            {
                "id": provider_id,
                "label": label,
                "provider": text(status.get("provider") or tts_settings.get("provider") if provider_id == "tts" else status.get("provider")),
                "state": text(status.get("status"), "unknown"),
                "tone": "success" if status.get("healthy") else tone_for_state(status.get("status")),
                "facts": [
                    {"id": "model", "label": "Model", "value": text(status.get("model") or (tts_settings.get("provider") if provider_id == "tts" else None))},
                    {"id": "last_error", "label": "Last Error", "value": text(status.get("last_error") or status.get("error"), "clear")},
                ],
            }
        )
    providers.append(
        {
            "id": "wake",
            "label": "Wake Runtime",
            "provider": text(voice_status.get("wake_provider", {}).get("provider") if isinstance(voice_status.get("wake_provider"), dict) else None),
            "state": text(services_status.get("openwakeword")),
            "tone": tone_for_state(services_status.get("openwakeword")),
            "facts": [{"id": "piper", "label": "Piper Runtime", "value": text(services_status.get("piper_tts"))}],
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
            "state": "restart_required" if tts_settings.get("restart_required") else text(tts.get("status"), "ready"),
            "tone": "warning" if tts_settings.get("restart_required") else ("success" if tts.get("healthy", True) else "danger"),
            "facts": [
                {"id": "models", "label": "Models", "value": str(len(tts_settings.get("models") or []))},
                {"id": "warm_voices", "label": "Warm Voices", "value": ", ".join(tts_settings.get("warm_voices") or []) or "none"},
                {"id": "conversion_rates", "label": "Conversion Rates", "value": ", ".join(str(item) for item in tts_settings.get("conversion_sample_rates_hz") or []) or "none"},
                {"id": "policy", "label": "Conversion Policy", "value": text(tts_settings.get("conversion_policy"))},
            ],
        }
    ]
    card["models"] = tts_settings.get("models") if isinstance(tts_settings.get("models"), list) else []
    return card


def artifact_records(artifacts_payload: dict[str, Any]) -> dict[str, Any]:
    artifacts = artifacts_payload.get("artifacts") if isinstance(artifacts_payload.get("artifacts"), list) else []
    card = base_card("artifact_browser", empty=not artifacts)
    card["summary"] = {"count": artifacts_payload.get("count", len(artifacts)), "limit": artifacts_payload.get("limit")}
    card["artifacts"] = artifacts
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
    card = base_card("artifact_browser", empty=not assets and not endpoints)
    card["assets"] = assets
    card["endpoint_inventories"] = [
        {
            "endpoint_id": endpoint.get("endpoint_id"),
            "last_seen_at": endpoint.get("last_seen_at"),
            "storage": endpoint.get("capabilities", {}).get("storage", {}) if isinstance(endpoint.get("capabilities"), dict) else {},
        }
        for endpoint in endpoints
    ]
    card["summary"] = {"asset_count": len(assets), "endpoint_count": len(endpoints)}
    return card
