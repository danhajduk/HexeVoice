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
    assert "updateSpeakerIdConfig" in api_source
    assert "getSpeakerIdEnrollmentCaptures" in api_source
    assert 'fetchJson(`/api/speaker-id/enrollment-captures?' in api_source
    assert "enrollSpeakerIdProfile" in api_source
    assert "deleteSpeakerIdProfile" in api_source
    assert "Speaker ID is biometric and local-only" in dashboard_source


def test_speaker_id_dashboard_covers_enrollment_profiles_and_diagnostics():
    dashboard_source = Path("frontend/src/features/dashboard/SpeakerIdDashboardSection.jsx").read_text(encoding="utf-8")

    assert "Consent recorded for local biometric Speaker ID templates" in dashboard_source
    assert "retention_policy: \"embeddings_only\"" in dashboard_source
    assert "Upload WAV Samples" in dashboard_source
    assert "Pasted WAV Base64" in dashboard_source
    assert "Voice PE Capture" in dashboard_source
    assert "Read-Aloud Samples" in dashboard_source
    assert "Start Capture Window" in dashboard_source
    assert "Refresh Captures" in dashboard_source
    assert "Add Sample" in dashboard_source
    assert "Hexe, this is my local voice profile." in dashboard_source
    assert "Confirm Delete" in dashboard_source
    assert "Delete removes local Speaker ID templates" in dashboard_source
    assert "Recent Identification Outcomes" in dashboard_source
    assert "Unknown Outcomes" in dashboard_source
    assert "Low Confidence" in dashboard_source
    assert "Export Metadata" in dashboard_source
