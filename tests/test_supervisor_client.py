from hexevoice.supervisor.client import supervisor_client_config


def test_supervisor_client_default_timeout_allows_resource_sampling(monkeypatch):
    monkeypatch.delenv("HEXE_SUPERVISOR_API_TIMEOUT_S", raising=False)

    assert supervisor_client_config().timeout_s == 8.0


def test_supervisor_client_timeout_env_override(monkeypatch):
    monkeypatch.setenv("HEXE_SUPERVISOR_API_TIMEOUT_S", "3.5")

    assert supervisor_client_config().timeout_s == 3.5
