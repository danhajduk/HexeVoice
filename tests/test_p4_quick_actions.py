from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from hexevoice.p4_quick_actions import P4QuickActionService
from hexevoice.radar_assets import PreparedRadarAsset


class FakeWeather:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.listeners = []
        self.result_listeners = []

    def prepared_snapshot(self):
        return self.snapshot

    def add_listener(self, listener):
        self.listeners.append(listener)

    def remove_listener(self, listener):
        self.listeners.remove(listener)

    def add_result_listener(self, listener):
        self.result_listeners.append(listener)

    def remove_result_listener(self, listener):
        self.result_listeners.remove(listener)


class FakeManager:
    def __init__(self):
        self.calls = []

    async def push_ui_layout_command(self, **kwargs):
        self.calls.append(("layout", kwargs))
        return {"accepted": True}

    async def push_play_sound_command(self, **kwargs):
        self.calls.append(("sound", kwargs))
        return {"accepted": True}

    async def push_listen_command(self, **kwargs):
        self.calls.append(("listen", kwargs))
        return {"accepted": True}

    async def push_ui_screen_clear_command(self, **kwargs):
        self.calls.append(("clear", kwargs))
        return {"accepted": True}

    async def push_ui_screen_set_command(self, **kwargs):
        self.calls.append(("screen_set", kwargs))
        return {"accepted": True}

    async def push_asset_prepare_command(self, **kwargs):
        self.calls.append(("asset_prepare", kwargs))
        return {"accepted": True}

    def connected_endpoint_ids(self, **kwargs):
        assert kwargs == {"board_profile": "waveshare_p4_wifi6_touch_lcd_7b"}
        return ["p4"]


class FakeRadarAssets:
    async def prepare(self, snapshot):
        return PreparedRadarAsset(
            asset_id="weather-radar-" + "a" * 24,
            snapshot_id=snapshot["snapshot_id"],
            revision="frame-1",
            path=None,
            sha256="b" * 64,
            size_bytes=800 * 420 * 3,
        )


def quick_actions(manager, weather):
    invocations = []

    async def invoke(**kwargs):
        invocations.append(kwargs)
        return {"matched": True, "intent_id": kwargs["intent_id"]}

    service = P4QuickActionService(
        manager=manager,
        weather=weather,
        radar_assets=FakeRadarAssets(),
        radar_asset_url=lambda asset_id: f"http://voice/radar/{asset_id}",
        interaction_api_base_url="http://interaction",
        intent_invoker=invoke,
    )
    service.test_intent_invocations = invocations
    return service


def snapshot(*, radar=True):
    now = datetime.now(UTC)
    return {
        "snapshot_id": "weather-home-1",
        "cache_revision": "weather-revision-1",
        "location": {"label": "Home"},
        "current_conditions": {"temperature": 56, "condition": "Clear"},
        "forecast_summary": {"summary": "Clear"},
        "transcript": "It is 56 degrees and clear.",
        "tts": {
            "status": "ready",
            "audio_url": "http://voice/weather-high.wav",
            "revision": "tts-1",
            "variants": {
                "compact": {"audio_url": "http://voice/weather-compact.wav"},
                "high": {"audio_url": "http://voice/weather-high.wav"},
                "standard": {"audio_url": "http://voice/weather-standard.wav"},
            },
        },
        "radar": {
            "status": "ready" if radar else "absent",
            "image_url": "http://interaction/radar.png" if radar else None,
            "sha256": "a" * 64 if radar else None,
            "observation_time": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "attribution": ["Radar provider"],
        },
    }


def test_weather_button_invokes_declared_intent_and_waits_for_owner_result():
    manager = FakeManager()
    service = quick_actions(manager, FakeWeather(snapshot()))

    asyncio.run(service.handle_button({"endpoint_id": "p4", "screen_id": "idle", "button_id": "button_weather", "intent_id": "weather.current"}))

    assert service.test_intent_invocations == [
        {"endpoint_id": "p4", "intent_id": "weather.current", "text": "what is the current weather"}
    ]
    assert manager.calls == []


def test_weather_success_result_renders_matching_snapshot():
    manager = FakeManager()
    weather = FakeWeather(snapshot())
    service = quick_actions(manager, weather)

    asyncio.run(
        service._handle_weather_result(
            {
                "event_type": "weather.current_succeeded",
                "data": {
                    "endpoint_id": "p4",
                    "snapshot_id": "weather-home-1",
                    "snapshot_revision": "weather-revision-1",
                },
            }
        )
    )

    assert manager.calls[0][0] == "layout"
    assert manager.calls[1][0] == "sound"


def test_weather_success_waits_for_matching_snapshot():
    manager = FakeManager()
    weather = FakeWeather(snapshot())
    service = quick_actions(manager, weather)
    result = {
        "event_type": "weather.current_succeeded",
        "data": {
            "endpoint_id": "p4",
            "snapshot_id": "weather-home-2",
            "snapshot_revision": "weather-revision-2",
        },
    }

    asyncio.run(service._handle_weather_result(result))

    assert manager.calls == []
    assert service._pending_weather_results["p4"]["snapshot_id"] == "weather-home-2"


