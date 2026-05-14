import asyncio
import json
import time

from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice import node_ui


def test_core_rendered_node_ui_manifest_keeps_local_ui_available(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    manifest_response = client.get("/api/node/ui-manifest")
    local_status_response = client.get("/api/node/status")

    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["schema_version"] == "1.0"
    assert manifest["node_type"] == "voice"
    assert manifest["node_id"] == "hexevoice"
    assert manifest["health"] == {
        "id": "node.health",
        "kind": "health_strip",
        "title": "Node Health",
        "data_endpoint": "/api/node/ui/overview/health",
        "refresh": node_ui.NEAR_LIVE_15S,
    }
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


def test_core_rendered_node_ui_overview_snapshot_advertises_only_health_and_warnings(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    snapshot = client.get("/api/node/ui/pages/overview").json()

    assert [card["id"] for card in snapshot["cards"]] == ["node.health", "node.warnings"]
    assert [card["kind"] for card in snapshot["cards"]] == ["health_strip", "warning_banner"]


def test_core_rendered_node_ui_caches_near_live_page_snapshots(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json")
    client = TestClient(create_app(settings))

    first = client.get("/api/node/ui/pages/runtime").json()
    time.sleep(0.01)
    second = client.get("/api/node/ui/pages/runtime").json()

    assert first == second
    cache_path = tmp_path / "rendered_node_ui_pages" / "runtime.json"
    assert cache_path.exists()
    assert json.loads(cache_path.read_text(encoding="utf-8")) == first


def test_core_rendered_node_ui_loads_page_snapshot_cache_from_runtime_file(tmp_path):
    settings = Settings(onboarding_state_path=tmp_path / "state.json")
    first_client = TestClient(create_app(settings))
    first = first_client.get("/api/node/ui/pages/runtime").json()

    second_client = TestClient(create_app(settings))
    second = second_client.get("/api/node/ui/pages/runtime").json()

    assert second == first


def test_page_snapshot_cache_serves_expired_disk_snapshot_while_refreshing(tmp_path):
    now = 100.0
    wall_now = time.time()
    builds: list[str] = []

    def clock() -> float:
        return now

    def wall_clock() -> float:
        return wall_now

    async def run() -> None:
        nonlocal now, wall_now
        cache = node_ui.PageSnapshotCache(cache_dir=tmp_path, clock=clock, wall_clock=wall_clock)

        async def first_builder() -> dict:
            builds.append("first")
            return {"page_id": "runtime", "version": "first"}

        first = await cache.get_or_build("runtime", node_ui.NEAR_LIVE_15S, first_builder)
        now += 20
        wall_now = cache.snapshot_path("runtime").stat().st_mtime + 20

        async def second_builder() -> dict:
            builds.append("second")
            return {"page_id": "runtime", "version": "second"}

        stale = await cache.get_or_build("runtime", node_ui.NEAR_LIVE_15S, second_builder)
        await asyncio.sleep(0)
        refreshed = await cache.get_or_build("runtime", node_ui.NEAR_LIVE_15S, second_builder)

        assert first == {"page_id": "runtime", "version": "first"}
        assert stale == first
        assert refreshed == {"page_id": "runtime", "version": "second"}
        assert builds == ["first", "second"]

    asyncio.run(run())


def test_page_snapshot_cache_serves_runtime_file_before_rebuild(tmp_path):
    builds: list[str] = []

    async def run() -> None:
        cache = node_ui.PageSnapshotCache(cache_dir=tmp_path)

        async def first_builder() -> dict:
            builds.append("first")
            return {"page_id": "runtime", "version": "first"}

        first = await cache.get_or_build("runtime", node_ui.NEAR_LIVE_15S, first_builder)
        cache.snapshot_path("runtime").write_text(
            json.dumps({"page_id": "runtime", "version": "file"}) + "\n",
            encoding="utf-8",
        )

        async def second_builder() -> dict:
            builds.append("second")
            return {"page_id": "runtime", "version": "second"}

        from_file = await cache.get_or_build("runtime", node_ui.NEAR_LIVE_15S, second_builder)

        assert first == {"page_id": "runtime", "version": "first"}
        assert from_file == {"page_id": "runtime", "version": "file"}
        assert builds == ["first"]

    asyncio.run(run())


def test_core_rendered_node_ui_invalidates_page_cache_on_endpoint_updates(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    first = client.get("/api/node/ui/pages/voice/endpoints").json()
    endpoint_card = next(card for card in first["cards"] if card["id"] == "voice.endpoints")
    assert endpoint_card["data"]["records"] == []

    client.post(
        "/api/endpoint/heartbeat",
        json={
            "endpoint_id": "esp-box-1",
            "device_state": "idle",
            "firmware_version": "0.1.0",
            "capabilities": {"storage": {"sd_card_available": True}},
        },
    )

    second = client.get("/api/node/ui/pages/voice/endpoints").json()
    endpoint_card = next(card for card in second["cards"] if card["id"] == "voice.endpoints")
    assert [record["endpoint_id"] for record in endpoint_card["data"]["records"]] == ["esp-box-1"]
    cache_path = tmp_path / "rendered_node_ui_pages" / "voice.endpoints.json"
    assert json.loads(cache_path.read_text(encoding="utf-8")) == second


def test_core_rendered_node_ui_overview_and_runtime_cards(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    overview = client.get("/api/node/ui/overview/node").json()
    health = client.get("/api/node/ui/overview/health").json()
    warnings = client.get("/api/node/ui/overview/warnings").json()
    facts = client.get("/api/node/ui/overview/facts").json()
    runtime = client.get("/api/node/ui/runtime/services").json()
    providers = client.get("/api/node/ui/providers/status").json()
    runtime_page = client.get("/api/node/ui/pages/runtime").json()

    assert overview["kind"] == "node_overview"
    assert overview["identity"]["local_ui_mode"] == "full"
    assert health["kind"] == "health_strip"
    assert [item["state_name"] for item in health["items"]] == [
        "Life cycle",
        "Trust",
        "Governance",
        "Providers",
        "STT engine",
        "TTS engine",
    ]
    assert warnings["kind"] == "warning_banner"
    assert facts["kind"] == "facts_card"
    assert any(fact["id"] == "node_id" for fact in facts["facts"])
    assert runtime["kind"] == "runtime_service"
    assert {service["id"] for service in runtime["services"]} >= {"backend", "stt", "wake", "tts"}
    wake_runtime = next(service for service in runtime["services"] if service["id"] == "wake")
    assert wake_runtime["label"] == "Wake Word"
    assert wake_runtime["provider"] == "openwakeword"
    assert [action["label"] for action in wake_runtime["actions"]] == ["Start", "Stop", "Restart"]
    runtime_page_card = next(card for card in runtime_page["cards"] if card["id"] == "runtime.services")
    action_map = {action["id"]: action for action in runtime_page_card["actions"]}
    wake_start = node_ui.service_control_action_id("openwakeword", "start")
    wake_stop = node_ui.service_control_action_id("openwakeword", "stop")
    wake_restart = node_ui.service_control_action_id("openwakeword", "restart")
    assert action_map[wake_start]["endpoint"] == "/api/node/ui/runtime/services/openwakeword/start"
    assert action_map[wake_stop]["endpoint"] == "/api/node/ui/runtime/services/openwakeword/stop"
    assert action_map[wake_stop]["destructive"] is True
    assert action_map[wake_restart]["endpoint"] == "/api/node/ui/runtime/services/openwakeword/restart"
    assert providers["kind"] == "provider_status"
    assert {provider["id"] for provider in providers["providers"]} >= {"stt", "tts", "wake"}
    provider_page_card = next(card for card in runtime_page["cards"] if card["id"] == "runtime.providers")
    provider_action_map = {action["id"]: action for action in provider_page_card["actions"]}
    voice_action_id = node_ui.provider_setup_action_id("voice")
    assert provider_action_map[voice_action_id]["method"] == "PUT"
    assert provider_action_map[voice_action_id]["endpoint"] == "/api/node/ui/providers/voice/setup"
    tts_provider = next(provider for provider in providers["providers"] if provider["id"] == "tts")
    assert [fact["id"] for fact in tts_provider["setup"]["facts"]] == [
        "provider_id",
        "setup_provider_id",
        "enabled",
        "default",
        "declaration_allowed",
        "enabled_providers",
        "supported_providers",
    ]
    assert tts_provider["setup"]["actions"][0]["id"].startswith("configure_provider_setup.")
    setup_form = tts_provider["setup"]["form"]
    assert setup_form["submit_action_id"] == tts_provider["setup"]["actions"][0]["id"]
    assert [field["id"] for field in setup_form["fields"]] == ["enabled", "default"]
    assert setup_form["fields"][0]["type"] == "checkbox"
    assert setup_form["fields"][1]["type"] == "checkbox"
    wake_provider = next(provider for provider in providers["providers"] if provider["id"] == "wake")
    wake_setup = {fact["id"]: fact["value"] for fact in wake_provider["setup"]["facts"]}
    assert wake_setup["enabled"] == "no"


def test_provider_status_treats_voice_setup_as_wake_enabled():
    card = node_ui.provider_status(
        {"openwakeword": "running", "piper_tts": "running"},
        {"wake_provider": {"provider": "supervised_openwakeword"}},
        {"provider": "piper"},
        {
            "enabled_providers": ["voice", "piper", "external_faster_whisper"],
            "supported_providers": ["voice", "piper", "external_faster_whisper"],
            "declaration_allowed": True,
        },
    )

    wake_provider = next(provider for provider in card["providers"] if provider["id"] == "wake")
    wake_setup = {fact["id"]: fact["value"] for fact in wake_provider["setup"]["facts"]}
    assert wake_setup["enabled"] == "yes"
    wake_form = wake_provider["setup"]["form"]
    assert wake_form["title"] == "Voice Setup"
    assert wake_form["fields"][0]["value"] is True
    assert wake_form["submit_action_id"] == node_ui.provider_setup_action_id("voice")


def test_provider_status_marks_healthy_stt_without_status_as_ready():
    card = node_ui.provider_status(
        {"openwakeword": "running", "piper_tts": "running"},
        {
            "turn_pipeline": {
                "stt": {
                    "provider": "external_faster_whisper",
                    "healthy": True,
                    "configured": True,
                    "model": "base.en",
                }
            },
            "wake_provider": {"provider": "supervised_openwakeword"},
        },
        {"provider": "piper"},
        {"enabled_providers": ["voice", "piper", "external_faster_whisper"]},
    )

    stt_provider = next(provider for provider in card["providers"] if provider["id"] == "stt")
    assert stt_provider["state"] == "ready"
    assert stt_provider["tone"] == "success"


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
    assert artifacts["kind"] == "record_list"
    assert media["kind"] == "record_list"
    assert media["summary"]["endpoint_count"] == 1


def test_core_rendered_node_ui_safe_actions(tmp_path):
    client = TestClient(create_app(Settings(onboarding_state_path=tmp_path / "state.json")))

    refresh = client.post("/api/node/ui/actions/refresh-status")
    assistant_turn = client.post("/api/node/ui/actions/test-assistant-turn")
    unsupported_service = client.post("/api/node/ui/runtime/services/not_registered/restart")

    assert refresh.status_code == 200
    assert refresh.json()["accepted"] is True
    assert assistant_turn.status_code == 200
    assert assistant_turn.json()["endpoint_id"] == "core-rendered-ui-test"
    assert unsupported_service.status_code == 200
    assert unsupported_service.json()["accepted"] is False
    assert unsupported_service.json()["status"] == "unsupported_service"


def test_core_rendered_node_ui_reports_configured_local_ui_mode(tmp_path):
    client = TestClient(
        create_app(Settings(onboarding_state_path=tmp_path / "state.json", voice_local_ui_mode="setup_only"))
    )

    response = client.get("/api/node/ui/overview/node")

    assert response.status_code == 200
    assert response.json()["identity"]["local_ui_mode"] == "setup_only"
    assert client.get("/api/node/status").status_code == 200


def test_core_rendered_node_ui_health_uses_governance_freshness_state():
    health = node_ui.overview_health(
        {
            "lifecycle_state": "operational",
            "trust_state": "trusted",
            "governance_sync_status": "issued",
            "governance_freshness_state": "fresh",
        },
        {},
        {"configured": True},
        {},
        {"turn_pipeline": {"stt": {"status": "ready"}, "tts": {"status": "ready"}}},
    )

    governance = next(item for item in health["items"] if item["state_name"] == "Governance")
    assert governance["current_state"] == "fresh"
    assert governance["tone"] == "info"
