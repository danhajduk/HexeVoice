from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from hexevoice.api.models import EndpointDiscoveryRequest
from hexevoice.config.settings import Settings
from hexevoice.endpoint.discovery import EndpointDiscoveryService
from hexevoice.main import create_app
from hexevoice.persistence import EndpointRegistryRecord, EndpointRegistryStore, PersistedEndpointRegistry


def test_endpoint_discovery_offer_returns_backend_pairing_settings(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                api_host="0.0.0.0",
                api_port=9004,
                endpoint_discovery_advertise_host="hexevoice.local",
                endpoint_discovery_udp_enabled=False,
            )
        )
    )

    response = client.post(
        "/api/endpoint/discovery/offer",
        json={
            "endpoint_id": "esp-box-1",
            "display_name": "ESP Box 1",
            "firmware_version": "0.1.0",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["schema_version"] == "hexevoice.endpoint.discovery.v1"
    assert payload["endpoint_id"] == "esp-box-1"
    assert payload["pairing_state"] == "paired"
    assert payload["backend_host"] == "hexevoice.local"
    assert payload["http_port"] == 9004
    assert payload["ws_port"] == 9004
    assert payload["heartbeat_path"] == "/api/endpoint/heartbeat"
    assert payload["voice_ws_path"] == "/api/voice/ws"


def test_endpoint_discovery_rejects_duplicate_online_identity(tmp_path):
    store = EndpointRegistryStore(path=tmp_path / "endpoint_registry.json")
    now = datetime.now(timezone.utc).isoformat()
    store.save(
        PersistedEndpointRegistry(
            endpoints={
                "esp-box-1": EndpointRegistryRecord(
                    endpoint_id="esp-box-1",
                    device_state="idle",
                    ip_address="10.0.0.20",
                    first_seen_at=now,
                    last_seen_at=now,
                    updated_at=now,
                )
            }
        )
    )
    service = EndpointDiscoveryService(
        settings=Settings(endpoint_discovery_advertise_host="hexevoice.local"),
        endpoint_registry_store=store,
        stale_after_seconds=60,
    )

    response = service.offer(
        EndpointDiscoveryRequest(endpoint_id="esp-box-1"),
        source_ip="10.0.0.99",
    )

    assert response.accepted is False
    assert response.pairing_state == "duplicate_online"
    assert response.reason == "endpoint_id_already_online"


def test_endpoint_discovery_recovers_stale_pairing(tmp_path):
    store = EndpointRegistryStore(path=tmp_path / "endpoint_registry.json")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    store.save(
        PersistedEndpointRegistry(
            endpoints={
                "esp-box-1": EndpointRegistryRecord(
                    endpoint_id="esp-box-1",
                    device_state="idle",
                    ip_address="10.0.0.20",
                    first_seen_at=stale,
                    last_seen_at=stale,
                    updated_at=stale,
                )
            }
        )
    )
    service = EndpointDiscoveryService(
        settings=Settings(endpoint_discovery_advertise_host="hexevoice.local", api_port=9004),
        endpoint_registry_store=store,
        stale_after_seconds=60,
    )

    response = service.offer(
        EndpointDiscoveryRequest(endpoint_id="esp-box-1"),
        source_ip="10.0.0.99",
    )
    record = store.load().endpoints["esp-box-1"]

    assert response.accepted is True
    assert response.pairing_state == "stale_recovered"
    assert record.ip_address == "10.0.0.99"
    assert record.last_seen_at != stale


def test_endpoint_discovery_offer_validates_required_endpoint_id(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "state.json",
                endpoint_discovery_udp_enabled=False,
            )
        )
    )

    response = client.post("/api/endpoint/discovery/offer", json={"endpoint_id": ""})

    assert response.status_code == 422
