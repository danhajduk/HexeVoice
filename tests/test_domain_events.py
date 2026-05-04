from __future__ import annotations

from datetime import UTC, datetime
import json

from hexevoice.config.settings import Settings
from hexevoice.domain_events import HexeMqttTimerCreateEventPublisher, domain_event_topic, format_duration_hhmmss


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