def test_weather_failed_result_displays_safe_message():
    manager = FakeManager()
    service = quick_actions(manager, FakeWeather(snapshot()))

    asyncio.run(
        service._handle_weather_result(
            {
                "event_type": "weather.current_failed",
                "data": {"endpoint_id": "p4", "message": "Weather is temporarily unavailable."},
            }
        )
    )

    assert manager.calls[0][0] == "layout"
    texts = [item.get("text") for item in manager.calls[0][1]["layout"]["elements"]]
    assert "Weather is temporarily unavailable." in texts


def test_weather_button_on_weather_screen_still_refreshes_weather_through_intent():
    manager = FakeManager()
    service = quick_actions(manager, FakeWeather(snapshot()))

    asyncio.run(service.handle_button({"endpoint_id": "p4", "screen_id": "weather", "button_id": "button_weather", "intent_id": "weather.current"}))

    assert service.test_intent_invocations[0]["intent_id"] == "weather.current"
    assert manager.calls == []


def test_new_weather_snapshot_prepares_radar_for_connected_p4_endpoint():
    manager = FakeManager()
    weather = FakeWeather(snapshot())
    service = quick_actions(manager, weather)

    async def run():
        service.start(asyncio.get_running_loop())
        weather.listeners[0](weather.snapshot)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        service.stop()

    asyncio.run(run())

    assert weather.listeners == []
    assert manager.calls[0][0] == "asset_prepare"
    assert manager.calls[0][1]["endpoint_id"] == "p4"
    assert manager.calls[0][1]["asset"]["width"] == 800
    assert manager.calls[0][1]["asset"]["height"] == 420


def test_timer_button_begins_interaction_owned_custom_capture(monkeypatch):
    manager = FakeManager()
    service = quick_actions(manager, FakeWeather(snapshot()))

    async def handler(request):
        assert request.url.path == "/api/interaction/devices/p4/timer-quick-actions"
        assert json.loads(request.content) == {"action": "begin_custom"}
        return httpx.Response(200, json={
            "prompt": "How long should the timer be?",
            "conversation": {"session_id": "timer-session-1", "status": "awaiting_duration"},
        })

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))

    asyncio.run(service.handle_button({"endpoint_id": "p4", "screen_id": "idle", "button_id": "button_timer", "intent_id": "timer.create"}))

    assert service.test_intent_invocations[0]["intent_id"] == "timer.create"
    assert [call[0] for call in manager.calls] == ["layout", "sound"]
    assert manager.calls[0][1]["layout"]["id"] == "timer_quick"
    assert manager.calls[1][1]["session_id"] == "timer-session-1"
    asyncio.run(service.handle_endpoint_event({
        "event_type": "tts.playback.completed",
        "endpoint_id": "p4",
        "session_id": "timer-session-1",
    }))
    assert manager.calls[2][0] == "listen"
    assert manager.calls[2][1]["reason"] == "timer_quick_action_capture"


def test_config_button_invokes_declared_settings_intent():
    manager = FakeManager()
    service = quick_actions(manager, FakeWeather(snapshot()))

    asyncio.run(service.handle_button({"endpoint_id": "p4", "screen_id": "weather", "button_id": "button_config", "intent_id": "endpoint.settings.open"}))

    assert service.test_intent_invocations == [
        {"endpoint_id": "p4", "intent_id": "endpoint.settings.open", "text": "open settings"}
    ]
    assert manager.calls == []


def test_custom_timer_capture_submits_duration_then_confirms(monkeypatch):
    manager = FakeManager()
    service = quick_actions(manager, FakeWeather(snapshot()))
    service._timer_sessions["p4"] = "timer-session-1"
    requests = []

    async def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["action"] == "read_active":
            status = "awaiting_confirmation" if any(item["action"] == "submit_duration" for item in requests) else "awaiting_duration"
            return httpx.Response(200, json={"conversation": {"status": status}})
        if payload["action"] == "submit_duration":
            return httpx.Response(200, json={
                "prompt": "Start a 5 minute timer?",
                "conversation": {"status": "awaiting_confirmation"},
            })
        if payload["action"] == "confirm":
            return httpx.Response(200, json={
                "prompt": "5 minute timer started.",
                "conversation": {"status": "created"},
                "timer": {"timer_id": "timer-1"},
            })
        raise AssertionError(payload)

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))

    asyncio.run(service.handle_custom_capture({"endpoint_id": "p4", "text": "five minutes", "error": None}))
    assert requests[-1] == {
        "action": "submit_duration",
        "session_id": "timer-session-1",
        "recognized_text": "five minutes",
    }
    assert service.custom_capture_active("p4") is False

    asyncio.run(service.handle_endpoint_event({
        "event_type": "tts.playback.completed",
        "endpoint_id": "p4",
        "session_id": "timer-session-1",
    }))
    assert service.custom_capture_active("p4") is True

    asyncio.run(service.handle_custom_capture({"endpoint_id": "p4", "text": "yes", "error": None}))
    assert requests[-1] == {"action": "confirm", "session_id": "timer-session-1"}
    assert manager.calls[-1] == ("screen_set", {"endpoint_id": "p4", "screen_id": "timer"})
