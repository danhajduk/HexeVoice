import json
from pathlib import Path


EVENT_SCHEMA_DIR = Path("docs/events-schemsa")


def test_event_schemas_are_valid_json_documents():
    schema_paths = sorted(EVENT_SCHEMA_DIR.glob("*.schema.json"))

    assert {path.name for path in schema_paths} == {
        "speaker-id-common.schema.json",
        "speaker-id-event.schema.json",
        "timer-common.schema.json",
        "timer-request-event.schema.json",
        "timer-response-event.schema.json",
    }
    for path in schema_paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(f"/docs/events-schemsa/{path.name}")


def test_timer_request_schema_lists_published_event_types():
    schema = json.loads((EVENT_SCHEMA_DIR / "timer-request-event.schema.json").read_text(encoding="utf-8"))

    assert schema["properties"]["event_type"]["enum"] == [
        "timer.create_requested",
        "timer.status_requested",
        "timer.stop_requested",
        "timer.cancel_requested",
        "timer.adjust_time_requested",
        "timer.snooze_requested",
    ]
    base_required = schema["$defs"]["base_request_data"]["required"]
    assert "correlation_id" not in base_required
    assert "correlation_id" in schema["$defs"]["status_request_data"]["allOf"][1]["required"]
    assert "correlation_id" in schema["$defs"]["control_request_data"]["allOf"][1]["required"]
    assert "correlation_id" in schema["$defs"]["adjust_request_data"]["allOf"][1]["required"]
    assert "correlation_id" in schema["$defs"]["snooze_request_data"]["allOf"][1]["required"]
    adjust_props = schema["$defs"]["adjust_request_data"]["allOf"][1]["properties"]
    assert adjust_props["delta_seconds"]["not"]["const"] == 0
    assert adjust_props["direction"]["enum"] == ["add", "remove"]
    snooze_props = schema["$defs"]["snooze_request_data"]["allOf"][1]["properties"]
    assert snooze_props["duration_seconds"]["minimum"] == 1


def test_timer_response_schema_lists_required_response_events():
    schema = json.loads((EVENT_SCHEMA_DIR / "timer-response-event.schema.json").read_text(encoding="utf-8"))

    assert set(schema["properties"]["event_type"]["enum"]) >= {
        "timer.create_succeeded",
        "timer.status_succeeded",
        "timer.stop_succeeded",
        "timer.cancel_succeeded",
        "timer.adjust_time_succeeded",
        "timer.create_failed",
        "timer.status_failed",
        "timer.stop_failed",
        "timer.cancel_failed",
        "timer.adjust_time_failed",
        "timer.completed",
    }
    data_props = schema["$defs"]["response_data"]["properties"]
    assert "timers" in data_props
    assert data_props["alarm_status"]["enum"] == ["idle", "pending", "playing", "acknowledged", "stopped", "failed"]


def test_timer_snapshot_allows_parent_endpoint_on_status_lists():
    schema = json.loads((EVENT_SCHEMA_DIR / "timer-common.schema.json").read_text(encoding="utf-8"))

    assert schema["$defs"]["timer_snapshot"]["required"] == ["timer_id"]


def test_speaker_id_event_schema_lists_required_event_types():
    schema = json.loads((EVENT_SCHEMA_DIR / "speaker-id-event.schema.json").read_text(encoding="utf-8"))

    assert schema["properties"]["event_type"]["enum"] == [
        "speaker.enrollment.started",
        "speaker.enrollment.completed",
        "speaker.enrollment.failed",
        "speaker.identified",
        "speaker.verification.completed",
        "speaker.profile.deleted",
        "speaker.unknown",
        "speaker.low_confidence",
    ]
    assert schema["properties"]["privacy"]["$ref"] == "speaker-id-common.schema.json#/$defs/privacy"
    identified_required = schema["$defs"]["identified_data"]["allOf"][3]["required"]
    assert "endpoint_id" in identified_required
    assert "session_id" in identified_required
    assert "confidence" in identified_required


def test_speaker_id_common_schema_forbids_biometric_payload_leakage_flags():
    schema = json.loads((EVENT_SCHEMA_DIR / "speaker-id-common.schema.json").read_text(encoding="utf-8"))

    privacy = schema["$defs"]["privacy"]
    assert privacy["required"] == ["classification", "redaction", "contains_biometric_template", "contains_raw_audio"]
    assert privacy["properties"]["classification"]["const"] == "biometric"
    assert privacy["properties"]["contains_biometric_template"]["const"] is False
    assert privacy["properties"]["contains_raw_audio"]["const"] is False
