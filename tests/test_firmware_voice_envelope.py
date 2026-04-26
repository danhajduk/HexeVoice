from pathlib import Path


FIRMWARE_BACKEND_CLIENT = Path("firmware/main/voice/backend_client.cpp")


def test_firmware_voice_events_emit_full_v1_envelope():
    source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert "kVoiceEventSchemaVersion" in source
    assert "append_event_header" in source
    assert '"event_id"' in source
    assert '"schema_version"' in source
    assert '"timestamp"' in source
    assert "session.start" in source
    assert "audio.chunk" in source
    assert "audio.end" in source
    assert "session.cancel" in source
    assert "command.ack" in source
    assert "command.error" in source


def test_firmware_heartbeat_reports_network_metadata():
    source = FIRMWARE_BACKEND_CLIENT.read_text()

    assert "ip_address" in source
    assert "rssi_dbm" in source
    assert "current_ip_address()" in source
    assert "wifi_rssi" in source
