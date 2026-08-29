from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from hexevoice.config.settings import Settings
from hexevoice.main import create_app
from hexevoice.persistence.speaker_profile_review import SpeakerProfileReviewStore


def _evidence(**overrides):
    payload = {
        "learning_eligible": True,
        "learning_eligibility_reason": "eligible_for_operator_review",
        "speaker_public_id": "speaker_dan",
        "display_name": "Dan",
        "confidence": 0.91,
        "score": 0.91,
        "score_margin": 0.2,
        "confidence_tier": "high",
        "audio_quality": {"warnings": [], "snr_db": 24.0},
        "transcript": {"text": "what is on my calendar", "text_chars": 20},
        "automatic_learning_enabled": False,
        "requires_operator_review": True,
    }
    payload.update(overrides)
    return payload


def test_profile_review_store_queues_privacy_safe_candidates(tmp_path):
    store = SpeakerProfileReviewStore(path=tmp_path / "profile_review.json")

    candidate = store.add_candidate(
        speaker_public_id="speaker_dan",
        display_name="Dan",
        profile_id="profile-dan",
        session_id="session-1",
        endpoint_id="esp-box-1",
        evidence={
            **_evidence(),
            "audio_base64": "raw",
            "embeddings": [{"values": [1, 2, 3]}],
        },
    )

    assert candidate["status"] == "pending"
    assert candidate["evidence"]["speaker_public_id"] == "speaker_dan"
    assert "audio_base64" not in json.dumps(candidate["evidence"])
    assert "embeddings" not in json.dumps(candidate["evidence"])
    assert candidate["sample"] is None


def test_profile_review_store_rejects_low_margin_candidates(tmp_path):
    store = SpeakerProfileReviewStore(path=tmp_path / "profile_review.json")

    with pytest.raises(ValueError, match="profile_learning_candidate_guardrail_failed"):
        store.add_candidate(speaker_public_id="speaker_dan", evidence=_evidence(score_margin=0.01))


def test_profile_review_store_keeps_retained_audio_only_for_one_day_debug_policy(tmp_path):
    store = SpeakerProfileReviewStore(path=tmp_path / "profile_review.json")

    discarded_sample = store.add_candidate(
        speaker_public_id="speaker_dan",
        session_id="session-no-retention",
        evidence=_evidence(),
        sample={"audio_base64": "raw-audio", "raw_audio_policy": "discard_after_metrics"},
    )
    retained_sample = store.add_candidate(
        speaker_public_id="speaker_dan",
        session_id="session-retained",
        evidence=_evidence(),
        sample={"audio_base64": "raw-audio", "raw_audio_policy": "debug_retention_one_day", "sample_rate_hz": 16000},
    )

    assert discarded_sample["sample"] is None
    assert retained_sample["sample"]["audio_base64"] == "raw-audio"
    assert retained_sample["sample"]["raw_audio_policy"] == "debug_retention_one_day"


def test_profile_review_store_marks_rejected_without_mutating_candidate_evidence(tmp_path):
    store = SpeakerProfileReviewStore(path=tmp_path / "profile_review.json")
    candidate = store.add_candidate(speaker_public_id="speaker_dan", evidence=_evidence())

    reviewed = store.mark_reviewed(
        candidate_id=candidate["candidate_id"],
        decision="rejected",
        reviewed_by="operator",
        reason="voice changed today",
    )

    assert reviewed["status"] == "rejected"
    assert reviewed["review"]["decision"] == "rejected"
    assert reviewed["review"]["reviewed_by"] == "operator"
    assert store.list_candidates(status="pending") == []


def test_profile_learning_review_api_lists_rejects_and_blocks_approve_without_retained_audio(tmp_path):
    review_path = tmp_path / "profile_review.json"
    store = SpeakerProfileReviewStore(path=review_path)
    candidate = store.add_candidate(
        speaker_public_id="speaker_dan",
        display_name="Dan",
        profile_id="profile-dan",
        session_id="session-1",
        evidence=_evidence(),
    )
    app = create_app(
        Settings(
            onboarding_state_path=tmp_path / "state.json",
            runtime_dir=tmp_path,
            voice_profile_review_path=review_path,
        )
    )
    client = TestClient(app)

    listed = client.get("/api/speaker-id/profile-learning-candidates?status=pending")
    blocked = client.post(
        f"/api/speaker-id/profile-learning-candidates/{candidate['candidate_id']}/review",
        json={"decision": "approve", "reviewed_by": "operator"},
    )
    rejected = client.post(
        f"/api/speaker-id/profile-learning-candidates/{candidate['candidate_id']}/review",
        json={"decision": "reject", "reviewed_by": "operator", "reason": "operator declined"},
    )

    assert listed.status_code == 200
    assert listed.json()["candidates"][0]["candidate_id"] == candidate["candidate_id"]
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "profile_learning_candidate_has_no_retained_audio"
    assert rejected.status_code == 200
    assert rejected.json()["candidate"]["status"] == "rejected"
