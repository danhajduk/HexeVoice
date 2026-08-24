from __future__ import annotations

from datetime import UTC, datetime
import json
import threading
import time

from hexevoice.config.settings import Settings
from hexevoice.domain_events import (
    AsyncDomainEventPublisher,
    DomainEventPublishDecision,
    HexeMqttTimerCreateEventPublisher,
    domain_event_topic,
    format_duration_hhmmss,
)


def test_domain_event_topic_maps_voice_timer_event_to_node_scope():
    assert (
        domain_event_topic("node-voice-1", "timer.create_requested")
        == "hexe/nodes/node-voice-1/events/timer/create_requested"
    )


def test_format_duration_hhmmss():
    assert format_duration_hhmmss(5) == "00:00:05"
    assert format_duration_hhmmss(300) == "00:05:00"
    assert format_duration_hhmmss(5400) == "01:30:00"
    assert format_duration_hhmmss(90061) == "25:01:01"


def test_timer_event_publisher_uses_hexecore_node_event_contract(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "trust_activation": {
                    "node_id": "node-voice-1",
                    "node_type": "voice-node",
                    "trust_status": "trusted",
                    "operational_mqtt_identity": "hn_node-voice-1",
                    "operational_mqtt_token": "mqtt-token",
                    "operational_mqtt_host": "10.0.0.100",
                    "operational_mqtt_port": 1883,
                },
                "operational_status": {
                    "operational_ready": True,
                },
            }
        )
    )
    captured = {}
    settings = Settings(onboarding_state_path=state_path)
    publisher = HexeMqttTimerCreateEventPublisher(settings=settings)

    def fake_publish(**kwargs):
        publisher._stamp_mqtt_sent(kwargs["payload"], kwargs["request_timestamp"])
        captured.update(kwargs)

    monkeypatch.setattr(publisher, "_publish", fake_publish)

    requested_at = datetime(2026, 5, 4, 1, 58, 0, tzinfo=UTC)
    decision = publisher.publish_timer_create(
        endpoint_id="esp-box-1",
        session_id="session-1",
        heard_text="set a timer for 5 minutes",
        duration_seconds=300,
        duration_text="5 minutes",
        requested_at=requested_at,
    )

    assert decision.status == "published"
    assert captured["host"] == "10.0.0.100"
    assert captured["port"] == 1883
    assert captured["identity"] == "hn_node-voice-1"
    assert captured["token"] == "mqtt-token"
    assert captured["topic"] == "hexe/nodes/node-voice-1/events/timer/create_requested"
    payload = captured["payload"]
    assert payload["schema_version"] == 1
    assert payload["event_type"] == "timer.create_requested"
    assert payload["occurred_at"] == "2026-05-04T01:58:00+00:00"
    assert payload["source"]["node_id"] == "node-voice-1"
    assert payload["source"]["component"] == "hexevoice.assistant.local_intents"
    assert payload["subject"]["family"] == "timer"
    assert payload["data"]["duration_seconds"] == 300
    assert payload["data"]["duration_hhmmss"] == "00:05:00"
    assert payload["data"]["duration_text"] == "5 minutes"
    assert payload["data"]["heard_text"] == "set a timer for 5 minutes"
    assert payload["data"]["requested_at"] == "2026-05-04T01:58:00+00:00"
    assert datetime.fromisoformat(payload["data"]["mqtt_sent_at"]) >= requested_at
    assert payload["data"]["request_to_mqtt_latency_ms"] >= 0


