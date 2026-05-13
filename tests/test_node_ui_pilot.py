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

    assert overview["kind"] == "node_overview"
    assert overview["identity"]["local_ui_mode"] == "full"
    assert health["kind"] == "health_strip"
    assert [item["id"] for item in health["items"]] == [
        "lifecycle",
        "trust",
        "governance",
        "providers",
        "stt_engine",
        "tts_engine",
    ]
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

    governance = next(item for item in health["items"] if item["id"] == "governance")
    assert governance["value"] == "fresh"
    assert governance["tone"] == "info"
