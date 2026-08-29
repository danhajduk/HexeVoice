from hexevoice.persistence import VoicePlacementCalibrationStore
from hexevoice.voice import VoiceSessionManager
from hexevoice.voice.placement import (
    PlacementReportInput,
    build_active_placement_report,
    build_long_window_placement_report,
    phrase_similarity,
)


def placement_input(
    *,
    expected_phrase="Hexe turn on the kitchen lights",
    observed_text="Hexe turn on the kitchen lights",
    expected_speaker_public_id=None,
    speaker_identity=None,
    audio_quality=None,
):
    return PlacementReportInput(
        test={
            "test_id": "placement-test",
            "endpoint_id": "esp-box-1",
            "room": "kitchen",
            "expected_phrase": expected_phrase,
            "expected_speaker_public_id": expected_speaker_public_id,
        },
        transcript={
            "text": observed_text,
            "confidence": 0.92,
            "provider_id": "deterministic",
            "model": "deterministic",
            "error": None,
        },
        speaker_identity=speaker_identity,
        audio_quality=audio_quality
        or {
            "status": "ok",
            "warnings": [],
            "snr_db": 22.0,
            "snr_status": "ok",
            "clipping_count": 0,
            "clipping_ratio": 0.0,
            "duration_ms": 1200,
            "source": "endpoint",
        },
        related_reports=[],
    )


def test_phrase_similarity_accepts_minor_wake_word_variation():
    assert phrase_similarity("Hexe turn on the kitchen lights", "Alexa, turn on the kitchen lights") >= 0.82


def test_active_placement_report_scores_successful_sample():
    report = build_active_placement_report(
        placement_input(
            expected_speaker_public_id="speaker_dan",
            speaker_identity={
                "status": "identified",
                "speaker_public_id": "speaker_dan",
                "confidence": 0.91,
            },
        )
    )

    assert report["score"] >= 80
    assert report["recommendation"] == "placement_good"
    assert report["stt"]["matched"] is True
    assert report["speaker_id"]["matched"] is True


def test_active_placement_report_flags_failed_transcript_match():
    report = build_active_placement_report(placement_input(observed_text="turn off the bedroom fan"))

    assert report["stt"]["matched"] is False
    assert "phrase_mismatch" in report["warnings"]
    assert report["score"] < 80


def test_active_placement_report_flags_unknown_expected_speaker():
    report = build_active_placement_report(
        placement_input(
            expected_speaker_public_id="speaker_dan",
            speaker_identity={"status": "unknown", "reason": "low_confidence"},
        )
    )

    assert report["speaker_id"]["matched"] is False
    assert "unknown_speaker" in report["warnings"]


def test_active_placement_report_prioritizes_low_snr_and_clipping_recommendations():
    low_snr = build_active_placement_report(
        placement_input(audio_quality={"status": "low_snr", "warnings": ["low_snr"], "snr_db": 7.0})
    )
    clipped = build_active_placement_report(
        placement_input(audio_quality={"status": "clipped", "warnings": ["clipped"], "clipping_count": 12})
    )

    assert low_snr["recommendation"] == "reduce_background_noise_or_move_closer"
    assert "low_snr" in low_snr["warnings"]
    assert clipped["recommendation"] == "move_endpoint_farther_or_reduce_input_gain"
    assert "clipped" in clipped["warnings"]


def test_long_window_report_combines_passive_ambient_and_active_results():
    report = build_long_window_placement_report(
        window={
            "calibration_id": "placement-cal",
            "endpoint_id": "esp-box-1",
            "room": "kitchen",
            "zone": "north",
        },
        passive_samples=[
            {
                "observed_at": "2026-08-29T08:00:00+00:00",
                "metrics": {"ambient_rms": 0.02, "peak": 0.1, "clipping_ratio": 0.0, "speech_like_activity": False},
            },
            {
                "observed_at": "2026-08-29T09:00:00+00:00",
                "metrics": {
                    "ambient_rms": 0.09,
                    "peak": 0.92,
                    "clipping_ratio": 0.08,
                    "speech_like_activity": True,
                    "snr_db": 12,
                },
            },
        ],
        active_reports=[
            {
                "stt": {"matched": True},
                "speaker_id": {"matched": True},
                "audio_quality": {"snr_db": 20},
            }
        ],
    )

    assert report["sample_count"] == 2
    assert report["active_test_count"] == 1
    assert report["ambient"]["average_rms"] == 0.055
    assert report["snr"]["count"] == 2
    assert "elevated_ambient_noise" in report["warnings"]
    assert report["privacy"]["stt_called_for_passive_samples"] is False


def test_passive_calibration_store_keeps_samples_metric_only_and_cleans_retention(tmp_path):
    store = VoicePlacementCalibrationStore(path=tmp_path / "placement-calibrations.json")
    window = store.start_window(
        endpoint_id="esp-pe-1",
        room="kitchen",
        duration_hours=24,
        sample_interval_seconds=600,
        retention_days=1,
        debug_record_audio=True,
    )

    sample = store.record_sample(
        calibration_id=window["calibration_id"],
        observed_at="2026-08-27T08:00:00+00:00",
        metrics={
            "ambient_rms": 0.02,
            "peak": 0.1,
            "raw_audio": "ignored",
            "transcript": "ignored",
        },
    )

    assert sample["privacy"]["metrics_only"] is True
    assert sample["privacy"]["raw_audio"]["persisted"] is False
    assert sample["privacy"]["stt"]["called"] is False
    assert sample["privacy"]["speaker_id"]["called"] is False
    assert "raw_audio" not in sample["metrics"]
    assert "transcript" not in sample["metrics"]
    assert store.status()["sample_count"] == 0


def test_passive_calibration_manager_does_not_call_unattended_stt_or_speaker_id(tmp_path):
    class TrapPipeline:
        def transcribe_audio(self, *_args, **_kwargs):
            raise AssertionError("passive calibration must not call STT")

        def identify_speaker(self, *_args, **_kwargs):
            raise AssertionError("passive calibration must not call Speaker ID")

    store = VoicePlacementCalibrationStore(path=tmp_path / "placement-calibrations.json")
    manager = VoiceSessionManager(turn_pipeline=TrapPipeline(), placement_calibration_store=store)
    window = manager.start_passive_placement_calibration(endpoint_id="esp-pe-1", room="kitchen")
    sample = manager.record_passive_placement_sample(
        calibration_id=window["calibration_id"],
        metrics={"ambient_rms": 0.02, "peak": 0.1, "speech_like_activity": False},
    )

    assert sample["privacy"]["stt"]["called"] is False
    assert sample["privacy"]["speaker_id"]["called"] is False