def test_timer_status_publisher_uses_hexecore_node_event_contract(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "trust_activation": {
                    "node_id": "node-voice-1",
                    "node_type": "voice-node",
                    "trust_status": "trusted",
                    "operational_mqtt_identity": "hn_node-voice-1",
                    "operational_mqtt_token": "mqtt-token",
                    "operational_mqtt_host": "10.0.0.100",
                    "operational_mqtt_port": 1883,
                },
                "operational_status": {
                    "operational_ready": True,
                },
            }
        )
    )
    captured = {}
    settings = Settings(onboarding_state_path=state_path)
    publisher = HexeMqttTimerCreateEventPublisher(settings=settings)

    def fake_publish(**kwargs):
        publisher._stamp_mqtt_sent(kwargs["payload"], kwargs["request_timestamp"])
        captured.update(kwargs)

    monkeypatch.setattr(publisher, "_publish", fake_publish)

    requested_at = datetime(2026, 5, 4, 1, 59, 0, tzinfo=UTC)
    decision = publisher.publish_timer_status_request(
        endpoint_id="esp-box-1",
        session_id="session-2",
        heard_text="how much time is left on the timer",
        requested_at=requested_at,
    )

    assert decision.status == "published"
    assert captured["topic"] == "hexe/nodes/node-voice-1/events/timer/status_requested"
    payload = captured["payload"]
    assert payload["schema_version"] == 1
    assert payload["event_type"] == "timer.status_requested"
    assert payload["occurred_at"] == "2026-05-04T01:59:00+00:00"
    assert payload["source"]["node_id"] == "node-voice-1"
    assert payload["subject"] == {"family": "timer", "record_id": "esp-box-1"}
    assert payload["data"]["intent"] == "timer.status"
    assert payload["data"]["endpoint_id"] == "esp-box-1"
    assert payload["data"]["session_id"] == "session-2"
    assert payload["data"]["scope"] == "active_for_endpoint"
    assert payload["data"]["correlation_id"].startswith("timer-status-")
    assert payload["data"]["heard_text"] == "how much time is left on the timer"
    assert payload["data"]["requested_at"] == "2026-05-04T01:59:00+00:00"


def test_timer_control_publisher_uses_hexecore_node_event_contract(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "trust_activation": {
                    "node_id": "node-voice-1",
                    "node_type": "voice-node",
                    "trust_status": "trusted",
                    "operational_mqtt_identity": "hn_node-voice-1",
                    "operational_mqtt_token": "mqtt-token",
                    "operational_mqtt_host": "10.0.0.100",
                    "operational_mqtt_port": 1883,
                },
                "operational_status": {
                    "operational_ready": True,
                },
            }
        )
    )
    captured = {}
    settings = Settings(onboarding_state_path=state_path)
    publisher = HexeMqttTimerCreateEventPublisher(settings=settings)

    def fake_publish(**kwargs):
        publisher._stamp_mqtt_sent(kwargs["payload"], kwargs["request_timestamp"])
        captured.update(kwargs)

    monkeypatch.setattr(publisher, "_publish", fake_publish)

    requested_at = datetime(2026, 5, 4, 2, 1, 0, tzinfo=UTC)
    decision = publisher.publish_timer_control_request(
        action="stop",
        endpoint_id="esp-pe-1",
        session_id="session-stop-1",
        heard_text="stop the timer",
        requested_at=requested_at,
    )

    assert decision.status == "published"
    assert captured["topic"] == "hexe/nodes/node-voice-1/events/timer/stop_requested"
    payload = captured["payload"]
    assert payload["schema_version"] == 1
    assert payload["event_type"] == "timer.stop_requested"
    assert payload["occurred_at"] == "2026-05-04T02:01:00+00:00"
    assert payload["subject"] == {"family": "timer", "record_id": "esp-pe-1"}
    assert payload["data"]["intent"] == "timer.stop"
    assert payload["data"]["action"] == "stop"
    assert payload["data"]["endpoint_id"] == "esp-pe-1"
    assert payload["data"]["session_id"] == "session-stop-1"
    assert payload["data"]["scope"] == "active_for_endpoint"
    assert payload["data"]["correlation_id"].startswith("timer-stop-")
    assert payload["data"]["heard_text"] == "stop the timer"


