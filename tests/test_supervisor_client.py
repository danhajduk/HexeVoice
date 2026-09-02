import httpx

from hexevoice.supervisor.client import SupervisorApiClient, SupervisorClientConfig, supervisor_client_config


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, **kwargs})
        return httpx.Response(200, json={"ok": True})


def test_supervisor_client_default_timeout_allows_resource_sampling(monkeypatch):
    monkeypatch.delenv("HEXE_SUPERVISOR_API_TIMEOUT_S", raising=False)

    assert supervisor_client_config().timeout_s == 8.0


def test_supervisor_client_timeout_env_override(monkeypatch):
    monkeypatch.setenv("HEXE_SUPERVISOR_API_TIMEOUT_S", "3.5")

    assert supervisor_client_config().timeout_s == 3.5


def test_supervisor_client_health_uses_configured_default_timeout():
    fake_client = FakeHttpClient()
    client = SupervisorApiClient(
        SupervisorClientConfig(
            transport="socket",
            base_url="http://127.0.0.1:9009",
            unix_socket="/tmp/supervisor.sock",
            timeout_s=8.0,
        ),
        client=fake_client,
    )

    assert client.health() == {"ok": True}

    assert fake_client.calls == [
        {
            "method": "GET",
            "path": "/api/supervisor/health",
            "json": None,
            "params": None,
        }
    ]


def test_supervisor_client_ble_scan_timeout_tracks_requested_scan_window():
    fake_client = FakeHttpClient()
    client = SupervisorApiClient(
        SupervisorClientConfig(
            transport="socket",
            base_url="http://127.0.0.1:9009",
            unix_socket="/tmp/supervisor.sock",
            timeout_s=8.0,
        ),
        client=fake_client,
    )

    assert client.scan_ble({"adapter": "hci0", "scan_seconds": 15}) == {"ok": True}

    assert fake_client.calls == [
        {
            "method": "POST",
            "path": "/api/supervisor/hardware/bluetooth/ble/scan",
            "json": {"adapter": "hci0", "scan_seconds": 15},
            "params": None,
            "timeout": 20.0,
        }
    ]
