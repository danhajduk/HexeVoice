from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app


def test_core_rendered_node_ui_manifest_keeps_local_ui_available(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    manifest_response = client.get("/api/node/ui-manifest")
    local_status_response = client.get("/api/node/status")

    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["schema_version"] == "1.0"
    assert manifest["node_type"] == "voice"
    assert manifest["node_id"] == "hexevoice"
    assert {page["id"] for page in manifest["pages"]} == {
        "overview",
        "runtime",
        "voice.endpoints",
        "voice.intents",
        "voice.tts",
    }
    for page in manifest["pages"]:
        assert page["page_endpoint"].startswith("/api/node/ui/pages/")
        assert "refresh" in page
        assert "surfaces" not in page
    assert local_status_response.status_code == 200
    assert local_status_response.json()["current_step_id"] == "node_identity"


def test_core_rendered_node_ui_page_snapshots_bundle_cards(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    expected_pages = {
        "overview": "/api/node/ui/pages/overview",
        "runtime": "/api/node/ui/pages/runtime",
        "voice.endpoints": "/api/node/ui/pages/voice/endpoints",
        "voice.intents": "/api/node/ui/pages/voice/intents",
        "voice.tts": "/api/node/ui/pages/voice/tts",
    }

    for page_id, endpoint in expected_pages.items():
        response = client.get(endpoint)

        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["page_id"] == page_id
        assert "refresh" in snapshot
        assert snapshot["cards"]
        assert snapshot["cards"][0]["id"] == "node.health"
        assert snapshot["cards"][0]["kind"] == "health_strip"
        assert snapshot["cards"][0]["data"]["kind"] == "health_strip"
        assert all("data" in card for card in snapshot["cards"])


def test_core_rendered_node_ui_overview_and_runtime_cards(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    overview = client.get("/api/node/ui/overview/node").json()
    health = client.get("/api/node/ui/overview/health").json()
    warnings = client.get("/api/node/ui/overview/warnings").json()
    facts = client.get("/api/node/ui/overview/facts").json()
    runtime = client.get("/api/node/ui/runtime/services").json()
    providers = client.get("/api/node/ui/providers/status").json()

    assert overview["kind"] == "node_overview"
    assert overview["identity"]["local_ui_mode"] == "full"
    assert health["kind"] == "health_strip"
    assert {item["id"] for item in health["items"]} >= {"lifecycle", "trust", "operational"}
    assert warnings["kind"] == "warning_banner"
    assert facts["kind"] == "facts_card"
    assert any(fact["id"] == "node_id" for fact in facts["facts"])
    assert runtime["kind"] == "runtime_service"
    assert {service["id"] for service in runtime["services"]} >= {"backend", "stt", "tts"}
    assert providers["kind"] == "provider_status"
    assert {provider["id"] for provider in providers["providers"]} >= {"stt", "tts", "wake"}


def test_core_rendered_voice_domain_cards(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))
    client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "device_state": "idle",
            "firmware_version": "0.1.0",
            "capabilities": {"storage": {"sd_card_available": True}},
        },
    )

    endpoints = client.get("/api/node/ui/voice/endpoints").json()
    endpoint_actions = client.get("/api/node/ui/voice/endpoint-actions").json()
    sessions = client.get("/api/node/ui/voice/sessions").json()
    intents = client.get("/api/node/ui/voice/intents").json()
    intent_actions = client.get("/api/node/ui/voice/intent-actions").json()
    tts = client.get("/api/node/ui/voice/tts").json()
    artifacts = client.get("/api/node/ui/voice/tts-artifacts").json()
    media = client.get("/api/node/ui/voice/media").json()

    assert endpoints["kind"] == "record_list"
    assert endpoints["records"][0]["endpoint_id"] == "esp-box-1"
    assert endpoint_actions["kind"] == "action_panel"
    assert sessions["kind"] == "record_list"
    assert intents["kind"] == "record_list"
    assert intents["summary"]["registered_count"] >= 1
    assert intent_actions["kind"] == "action_panel"
    assert tts["kind"] == "provider_status"
    assert artifacts["kind"] == "artifact_browser"
    assert media["kind"] == "artifact_browser"
    assert media["summary"]["endpoint_count"] == 1


def test_core_rendered_node_ui_safe_actions(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    refresh = client.post("/api/node/ui/actions/refresh-status")
    assistant_turn = client.post("/api/node/ui/actions/test-assistant-turn")

    assert refresh.status_code == 200
    assert refresh.json()["accepted"] is True
    assert assistant_turn.status_code == 200
    assert assistant_turn.json()["endpoint_id"] == "core-rendered-ui-test"


def test_core_rendered_node_ui_reports_configured_local_ui_mode(tmp_path):
    client = TestClient(
        create_app(Settings(onboarding_state_path=tmp_path / "state.json", voice_local_ui_mode="setup_only"))
    )

    response = client.get("/api/node/ui/overview/node")

    assert response.status_code == 200
    assert response.json()["identity"]["local_ui_mode"] == "setup_only"
    assert client.get("/api/node/status").status_code == 200
