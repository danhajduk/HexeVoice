from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.persistence import EndpointRegistryRecord, EndpointRegistryStore, PersistedEndpointRegistry


def client_for(tmp_path, *, stale_after_seconds=60):
    return TestClient(
        create_app(
            Settings(
                onboarding_state_path=tmp_path / "onboarding-state.json",
                endpoint_stale_after_seconds=stale_after_seconds,
            )
        )
    )


def test_endpoint_heartbeat_creates_persistent_registry_record(tmp_path):
    client = client_for(tmp_path)

    heartbeat = client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "device_state": "idle",
            "firmware_version": "0.1.0",
            "ip_address": "10.0.0.55",
            "rssi_dbm": -58,
            "capabilities": {
                "touchscreen": {"available": True},
                "storage": {"sd_card_available": False},
                "display": {"available": True, "resolution": "320x240", "pixel_format": "rgb565"},
                "audio": {
                    "input": {"available": True, "sample_rate_hz": 16000, "channels": 1},
                    "output": {"available": True, "volume_percent": 42, "muted": False},
                },
                "firmware": {"version": "0.1.0", "build_date": "Apr 26 2026"},
            },
        },
    )

    assert heartbeat.status_code == 200
    store = EndpointRegistryStore(path=tmp_path / "endpoint_registry.json")
    persisted = store.load().endpoints["esp-box-1"]
    assert persisted.endpoint_id == "esp-box-1"
    assert persisted.firmware_version == "0.1.0"
    assert persisted.ip_address == "10.0.0.55"
    assert persisted.rssi_dbm == -58
    assert persisted.capabilities["display"]["resolution"] == "320x240"
    assert persisted.capabilities["audio"]["output"]["volume_percent"] == 42
    assert persisted.capabilities["touchscreen"]["available"] is True

    restarted_client = client_for(tmp_path)
    status = restarted_client.get("/api/endpoint/status/esp-box-1")
    assert status.status_code == 200
    assert status.json()["firmware_version"] == "0.1.0"
    assert status.json()["capabilities"]["audio"]["input"]["sample_rate_hz"] == 16000
    assert status.json()["capabilities"]["audio"]["output"]["muted"] is False


def test_reconnect_updates_runtime_fields_without_erasing_operator_metadata(tmp_path):
    client = client_for(tmp_path)
    assert client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "firmware_version": "0.1.0",
            "capabilities": {"display": {"resolution": "320x240"}},
        },
    ).status_code == 200

    metadata = client.patch(
        "/api/endpoints/esp-box-1",
        json={"display_name": "Kitchen Voice", "zone_id": "kitchen"},
    )
    assert metadata.status_code == 200
    assert metadata.json()["display_name"] == "Kitchen Voice"
    assert metadata.json()["zone_id"] == "kitchen"

    reconnect = client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "device_state": "listening",
            "session_id": "session-2",
            "ip_address": "10.0.0.56",
            "rssi_dbm": -64,
        },
    )

    assert reconnect.status_code == 200
    status = client.get("/api/endpoint/status/esp-box-1").json()
    assert status["display_name"] == "Kitchen Voice"
    assert status["zone_id"] == "kitchen"
    assert status["device_state"] == "listening"
    assert status["session_id"] == "session-2"
    assert status["firmware_version"] == "0.1.0"
    assert status["ip_address"] == "10.0.0.56"
    assert status["capabilities"]["display"]["resolution"] == "320x240"


def test_endpoint_registry_projects_stale_records(tmp_path):
    stale_seen = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    store = EndpointRegistryStore(path=tmp_path / "endpoint_registry.json")
    store.save(
        PersistedEndpointRegistry(
            endpoints={
                "esp-box-1": EndpointRegistryRecord(
                    endpoint_id="esp-box-1",
                    display_name="Desk Voice",
                    zone_id="office",
                    device_state="idle",
                    firmware_version="0.1.0",
                    first_seen_at=stale_seen,
                    last_seen_at=stale_seen,
                    updated_at=stale_seen,
                )
            }
        )
    )
    client = client_for(tmp_path, stale_after_seconds=10)

    status = client.get("/api/endpoint/status/esp-box-1")
    list_response = client.get("/api/endpoints")

    assert status.status_code == 200
    assert status.json()["connection_state"] == "stale"
    assert status.json()["stale"] is True
    assert list_response.status_code == 200
    assert list_response.json()["endpoints"][0]["endpoint_id"] == "esp-box-1"
    assert list_response.json()["endpoints"][0]["connection_state"] == "stale"


def test_operator_metadata_update_is_partial_and_clearable(tmp_path):
    client = client_for(tmp_path)
    assert client.post("/api/endpoint/heartbeat", json={"endpoint_id": "esp-box-1"}).status_code == 200
    assert client.patch(
        "/api/endpoints/esp-box-1",
        json={"display_name": "Kitchen Voice", "zone_id": "kitchen"},
    ).status_code == 200

    partial = client.patch("/api/endpoints/esp-box-1", json={"display_name": "Counter Voice"})
    cleared = client.patch("/api/endpoints/esp-box-1", json={"zone_id": ""})

    assert partial.status_code == 200
    assert partial.json()["display_name"] == "Counter Voice"
    assert partial.json()["zone_id"] == "kitchen"
    assert cleared.status_code == 200
    assert cleared.json()["display_name"] == "Counter Voice"
    assert cleared.json()["zone_id"] is None