def test_timer_adjust_publisher_uses_hexecore_node_event_contract(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "trust_activation": {
                    "node_id": "node-voice-1",
                    "node_type": "voice-node",
                    "trust_status": "trusted",
                    "operational_mqtt_identity": "hn_node-voice-1",
                    "operational_mqtt_token": "mqtt-token",
                    "operational_mqtt_host": "10.0.0.100",
                    "operational_mqtt_port": 1883,
                },
                "operational_status": {
                    "operational_ready": True,
                },
            }
        )
    )
    captured = {}
    settings = Settings(onboarding_state_path=state_path)
    publisher = HexeMqttTimerCreateEventPublisher(settings=settings)

    def fake_publish(**kwargs):
        publisher._stamp_mqtt_sent(kwargs["payload"], kwargs["request_timestamp"])
        captured.update(kwargs)

    monkeypatch.setattr(publisher, "_publish", fake_publish)

    requested_at = datetime(2026, 5, 4, 2, 3, 0, tzinfo=UTC)
    decision = publisher.publish_timer_adjust_request(
        endpoint_id="esp-pe-1",
        session_id="session-adjust-1",
        heard_text="remove two minutes from the timer",
        delta_seconds=-120,
        delta_text="2 minutes",
        requested_at=requested_at,
    )

    assert decision.status == "published"
    assert captured["topic"] == "hexe/nodes/node-voice-1/events/timer/adjust_time_requested"
    payload = captured["payload"]
    assert payload["schema_version"] == 1
    assert payload["event_type"] == "timer.adjust_time_requested"
    assert payload["occurred_at"] == "2026-05-04T02:03:00+00:00"
    assert payload["subject"] == {"family": "timer", "record_id": "esp-pe-1"}
    assert payload["data"]["intent"] == "timer.adjust_time"
    assert payload["data"]["endpoint_id"] == "esp-pe-1"
    assert payload["data"]["session_id"] == "session-adjust-1"
    assert payload["data"]["scope"] == "active_for_endpoint"
    assert payload["data"]["delta_seconds"] == -120
    assert payload["data"]["delta_hhmmss"] == "00:02:00"
    assert payload["data"]["delta_text"] == "2 minutes"
    assert payload["data"]["direction"] == "remove"
    assert payload["data"]["correlation_id"].startswith("timer-adjust-")
    assert payload["data"]["heard_text"] == "remove two minutes from the timer"


def test_timer_snooze_publisher_uses_hexecore_node_event_contract(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "trust_activation": {
                    "node_id": "node-voice-1",
                    "node_type": "voice-node",
                    "trust_status": "trusted",
                    "operational_mqtt_identity": "hn_node-voice-1",
                    "operational_mqtt_token": "mqtt-token",
                    "operational_mqtt_host": "10.0.0.100",
                    "operational_mqtt_port": 1883,
                },
                "operational_status": {
                    "operational_ready": True,
                },
            }
        )
    )
    captured = {}
    settings = Settings(onboarding_state_path=state_path)
    publisher = HexeMqttTimerCreateEventPublisher(settings=settings)

    def fake_publish(**kwargs):
        publisher._stamp_mqtt_sent(kwargs["payload"], kwargs["request_timestamp"])
        captured.update(kwargs)

    monkeypatch.setattr(publisher, "_publish", fake_publish)

    requested_at = datetime(2026, 5, 4, 2, 5, 0, tzinfo=UTC)
    decision = publisher.publish_timer_snooze_request(
        endpoint_id="esp-pe-1",
        session_id="session-snooze-1",
        heard_text="snooze the timer for five minutes",
        duration_seconds=300,
        duration_text="5 minutes",
        requested_at=requested_at,
        timer_id="timer-1",
    )

    assert decision.status == "published"
    assert captured["topic"] == "hexe/nodes/node-voice-1/events/timer/snooze_requested"
    payload = captured["payload"]
    assert payload["schema_version"] == 1
    assert payload["event_type"] == "timer.snooze_requested"
    assert payload["occurred_at"] == "2026-05-04T02:05:00+00:00"
    assert payload["subject"] == {"family": "timer", "record_id": "timer-1"}
    assert payload["data"]["intent"] == "timer.snooze"
    assert payload["data"]["endpoint_id"] == "esp-pe-1"
    assert payload["data"]["session_id"] == "session-snooze-1"
    assert payload["data"]["timer_id"] == "timer-1"
    assert payload["data"]["scope"] == "active_for_endpoint"
    assert payload["data"]["duration_seconds"] == 300
    assert payload["data"]["duration_hhmmss"] == "00:05:00"
    assert payload["data"]["duration_text"] == "5 minutes"
    assert payload["data"]["correlation_id"].startswith("timer-snooze-")
    assert payload["data"]["heard_text"] == "snooze the timer for five minutes"


