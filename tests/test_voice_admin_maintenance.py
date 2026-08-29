from __future__ import annotations

from datetime import UTC, datetime
import json

from hexevoice.persistence.voice_admin_maintenance import VoiceAdminMaintenanceStore
from hexevoice.persistence.voice_admin_maintenance import extract_spoken_passcode
from hexevoice.persistence.voice_admin_maintenance import redact_spoken_passcodes


def _admin_speaker() -> dict[str, object]:
    return {
        "status": "identified",
        "speaker_public_id": "speaker-admin",
        "confidence": 0.98,
        "score_margin": 0.22,
        "admin_eligible": True,
    }


def _audio_quality(warnings: list[str] | None = None) -> dict[str, object]:
    return {
        "warnings": warnings or [],
        "snr_db": 22.0,
        "rms_dbfs": -26.0,
    }


def _enabled_store(tmp_path):
    store = VoiceAdminMaintenanceStore(path=tmp_path / "admin_maintenance.json")
    store.set_passcode("1234")
    store.update_settings(
        enabled=True,
        admin_speaker_public_ids=["speaker-admin"],
        enabled_intents={"admin.debug.start": True},
    )
    return store


def test_voice_admin_maintenance_store_defaults_disabled_and_redacts_status(tmp_path):
    store = VoiceAdminMaintenanceStore(path=tmp_path / "admin_maintenance.json")

    status = store.status()

    assert status["enabled"] is False
    assert status["passcode_configured"] is False
    assert status["enabled_intents"]["admin.debug.start"] is False

    store.set_passcode("1234")
    payload = json.loads((tmp_path / "admin_maintenance.json").read_text())

    assert store.status()["passcode_configured"] is True
    assert payload["passcode_hash"]["algorithm"] == "pbkdf2_sha256"
    assert "1234" not in (tmp_path / "admin_maintenance.json").read_text()


def test_spoken_passcode_extracts_numeric_and_word_forms_and_redacts():
    assert extract_spoken_passcode("admin debug start passcode 1234") == "1234"
    assert extract_spoken_passcode("admin debug start code one two three four") == "1234"
    assert redact_spoken_passcodes("admin debug start passcode 1234") == "admin debug start passcode [passcode]"
    assert redact_spoken_passcodes("code one two three four") == "code [passcode]"


def test_voice_admin_maintenance_allows_only_strong_admin_speaker_and_good_audio(tmp_path):
    store = _enabled_store(tmp_path)

    decision = store.evaluate(
        text="admin debug start passcode 1234",
        intent_id="admin.debug.start",
        speaker_identity=_admin_speaker(),
        audio_quality=_audio_quality(),
    )

    assert decision.allowed is True
    assert decision.reason == "admin_maintenance_allowed"

    low_confidence = {**_admin_speaker(), "confidence": 0.9}
    assert (
        store.evaluate(
            text="admin debug start passcode 1234",
            intent_id="admin.debug.start",
            speaker_identity=low_confidence,
            audio_quality=_audio_quality(),
        ).reason
        == "admin_speaker_confidence_too_low"
    )
    assert (
        store.evaluate(
            text="admin debug start passcode 1234",
            intent_id="admin.debug.start",
            speaker_identity=_admin_speaker(),
            audio_quality=_audio_quality(["low_snr"]),
        ).reason
        == "admin_audio_quality_low_snr"
    )


def test_voice_admin_maintenance_wrong_passcode_locks_after_three_attempts(tmp_path):
    store = _enabled_store(tmp_path)
    now = datetime(2026, 8, 29, 18, 0, tzinfo=UTC)

    first = store.evaluate(
        text="admin debug start passcode 0000",
        intent_id="admin.debug.start",
        speaker_identity=_admin_speaker(),
        audio_quality=_audio_quality(),
        now=now,
    )
    second = store.evaluate(
        text="admin debug start passcode 0000",
        intent_id="admin.debug.start",
        speaker_identity=_admin_speaker(),
        audio_quality=_audio_quality(),
        now=now,
    )
    third = store.evaluate(
        text="admin debug start passcode 0000",
        intent_id="admin.debug.start",
        speaker_identity=_admin_speaker(),
        audio_quality=_audio_quality(),
        now=now,
    )

    assert first.reason == "admin_passcode_wrong"
    assert second.reason == "admin_passcode_wrong"
    assert third.reason == "admin_maintenance_locked"
    assert third.locked_until is not None
