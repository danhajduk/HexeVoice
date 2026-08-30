from datetime import UTC, datetime, timedelta

from hexevoice.voice import WakeCandidate, WakeCandidateElection, choose_wake_election_winner


def test_wake_election_accepts_single_candidate():
    started_at = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    election = WakeCandidateElection(window_ms=250)
    candidate = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.72,
        received_at=started_at,
    )

    decision = election.submit_candidate(candidate)

    assert decision.accepted is True
    assert decision.reason == "winner_selected"
    assert decision.winner == candidate


def test_wake_election_selects_highest_confidence_endpoint_candidate():
    started_at = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    low_confidence = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.72,
        received_at=started_at,
    )
    high_confidence = WakeCandidate(
        endpoint_id="esp-pe-1",
        session_id="session-2",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.91,
        received_at=started_at + timedelta(milliseconds=80),
    )

    winner = choose_wake_election_winner([low_confidence, high_confidence])

    assert winner == high_confidence


def test_wake_election_uses_audio_quality_as_near_tie_breaker():
    started_at = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    louder_but_noisy = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.83,
        received_at=started_at,
        speech_peak_level=2600,
        noise_floor_level=2400,
        snr_db=2.0,
    )
    clearer = WakeCandidate(
        endpoint_id="esp-pe-1",
        session_id="session-2",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.82,
        received_at=started_at + timedelta(milliseconds=40),
        speech_peak_level=5200,
        noise_floor_level=300,
        snr_db=18.0,
    )

    winner = choose_wake_election_winner([louder_but_noisy, clearer])

    assert winner == clearer
    assert clearer.score() > louder_but_noisy.score()


def test_wake_election_prefers_endpoint_candidate_when_confidence_ties_backend_backup():
    started_at = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    backend = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="backend_openwakeword",
        model="hexe",
        confidence=0.88,
        received_at=started_at,
    )
    endpoint = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.88,
        received_at=started_at + timedelta(milliseconds=50),
    )

    winner = choose_wake_election_winner([backend, endpoint])

    assert winner == endpoint


def test_wake_election_replaces_provisional_winner_inside_active_window():
    started_at = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    election = WakeCandidateElection(window_ms=350)
    first = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.83,
        received_at=started_at,
    )
    second = WakeCandidate(
        endpoint_id="esp-pe-1",
        session_id="session-2",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.95,
        received_at=started_at + timedelta(milliseconds=100),
    )

    first_decision = election.submit_candidate(first)
    second_decision = election.submit_candidate(second)

    assert first_decision.accepted is True
    assert first_decision.winner == first
    assert second_decision.accepted is True
    assert second_decision.reason == "winner_replaced"
    assert second_decision.winner == second
    assert second_decision.replaced_winner == first


def test_wake_election_stands_down_lower_rank_candidate_inside_active_window():
    started_at = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    election = WakeCandidateElection(window_ms=350)
    first = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.95,
        received_at=started_at,
    )
    second = WakeCandidate(
        endpoint_id="esp-pe-1",
        session_id="session-2",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.83,
        received_at=started_at + timedelta(milliseconds=100),
    )

    first_decision = election.submit_candidate(first)
    second_decision = election.submit_candidate(second)

    assert first_decision.accepted is True
    assert second_decision.accepted is False
    assert second_decision.reason == "stand_down_existing_winner"
    assert second_decision.winner == first


def test_wake_election_accepts_late_candidate_after_window_closes():
    started_at = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    election = WakeCandidateElection(window_ms=250)
    first = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.83,
        received_at=started_at,
    )
    late = WakeCandidate(
        endpoint_id="esp-pe-1",
        session_id="session-2",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.95,
        received_at=started_at + timedelta(milliseconds=400),
    )

    first_decision = election.submit_candidate(first)
    late_decision = election.submit_candidate(late)

    assert first_decision.accepted is True
    assert late_decision.accepted is True
    assert late_decision.winner == late


def test_wake_election_status_expires_stale_pending_candidate():
    started_at = datetime.now(UTC) - timedelta(seconds=1)
    election = WakeCandidateElection(window_ms=10)
    candidate = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.83,
        received_at=started_at,
    )

    election.submit_candidate(candidate)

    status = election.status()

    assert status["active_decision"] is None
    assert status["pending_candidate_count"] == 0
    assert len(status["recent_candidates"]) == 1


def test_wake_election_handles_missing_metrics_without_raw_audio():
    candidate = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=None,
    )

    decision = WakeCandidateElection(window_ms=250).decide_candidates([candidate])

    assert decision.accepted is True
    assert decision.winner == candidate
    assert decision.winner.score() == 0.0
    assert decision.model_dump()["winner"]["score_breakdown"]["metrics_present"]["confidence"] is False


def test_wake_election_keeps_disconnected_loser_candidate_diagnostic():
    started_at = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    connected = WakeCandidate(
        endpoint_id="esp-box-1",
        session_id="session-1",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.9,
        received_at=started_at,
        metadata={"endpoint_connected": True},
    )
    disconnected_loser = WakeCandidate(
        endpoint_id="esp-pe-1",
        session_id="session-2",
        source="endpoint_micro_wake_word",
        model="alexa",
        confidence=0.62,
        received_at=started_at + timedelta(milliseconds=20),
        metadata={"endpoint_connected": False},
    )

    decision = WakeCandidateElection(window_ms=250).decide_candidates([connected, disconnected_loser])

    assert decision.winner == connected
    assert decision.model_dump()["candidates"][1]["metadata"]["endpoint_connected"] is False
