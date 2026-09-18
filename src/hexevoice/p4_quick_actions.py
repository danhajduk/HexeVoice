from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import logging
from typing import Any, Awaitable, Callable

import httpx

from hexevoice.weather_snapshots import WeatherSnapshotService, parse_timestamp
from hexevoice.radar_assets import RadarAssetService


log = logging.getLogger(__name__)

BUTTON_INTENTS = {
    "button_timer": {"intent_id": "timer.new", "text": "new timer"},
    "button_weather": {"intent_id": "weather.current", "text": "what is the current weather"},
    "button_config": {"intent_id": "endpoint.settings.open", "text": "open settings"},
}


class P4QuickActionService:
    def __init__(
        self,
        *,
        manager: Any,
        weather: WeatherSnapshotService,
        radar_assets: RadarAssetService,
        radar_asset_url: Any,
        interaction_api_base_url: str,
        intent_invoker: Callable[..., Awaitable[dict[str, Any]]],
    ) -> None:
        self._manager = manager
        self._weather = weather
        self._radar_assets = radar_assets
        self._radar_asset_url = radar_asset_url
        self._interaction_api_base_url = interaction_api_base_url.rstrip("/")
        self._intent_invoker = intent_invoker
        self._timer_sessions: dict[str, str] = {}
        self._timer_prompt_playback: dict[str, tuple[str, bool]] = {}
        self._pending_weather_results: dict[str, dict[str, Any]] = {}
        self._status: dict[str, Any] = {"last_action": None, "last_error": None}
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._weather.add_listener(self._on_weather_snapshot)
        self._weather.add_result_listener(self._on_weather_result)

    def stop(self) -> None:
        self._weather.remove_listener(self._on_weather_snapshot)
        self._weather.remove_result_listener(self._on_weather_result)
        self._loop = None

    def _on_weather_snapshot(self, snapshot: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._prepare_radar_for_connected_endpoints(snapshot), loop)
        for endpoint_id, result in tuple(self._pending_weather_results.items()):
            if self._snapshot_matches_result(snapshot, result):
                self._pending_weather_results.pop(endpoint_id, None)
                asyncio.run_coroutine_threadsafe(self._show_weather(endpoint_id), loop)

    def _on_weather_result(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._handle_weather_result(event), loop)

    async def _handle_weather_result(self, event: dict[str, Any]) -> None:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        endpoint_id = str(data.get("endpoint_id") or "").strip()
        if not endpoint_id:
            return
        if endpoint_id not in self._manager.connected_endpoint_ids(
            board_profile="waveshare_p4_wifi6_touch_lcd_7b"
        ):
            return
        if event.get("event_type") == "weather.current_failed":
            self._pending_weather_results.pop(endpoint_id, None)
            await self._show_message(
                endpoint_id,
                "Weather unavailable",
                str(data.get("message") or "Current weather is unavailable."),
                "weather",
            )
            return
        snapshot = self._weather.prepared_snapshot()
        if snapshot is not None and self._snapshot_matches_result(snapshot, data):
            self._pending_weather_results.pop(endpoint_id, None)
            await self._show_weather(endpoint_id)
            return
        self._pending_weather_results[endpoint_id] = deepcopy(data)

    @staticmethod
    def _snapshot_matches_result(snapshot: dict[str, Any], result: dict[str, Any]) -> bool:
        return (
            str(snapshot.get("snapshot_id") or "") == str(result.get("snapshot_id") or "")
            and str(snapshot.get("cache_revision") or "") == str(result.get("snapshot_revision") or "")
        )

    async def _prepare_radar_for_connected_endpoints(self, snapshot: dict[str, Any]) -> None:
        radar = snapshot.get("radar") if isinstance(snapshot.get("radar"), dict) else {}
        if radar.get("status") != "ready":
            return
        try:
            asset = await self._radar_assets.prepare(snapshot)
            prepared = asset.as_dict(download_url=self._radar_asset_url(asset.asset_id))
            prepared["expires_at"] = radar.get("expires_at")
            for endpoint_id in self._manager.connected_endpoint_ids(
                board_profile="waveshare_p4_wifi6_touch_lcd_7b"
            ):
                await self._manager.push_asset_prepare_command(endpoint_id=endpoint_id, asset=prepared)
        except Exception as exc:
            self._status["last_error"] = str(exc)
            log.warning("P4 radar preparation failed: error=%s", exc)

    async def handle_button(self, event: dict[str, Any]) -> None:
        endpoint_id = str(event.get("endpoint_id") or "").strip()
        button_id = str(event.get("button_id") or "").strip()
        screen_id = str(event.get("screen_id") or "").strip()
        if not endpoint_id:
            return
        try:
            declaration = BUTTON_INTENTS.get(button_id)
            if declaration is None:
                return
            declared_intent_id = str(event.get("intent_id") or "").strip()
            if declared_intent_id != declaration["intent_id"]:
                raise RuntimeError("button_intent_declaration_mismatch")
            invocation = await self._intent_invoker(
                endpoint_id=endpoint_id,
                intent_id=declaration["intent_id"],
                text=declaration["text"],
            )
            if not invocation.get("matched") or invocation.get("intent_id") != declaration["intent_id"]:
                raise RuntimeError(str(invocation.get("reason") or "declared_button_intent_not_available"))
            if button_id == "button_weather":
                self._pending_weather_results.setdefault(endpoint_id, {})
            elif button_id == "button_timer":
                await self._begin_timer(endpoint_id)
            self._status = {
                "last_action": {
                    "endpoint_id": endpoint_id,
                    "screen_id": screen_id,
                    "button_id": button_id,
                    "intent_id": declaration["intent_id"],
                },
                "last_error": None,
            }
        except Exception as exc:
            self._status["last_error"] = str(exc)
            log.warning("P4 quick action failed: endpoint_id=%s button_id=%s error=%s", endpoint_id, button_id, exc)

    def status(self) -> dict[str, Any]:
        return deepcopy(self._status)

    async def handle_endpoint_event(self, event: dict[str, Any]) -> None:
        if event.get("event_type") != "tts.playback.completed":
            return
        endpoint_id = str(event.get("endpoint_id") or "")
        session_id = str(event.get("session_id") or "")
        pending = self._timer_prompt_playback.get(endpoint_id)
        if pending is None or pending[0] != session_id:
            return
        self._timer_prompt_playback.pop(endpoint_id, None)
        if pending[1]:
            await self._manager.push_listen_command(
                endpoint_id=endpoint_id,
                reason="timer_quick_action_capture",
            )

    def custom_capture_active(self, endpoint_id: str) -> bool:
        return endpoint_id in self._timer_sessions and endpoint_id not in self._timer_prompt_playback

    async def handle_custom_capture(self, capture: dict[str, Any]) -> None:
        endpoint_id = str(capture.get("endpoint_id") or "")
        interaction_session_id = self._timer_sessions.get(endpoint_id)
        if not interaction_session_id:
            return
        recognized_text = str(capture.get("text") or "").strip()
        if capture.get("error") or not recognized_text:
            await self._continue_timer_prompt(endpoint_id, interaction_session_id, "I didn't understand the timer duration.")
            return
        state = await self._timer_action(
            endpoint_id,
            {"action": "read_active", "session_id": interaction_session_id},
        )
        conversation = state.get("conversation") if isinstance(state.get("conversation"), dict) else {}
        status = conversation.get("status")
        if status in {"awaiting_duration", "error"}:
            result = await self._timer_action(
                endpoint_id,
                {
                    "action": "submit_duration",
                    "session_id": interaction_session_id,
                    "recognized_text": recognized_text,
                },
            )
            await self._continue_timer_prompt(
                endpoint_id,
                interaction_session_id,
                str(result.get("prompt") or "Should I start that timer?"),
            )
            return
        if status == "awaiting_confirmation":
            normalized = recognized_text.lower().strip(" .!?")
            yes = {"yes", "yeah", "yep", "confirm", "start", "start it"}
            no = {"no", "nope", "cancel", "stop", "never mind", "nevermind"}
            if normalized not in yes | no:
                await self._continue_timer_prompt(endpoint_id, interaction_session_id, "Should I start that timer?")
                return
            action = "confirm" if normalized in yes else "cancel"
            result = await self._timer_action(
                endpoint_id,
                {"action": action, "session_id": interaction_session_id},
            )
            self._timer_sessions.pop(endpoint_id, None)
            prompt = str(result.get("prompt") or ("Timer started." if action == "confirm" else "Timer creation cancelled."))
            await self._manager.push_play_sound_command(
                endpoint_id=endpoint_id,
                text=prompt,
                session_id=interaction_session_id,
            )
            if action == "confirm":
                await self._manager.push_ui_screen_set_command(endpoint_id=endpoint_id, screen_id="timer")
            else:
                await self._manager.push_ui_screen_clear_command(endpoint_id=endpoint_id)

    async def _continue_timer_prompt(self, endpoint_id: str, session_id: str, prompt: str) -> None:
        await self._manager.push_ui_layout_command(
            endpoint_id=endpoint_id,
            layout=self._layout(
                "timer_quick",
                [
                    ("New timer", 180, 36, "#55B8FF"),
                    (prompt, 315, 30, "#D8FFFA"),
                    ("Say yes or no", 405, 22, "#35F4DB"),
                ],
            ),
            duration_seconds=60,
        )
        await self._manager.push_play_sound_command(endpoint_id=endpoint_id, text=prompt, session_id=session_id)
        self._timer_prompt_playback[endpoint_id] = (session_id, True)

    async def _show_weather(self, endpoint_id: str) -> None:
        snapshot = self._weather.prepared_snapshot()
        if snapshot is None:
            await self._show_message(endpoint_id, "Weather unavailable", "No prepared forecast is available.", "weather")
            return
        current = snapshot.get("current_conditions") if isinstance(snapshot.get("current_conditions"), dict) else {}
        forecast = snapshot.get("forecast_summary") if isinstance(snapshot.get("forecast_summary"), dict) else {}
        location = snapshot.get("location") if isinstance(snapshot.get("location"), dict) else {}
        temperature = current.get("temperature")
        temperature_text = f"{temperature} degrees" if temperature is not None else "Temperature unavailable"
        condition = str(current.get("condition") or forecast.get("summary") or "Current weather")
        layout = self._layout(
            "weather",
            [
                (str(location.get("label") or "Weather"), 170, 32, "#55B8FF"),
                (temperature_text, 275, 64, "#D8FFFA"),
                (condition, 365, 30, "#35F4DB"),
                ("Tap weather again for radar" if self._radar_ready() else "Radar is not currently available", 465, 20, "#A9BBC8"),
            ],
        )
        await self._manager.push_ui_layout_command(endpoint_id=endpoint_id, layout=layout, duration_seconds=60)
        tts = snapshot.get("tts") if isinstance(snapshot.get("tts"), dict) else {}
        variants = tts.get("variants") if isinstance(tts.get("variants"), dict) else {}
        high = variants.get("high") if isinstance(variants.get("high"), dict) else {}
        standard = variants.get("standard") if isinstance(variants.get("standard"), dict) else {}
        compact = variants.get("compact") if isinstance(variants.get("compact"), dict) else {}
        audio_url = high.get("audio_url") or standard.get("audio_url") or compact.get("audio_url") or tts.get("audio_url")
        if tts.get("status") == "ready" and audio_url:
            await self._manager.push_play_sound_command(
                endpoint_id=endpoint_id,
                audio_url=str(audio_url),
                stream_id=str(tts.get("revision") or snapshot.get("snapshot_id") or "weather"),
                content_type="audio/wav",
                text=str(snapshot.get("transcript") or "") or None,
                source_event_id=str(snapshot.get("snapshot_id") or "") or None,
            )

    async def _show_radar(self, endpoint_id: str) -> None:
        snapshot = self._weather.prepared_snapshot() or {}
        radar = snapshot.get("radar") if isinstance(snapshot.get("radar"), dict) else {}
        if radar.get("status") != "ready":
            await self._show_message(endpoint_id, "Radar unavailable", "The latest weather remains available.", "radar")
            return
        observed = str(radar.get("observation_time") or radar.get("fetched_at") or "")
        age = _age_text(observed)
        attribution = radar.get("attribution") if isinstance(radar.get("attribution"), list) else []
        asset = await self._radar_assets.prepare(snapshot)
        prepared = asset.as_dict(download_url=self._radar_asset_url(asset.asset_id))
        prepared["expires_at"] = radar.get("expires_at")
        result = await self._manager.push_asset_prepare_command(endpoint_id=endpoint_id, asset=prepared)
        if not result.get("accepted"):
            raise RuntimeError(str(result.get("reason") or "radar_asset_prepare_rejected"))
        layout = self._layout(
            "radar",
            [
                ("Latest rain radar", 145, 34, "#55B8FF"),
                (f"Observed {age}", 205, 22, "#35F4DB"),
                (" | ".join(str(item) for item in attribution)[:90] or "Provider attribution unavailable", 540, 18, "#A9BBC8"),
            ],
        )
        layout["elements"].insert(
            0,
            {
                "type": "image",
                "item": "weather_radar",
                "asset_id": asset.asset_id,
                "x": asset.x,
                "y": asset.y,
                "width": asset.width,
                "height": asset.height,
            },
        )
        await self._manager.push_ui_layout_command(endpoint_id=endpoint_id, layout=layout, duration_seconds=60)

    async def _begin_timer(self, endpoint_id: str) -> None:
        state = await self._timer_action(endpoint_id, {"action": "begin_custom"})
        conversation = state.get("conversation") if isinstance(state.get("conversation"), dict) else {}
        session_id = str(conversation.get("session_id") or "")
        if session_id:
            self._timer_sessions[endpoint_id] = session_id
        prompt = str(state.get("prompt") or "How long should the timer be?")
        await self._manager.push_ui_layout_command(
            endpoint_id=endpoint_id,
            layout=self._layout(
                "timer_quick",
                [
                    ("New timer", 180, 36, "#55B8FF"),
                    (prompt, 315, 32, "#D8FFFA"),
                    ("Speak the duration", 405, 22, "#35F4DB"),
                ],
            ),
            duration_seconds=60,
        )
        await self._manager.push_play_sound_command(endpoint_id=endpoint_id, text=prompt, session_id=session_id or None)
        if session_id:
            self._timer_prompt_playback[endpoint_id] = (session_id, True)

    async def _timer_action(self, endpoint_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._interaction_api_base_url}/api/interaction/devices/{endpoint_id}/timer-quick-actions"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("invalid_timer_quick_action_response")
        return result

    async def _show_message(self, endpoint_id: str, title: str, message: str, screen_id: str) -> None:
        await self._manager.push_ui_layout_command(
            endpoint_id=endpoint_id,
            layout=self._layout(screen_id, [(title, 230, 36, "#55B8FF"), (message, 330, 24, "#D8FFFA")]),
            duration_seconds=30,
        )

    def _radar_ready(self) -> bool:
        snapshot = self._weather.prepared_snapshot() or {}
        radar = snapshot.get("radar") if isinstance(snapshot.get("radar"), dict) else {}
        if radar.get("status") != "ready" or not radar.get("image_url"):
            return False
        try:
            return parse_timestamp(radar.get("expires_at"), "radar.expires_at") > datetime.now(UTC)
        except Exception:
            return False

    @staticmethod
    def _layout(screen_id: str, lines: list[tuple[str, int, int, str]]) -> dict[str, Any]:
        return {
            "id": screen_id,
            "owner": "backend",
            "sidebars": True,
            "buttons": [
                {"id": button_id, "intent_id": declaration["intent_id"]}
                for button_id, declaration in BUTTON_INTENTS.items()
            ],
            "elements": [
                {"type": "clock", "item": "header_clock", "color": "#35F4DB"},
                *[
                    {
                        "type": "text",
                        "item": f"{screen_id}_line_{index}",
                        "text": text[:95],
                        "x": 512,
                        "y": y,
                        "font": "manrope/date_32.hxf" if size >= 28 else "manrope/message_20.hxf",
                        "font_size": size,
                        "color": color,
                        "align": "center",
                    }
                    for index, (text, y, size, color) in enumerate(lines)
                ],
            ],
        }


def _age_text(timestamp: str) -> str:
    try:
        seconds = max(0, int((datetime.now(UTC) - parse_timestamp(timestamp, "radar.observation_time")).total_seconds()))
    except Exception:
        return "time unknown"
    if seconds < 60:
        return "just now"
    return f"{seconds // 60} min ago"
