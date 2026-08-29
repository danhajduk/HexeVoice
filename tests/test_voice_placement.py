from hexevoice.voice.placement import PlacementReportInput, build_active_placement_report, phrase_similarity


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
