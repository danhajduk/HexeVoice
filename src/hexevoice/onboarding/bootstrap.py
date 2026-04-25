from __future__ import annotations

from datetime import datetime, timezone
from socket import AF_INET, SOCK_STREAM, socket
from urllib.parse import urlparse

from hexevoice.api.models import BootstrapAdvertisementRequest, BootstrapDiscoveryResponse
from hexevoice.config.settings import Settings
from hexevoice.persistence import OnboardingStateStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BootstrapDiscoveryService:
    def __init__(self, *, settings: Settings, onboarding_state_store: OnboardingStateStore) -> None:
        self._settings = settings
        self._store = onboarding_state_store

    def status_payload(self) -> BootstrapDiscoveryResponse:
        state = self._store.load()
        bootstrap = state.bootstrap_discovery
        return BootstrapDiscoveryResponse(
            configured=bootstrap.advertisement_valid,
            bootstrap_topic=bootstrap.bootstrap_topic,
            bootstrap_host=bootstrap.bootstrap_host,
            bootstrap_port=bootstrap.bootstrap_port,
            connection_status=bootstrap.connection_status,
            advertisement_valid=bootstrap.advertisement_valid,
            onboarding_mode=bootstrap.onboarding_mode,
            onboarding_contract=bootstrap.onboarding_contract,
            api_base=bootstrap.api_base,
            mqtt_host=bootstrap.mqtt_host,
            mqtt_port=bootstrap.mqtt_port,
            register_session_endpoint=bootstrap.register_session_endpoint,
            registrations_endpoint=bootstrap.registrations_endpoint,
            compatibility_register_endpoint=bootstrap.compatibility_register_endpoint,
            compatibility_ai_node_register_endpoint=bootstrap.compatibility_ai_node_register_endpoint,
            last_checked_at=bootstrap.last_checked_at,
            last_error=bootstrap.last_error,
        )

    def test_connection(self) -> BootstrapDiscoveryResponse:
        state = self._store.load()
        core_base_url = state.pre_trust.core_base_url
        parsed = urlparse(core_base_url or "")
        host = parsed.hostname
        port = self._settings.bootstrap_mqtt_port
        connection_status = "connection_failed"
        last_error = None

        if not host:
            last_error = "core_base_url_missing"
        else:
            try:
                with socket(AF_INET, SOCK_STREAM) as sock:
                    sock.settimeout(1.0)
                    sock.connect((host, port))
                connection_status = "bootstrap_connected"
            except OSError as exc:
                last_error = str(exc)

        current_step_id = state.resume.current_step_id
        last_completed_step_id = state.resume.last_completed_step_id
        if connection_status == "bootstrap_connected":
            current_step_id = "bootstrap_discovery"
            last_completed_step_id = "core_connection"
        elif state.pre_trust.core_base_url:
            current_step_id = "core_connection"
            last_completed_step_id = "node_identity"

        updated = state.model_copy(
            update={
                "bootstrap_discovery": state.bootstrap_discovery.model_copy(
                    update={
                        "bootstrap_host": host,
                        "bootstrap_port": port,
                        "connection_status": connection_status,
                        "last_checked_at": _utc_now(),
                        "last_error": last_error,
                        "bootstrap_topic": self._settings.bootstrap_topic,
                    }
                ),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": current_step_id,
                        "last_completed_step_id": last_completed_step_id,
                    }
                ),
            }
        )
        self._store.save(updated)
        return self.status_payload()

    def validate_advertisement(self, payload: BootstrapAdvertisementRequest) -> BootstrapDiscoveryResponse:
        state = self._store.load()
        endpoints = payload.onboarding_endpoints or {}
        errors: list[str] = []

        if payload.topic != self._settings.bootstrap_topic:
            errors.append("bootstrap_topic_invalid")
        if payload.onboarding_mode != "api":
            errors.append("onboarding_mode_invalid")
        if payload.onboarding_contract != "global-node-v1":
            errors.append("onboarding_contract_invalid")
        if endpoints.get("register_session") != "/api/system/nodes/onboarding/sessions":
            errors.append("register_session_endpoint_invalid")
        if endpoints.get("registrations") != "/api/system/nodes/registrations":
            errors.append("registrations_endpoint_invalid")

        advertisement_valid = not errors
        connection_status = state.bootstrap_discovery.connection_status
        if advertisement_valid and connection_status == "bootstrap_connected":
            connection_status = "core_discovered"

        current_step_id = state.resume.current_step_id
        last_completed_step_id = state.resume.last_completed_step_id
        if advertisement_valid:
            current_step_id = "registration"
            last_completed_step_id = "bootstrap_discovery"

        updated = state.model_copy(
            update={
                "bootstrap_discovery": state.bootstrap_discovery.model_copy(
                    update={
                        "bootstrap_topic": payload.topic,
                        "advertisement_valid": advertisement_valid,
                        "onboarding_mode": payload.onboarding_mode,
                        "onboarding_contract": payload.onboarding_contract,
                        "api_base": payload.api_base,
                        "mqtt_host": payload.mqtt_host,
                        "mqtt_port": payload.mqtt_port,
                        "register_session_endpoint": endpoints.get("register_session"),
                        "registrations_endpoint": endpoints.get("registrations"),
                        "compatibility_register_endpoint": endpoints.get("register"),
                        "compatibility_ai_node_register_endpoint": endpoints.get("ai_node_register"),
                        "last_checked_at": _utc_now(),
                        "last_error": ",".join(errors) if errors else None,
                        "connection_status": connection_status,
                    }
                ),
                "resume": state.resume.model_copy(
                    update={
                        "current_step_id": current_step_id,
                        "last_completed_step_id": last_completed_step_id,
                    }
                ),
            }
        )
        self._store.save(updated)
        return self.status_payload()
