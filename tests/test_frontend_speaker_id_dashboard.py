from pathlib import Path


def test_speaker_id_dashboard_is_routed_and_calls_api_helpers():
    app_source = Path("frontend/src/App.jsx").read_text(encoding="utf-8")
    sidebar_source = Path("frontend/src/features/dashboard/cards/DashboardSidebarCard.jsx").read_text(encoding="utf-8")
    api_source = Path("frontend/src/api/client.js").read_text(encoding="utf-8")
    dashboard_source = Path("frontend/src/features/dashboard/SpeakerIdDashboardSection.jsx").read_text(encoding="utf-8")

    assert 'dashboardSection === "speaker-id"' in app_source
    assert "<SpeakerIdDashboardSection onRefresh={refresh} />" in app_source
    assert 'openDashboard("speaker-id")' in sidebar_source
    assert "getSpeakerIdStatus" in api_source
    assert 'fetchJson("/api/speaker-id/status")' in api_source
    assert "getSpeakerIdPhraseSets" in api_source
    assert 'fetchJson("/api/speaker-id/phrase-sets")' in api_source
    assert "updateSpeakerIdConfig" in api_source
    assert "installService" in api_source
    assert 'sendJson("/api/services/install"' in api_source
    assert "startSpeakerIdEnrollmentCaptureWindow" in api_source
    assert 'sendJson("/api/speaker-id/enrollment-capture-windows"' in api_source
    assert "startEndpointListen" in api_source
    assert 'sendJson("/api/endpoint/session/listen"' in api_source
    assert "getSpeakerIdEnrollmentCaptures" in api_source
    assert 'fetchJson(`/api/speaker-id/enrollment-captures?' in api_source
    assert "enrollSpeakerIdProfile" in api_source
    assert "deleteSpeakerIdProfile" in api_source
    assert "local biometric Speaker ID templates" in dashboard_source


def test_speaker_id_dashboard_covers_enrollment_profiles_and_diagnostics():
    dashboard_source = Path("frontend/src/features/dashboard/SpeakerIdDashboardSection.jsx").read_text(encoding="utf-8")

    assert "Consent recorded for local biometric Speaker ID templates" in dashboard_source
    assert "retention_policy: \"embeddings_only\"" in dashboard_source
    assert "Live Capture" in dashboard_source
    assert "Three-Phrase Capture" in dashboard_source
    assert "Start Batch Capture" in dashboard_source
    assert "Capture Phrase" in dashboard_source
    assert "startEndpointListen(selectedEndpointId)" in dashboard_source
    assert "Assistant replies are muted for this capture window." in dashboard_source
    assert "MIN_ENROLLMENT_SAMPLES = 8" in dashboard_source
    assert 'RECOMMENDED_ENROLLMENT_SAMPLE_RANGE = "12-16"' in dashboard_source
    assert 'DEFAULT_PHRASE_SET_VERSION = "speaker-id-phrase-set-v1"' in dashboard_source
    assert "phrase_set_version: phraseSet.version || DEFAULT_PHRASE_SET_VERSION" in dashboard_source
    assert "phrase_tracking: profile.phrase_tracking" in dashboard_source
    assert "<strong>Phrase Set</strong>" in dashboard_source
    assert "ageBand: \"unknown\"" in dashboard_source
    assert "guardianManaged: false" in dashboard_source
    assert "adminEligible: false" in dashboard_source
    assert "<strong>Review Due</strong>" in dashboard_source
    assert "age_band: enrollment.ageBand || \"unknown\"" in dashboard_source
    assert "guardian_managed: enrollment.guardianManaged" in dashboard_source
    assert "admin_eligible: enrollment.adminEligible" in dashboard_source
    assert "Child, under 13" in dashboard_source
    assert "Teen, 13-17" in dashboard_source
    assert "Adult admin eligible" in dashboard_source
    assert "Refresh Captures" in dashboard_source
    assert "Add Sample" in dashboard_source
    assert "Hexe, turn on the lights in the living room." in dashboard_source
    assert "A bright yellow scarf was folded inside the small suitcase." in dashboard_source
    assert "Confirm Delete" in dashboard_source
    assert "Delete removes local Speaker ID templates" in dashboard_source
    assert "Recent Outcomes" in dashboard_source
    assert "RecognitionOutcomeDetailPopout" in dashboard_source
    assert "setSelectedOutcome(outcome)" in dashboard_source
    assert "speaker-outcome-detail-popout" in dashboard_source
    assert "Candidates" in dashboard_source
    assert "JSON.stringify(outcome, null, 2)" in dashboard_source
    assert 'label="Unknown"' in dashboard_source
    assert "Low Confidence" in dashboard_source
    assert "Export Metadata" in dashboard_source
    assert "Install missing dependencies" in dashboard_source
    assert 'installService("speaker_id")' in dashboard_source