def test_voice_intent_recognized_event_includes_reply_audio_metadata(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "trust_activation": {
                    "node_id": "node-voice-1",
                    "node_type": "voice-node",
                    "trust_status": "trusted",
                    "operational_mqtt_identity": "hn_node-voice-1",
                    "operational_mqtt_token": "mqtt-token",
                    "operational_mqtt_host": "10.0.0.100",
                    "operational_mqtt_port": 1883,
                },
                "operational_status": {
                    "operational_ready": True,
                },
            }
        )
    )
    captured = {}
    settings = Settings(onboarding_state_path=state_path)
    publisher = HexeMqttTimerCreateEventPublisher(settings=settings)

    def fake_publish(**kwargs):
        publisher._stamp_mqtt_sent(kwargs["payload"], kwargs["request_timestamp"])
        captured.update(kwargs)

    monkeypatch.setattr(publisher, "_publish", fake_publish)

    reply_audio = {
        "stream_id": "voice-intent-audio-1",
        "voice_ready": True,
        "audio_url": "http://voice.local/api/tts/audio/voice-intent-audio-1",
        "content_type": "audio/wav",
        "ttl_seconds": 300,
    }
    requested_at = datetime(2026, 5, 4, 1, 58, 0, tzinfo=UTC)

    decision = publisher.publish_voice_intent_recognized(
        event_id="voice-intent-audio-1",
        endpoint_id="box-1",
        session_id="session-1",
        intent_id="timer.create",
        intent_name="Create timer",
        service_id="voice.local_intents",
        version="v1",
        command="timer.create",
        provider_id="registered_intent",
        recognized_text="set a timer for five minutes",
        slots={"duration_seconds": 300},
        reply_text="Setting timer for five minutes.",
        dispatch={"type": "domain_event", "event_type": "timer.create_requested"},
        requested_at=requested_at,
        reply_audio=reply_audio,
        intent_latency_ms=12.5,
    )

    assert decision.status == "published"
    payload = captured["payload"]
    assert payload["event_type"] == "voice.intent.recognized"
    assert payload["data"]["reply_audio"] == reply_audio
    assert payload["data"]["reply_audio"]["audio_url"].endswith("/voice-intent-audio-1")
    assert payload["data"]["intent_latency_ms"] == 12.5
    assert datetime.fromisoformat(payload["data"]["mqtt_sent_at"]) >= requested_at


def test_async_domain_event_publisher_queues_without_blocking():
    published = threading.Event()
    calls: list[dict] = []

    class SlowPublisher:
        def publish_timer_create(self, **payload):
            time.sleep(0.25)
            calls.append({"type": "timer", **payload})
            published.set()
            return DomainEventPublishDecision(status="published", reason="published", event_type="timer.create_requested")

        def publish_timer_status_request(self, **payload):
            time.sleep(0.25)
            calls.append({"type": "timer_status", **payload})
            published.set()
            return DomainEventPublishDecision(status="published", reason="published", event_type="timer.status_requested")

        def publish_timer_control_request(self, **payload):
            time.sleep(0.25)
            calls.append({"type": "timer_control", **payload})
            published.set()
            return DomainEventPublishDecision(
                status="published",
                reason="published",
                event_type=f"timer.{payload['action']}_requested",
            )

        def publish_timer_adjust_request(self, **payload):
            time.sleep(0.25)
            calls.append({"type": "timer_adjust", **payload})
            published.set()
            return DomainEventPublishDecision(status="published", reason="published", event_type="timer.adjust_time_requested")

        def publish_timer_snooze_request(self, **payload):
            time.sleep(0.25)
            calls.append({"type": "timer_snooze", **payload})
            published.set()
            return DomainEventPublishDecision(status="published", reason="published", event_type="timer.snooze_requested")

        def publish_voice_intent_recognized(self, **payload):
            time.sleep(0.25)
            calls.append({"type": "recognition", **payload})
            published.set()
            return DomainEventPublishDecision(
                status="published",
                reason="published",
                event_id=payload["event_id"],
                event_type="voice.intent.recognized",
            )

        def status(self):
            return {"provider": "slow", "enabled": True}

    publisher = AsyncDomainEventPublisher(SlowPublisher())
    started = time.perf_counter()
    decision = publisher.publish_voice_intent_recognized(
        event_id="voice-intent-1",
        endpoint_id="box-1",
        session_id="session-1",
        intent_id="voice.time.query",
        intent_name="What is the time",
        command="voice.time.query",
        provider_id="local_pattern",
        recognized_text="what is the time",
        slots={},
        reply_text="It is 4:10 PM.",
        requested_at=datetime(2026, 5, 9, 23, 10, 40, tzinfo=UTC),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert decision.status == "queued"
    assert decision.reason == "queued_for_async_publish"
    assert elapsed_ms < 100
    assert published.wait(timeout=1)
    assert calls[0]["type"] == "recognition"
    status = publisher.status()
    assert status["async_publish"] is True
    assert status["last_queued_decision"]["status"] == "queued"
    assert status["last_worker_decision"]["status"] == "published"
