from datetime import datetime, timedelta, timezone
import sys

from fastapi.testclient import TestClient

from hexevoice.api.models import EndpointDiscoveryRequest
from hexevoice.config.settings import Settings
from hexevoice.endpoint.beacon import EndpointBeaconService, build_endpoint_beacon_payload
from hexevoice.endpoint.discovery import EndpointDiscoveryService
from hexevoice.endpoint.mdns import EndpointMdnsAdvertiser, build_endpoint_mdns_metadata
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


def test_endpoint_mdns_metadata_uses_ip_based_api_and_ui_urls():
    metadata = build_endpoint_mdns_metadata(
        Settings(
            api_port=9004,
            public_api_base_url="http://hexe.local:9004",
            public_ui_base_url="http://hexe.local:8084",
            endpoint_mdns_advertise_host="10.0.0.100",
        ),
        node_id="node-voice-123",
    )

    assert metadata.service_type == "_hexevoice._tcp.local."
    assert metadata.api_url == "http://10.0.0.100:9004"
    assert metadata.ui_url == "http://10.0.0.100:8084"
    assert metadata.txt_properties() == {
        "api_url": "http://10.0.0.100:9004",
        "ui_url": "http://10.0.0.100:8084",
        "api_port": "9004",
        "ui_port": "8084",
        "node_id": "node-voice-123",
        "node_type": "voice-node",
        "tls": "false",
        "advertised_ip": "10.0.0.100",
    }


def test_endpoint_mdns_disabled_status_is_diagnostic_only():
    advertiser = EndpointMdnsAdvertiser(settings=Settings(endpoint_mdns_enabled=False))

    advertiser.start()

    assert advertiser.status()["enabled"] is False
    assert advertiser.status()["active"] is False
    assert advertiser.status()["status"] == "disabled"


def test_endpoint_mdns_import_failure_does_not_block_api(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "zeroconf", None)
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        endpoint_discovery_udp_enabled=False,
        endpoint_mdns_enabled=True,
        endpoint_mdns_advertise_host="10.0.0.100",
    )

    with TestClient(create_app(settings)) as client:
        health = client.get("/health/live")
        status = client.get("/api/endpoint/discovery/status")

    assert health.status_code == 200
    assert status.status_code == 200
    assert status.json()["mdns"]["active"] is False
    assert status.json()["mdns"]["last_error"] == "zeroconf_not_installed"


def test_endpoint_beacon_payload_uses_ip_based_api_and_ui_urls():
    payload = build_endpoint_beacon_payload(
        Settings(
            api_port=9004,
            public_api_base_url="http://hexe.local:9004",
            public_ui_base_url="http://hexe.local:8084",
            endpoint_beacon_advertise_host="10.0.0.100",
        ),
        node_id="node-voice-123",
    )

    assert payload["schema_version"] == "hexevoice.node.beacon.v1"
    assert payload["node"]["node_id"] == "node-voice-123"
    assert payload["node"]["node_type"] == "voice-node"
    assert payload["network"]["advertised_ip"] == "10.0.0.100"
    assert payload["network"]["tls"] is False
    assert payload["api"] == {
        "url": "http://10.0.0.100:9004",
        "port": 9004,
        "heartbeat_path": "/api/endpoint/heartbeat",
        "voice_ws_path": "/api/voice/ws",
    }
    assert payload["ui"] == {"url": "http://10.0.0.100:8084", "port": 8084}


def test_endpoint_beacon_disabled_status_is_diagnostic_only():
    service = EndpointBeaconService(settings=Settings(endpoint_beacon_udp_enabled=False))

    assert service.status()["enabled"] is False
    assert service.status()["active"] is False
    assert service.status()["status"] == "disabled"


def test_endpoint_beacon_rejects_loopback_or_hostname_advertised_host():
    for advertised_host in ("127.0.0.1", "0.0.0.0", "hexe.local"):
        settings = Settings(endpoint_beacon_advertise_host=advertised_host)
        try:
            build_endpoint_beacon_payload(settings)
        except ValueError as exc:
            assert str(exc) == "invalid_advertised_lan_ip"
        else:
            raise AssertionError(f"{advertised_host} should have been rejected")


def test_endpoint_discovery_status_includes_beacon_diagnostics(tmp_path):
    settings = Settings(
        onboarding_state_path=tmp_path / "state.json",
        endpoint_discovery_udp_enabled=False,
        endpoint_mdns_enabled=False,
        endpoint_beacon_udp_enabled=False,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/endpoint/discovery/status")

    assert response.status_code == 200
    assert response.json()["beacon"] == {
        "enabled": False,
        "active": False,
        "status": "disabled",
        "host": "255.255.255.255",
        "port": 9135,
        "interval_seconds": 5.0,
        "advertised_ip": None,
        "api_url": None,
        "ui_url": None,
        "last_sent_at": None,
        "last_error": None,
    }
