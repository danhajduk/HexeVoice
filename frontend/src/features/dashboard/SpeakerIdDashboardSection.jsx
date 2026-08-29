import { useCallback, useEffect, useMemo, useState } from "react";

import {
  deleteSpeakerIdProfile,
  enrollSpeakerIdProfile,
  getEndpointRegistry,
  getSpeakerIdEnrollmentCaptures,
  getSpeakerIdPhraseSets,
  getSpeakerIdProfiles,
  getSpeakerIdStatus,
  installService,
  startEndpointListen,
  startSpeakerIdEnrollmentCaptureWindow,
  updateSpeakerIdConfig,
  wakeRecordingAudioUrl,
} from "../../api/client";

const PROVIDERS = [
  { id: "deterministic_signal", label: "Deterministic Signal" },
  { id: "speechbrain_ecapa_tdnn", label: "SpeechBrain ECAPA-TDNN" },
  { id: "wespeaker", label: "WeSpeaker" },
  { id: "pyannote_audio", label: "pyannote.audio" },
  { id: "nvidia_nemo_speaker", label: "NVIDIA NeMo" },
];

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "enrollment", label: "Enrollment" },
  { id: "profiles", label: "Profiles" },
  { id: "admin", label: "Admin" },
];

const DEFAULT_ENROLLMENT = {
  displayName: "",
  speakerPublicId: "",
  labels: "",
  ageBand: "unknown",
  guardianManaged: false,
  adminEligible: false,
  consentId: "",
  consentVersion: "speaker-id-consent-v1",
  consentedBy: "",
  consentAccepted: false,
  samples: [],
};

const MIN_ENROLLMENT_SAMPLES = 8;
const RECOMMENDED_ENROLLMENT_SAMPLE_RANGE = "12-16";
const DEFAULT_PHRASE_SET_VERSION = "speaker-id-phrase-set-v1";

const ENROLLMENT_PHRASES = [
  "Hexe, turn on the lights in the living room.",
  "What's the weather going to be like tomorrow morning?",
  "Play some music in the kitchen and set the volume to forty percent.",
  "Remind me to call the dentist when I get home.",
  "Who is at the front door, and when did they arrive?",
  "The quick brown fox jumps over the lazy dog.",
  "Seven people bought fresh coffee, bread, cheese, and apples.",
  "I'd like to know what's on my calendar for Friday afternoon.",
  "Please turn the bedroom temperature down by two degrees.",
  "Sometimes I speak quietly, and sometimes I speak much louder.",
  "Hexe, what time is it?",
  "Could you please tell me whether the garage door is still open?",
  "Add tomatoes, pasta, olive oil, and basil to my shopping list.",
  "Turn off the downstairs lights after the movie is finished.",
  "Tell me how long the drive to the airport will take.",
  "Please remind Sarah that the package is beside the front steps.",
  "Set the hallway lights to a soft blue color tonight.",
  "I need a quiet alarm for six fifteen tomorrow morning.",
  "The old wooden clock stopped ticking during the storm.",
  "Check whether any windows are open before bedtime.",
  "Move my workout reminder from Monday to Wednesday evening.",
  "Start a twenty five minute focus timer in the office.",
  "Read the last notification from the security camera.",
  "A bright yellow scarf was folded inside the small suitcase.",
];

const DEFAULT_ENROLLMENT_PHRASE_SET = {
  version: DEFAULT_PHRASE_SET_VERSION,
  enrollment: ENROLLMENT_PHRASES.map((text, index) => ({
    phrase_id: `enroll-${String(index + 1).padStart(3, "0")}`,
    text,
    category: "enrollment",
  })),
};

function valueOrEmpty(value, fallback = "none") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function numberOrDefault(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatLocalDateTime(value) {
  if (!value) {
    return "none";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDurationMs(value) {
  const duration = Number(value);
  if (!Number.isFinite(duration)) {
    return "unknown";
  }
  return `${(duration / 1000).toFixed(1)} sec`;
}

function statusTone(status) {
  if (!status) {
    return "neutral";
  }
  if (status.ready && status.healthy !== false) {
    return status.enabled ? "success" : "warning";
  }
  return "danger";
}

function providerLabel(providerId) {
  return PROVIDERS.find((provider) => provider.id === providerId)?.label || valueOrEmpty(providerId);
}

function providerStatusMessage(status) {
  const providerStatus = status?.provider_status || {};
  const reason = providerStatus.reason;
  if (!reason) {
    return "";
  }
  if (reason === "missing_optional_dependency") {
    const dependencies = providerStatus.dependencies || {};
    const missing = Object.entries(dependencies)
      .filter(([, available]) => !available)
      .map(([name]) => name);
    return missing.length
      ? `Missing provider dependency: ${missing.join(", ")}. ${providerStatus.install_hint || ""}`.trim()
      : providerStatus.install_hint || "Provider dependency is missing.";
  }
  if (reason === "model_not_loaded") {
    return "Provider is installed; the model will load on first enrollment or identification.";
  }
  if (reason === "model_load_failed") {
    return "Provider is installed, but the model failed to load. Check model cache and network access.";
  }
  if (reason === "implementation_error") {
    return "Provider loaded, but embedding extraction failed. Check provider logs.";
  }
  return `Provider status: ${reason}`;
}

function isSpeakerServiceUnavailable(error) {
  return String(error || "").includes("speaker_id_service_unavailable");
}

function speakerServiceErrorDetail(error) {
  if (!error) {
    return "No service error recorded.";
  }
  const [, detail = error] = String(error).split("speaker_id_service_unavailable:");
  return detail.trim();
}

function profileLabels(profile) {
  return Array.isArray(profile?.labels) && profile.labels.length ? profile.labels.join(", ") : "none";
}

function outcomeText(outcome) {
  const match = outcome?.match || {};
  const speaker = match.display_name || match.speaker_public_id;
  return [outcome?.status, speaker, outcome?.reason].filter(Boolean).join(" / ") || "none";
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const [, base64 = result] = result.split(",");
      resolve(base64);
    };
    reader.onerror = () => reject(reader.error || new Error("blob_read_failed"));
    reader.readAsDataURL(blob);
  });
}

function buildProfileExport(profile) {
  return {
    schema_version: 1,
    exported_at: new Date().toISOString(),
    profile: {
      profile_id: profile.profile_id,
      speaker_public_id: profile.speaker_public_id,
      display_name: profile.display_name,
      labels: profile.labels,
      consent: profile.consent,
      profile_version: profile.profile_version,
      phrase_set_version: profile.phrase_set_version,
      phrase_tracking: profile.phrase_tracking,
      age_band: profile.age_band,
      age_restriction_class: profile.age_restriction_class,
      guardian_managed: profile.guardian_managed,
      profile_review_interval_days: profile.profile_review_interval_days,
      last_voice_profile_review_at: profile.last_voice_profile_review_at,
      next_voice_profile_review_at: profile.next_voice_profile_review_at,
      admin_eligible: profile.admin_eligible,
      profile_learning_requires_review: profile.profile_learning_requires_review,
      speaker_policy: profile.speaker_policy,
      created_at: profile.created_at,
      updated_at: profile.updated_at,
      provider_id: profile.provider_id,
      model_id: profile.model_id,
      embedding_dimensions: profile.embedding_dimensions,
      sample_count: profile.sample_count,
      accepted_sample_count: profile.accepted_sample_count,
      total_accepted_speech_duration_ms: profile.total_accepted_speech_duration_ms,
      enrollment_readiness: profile.enrollment_readiness,
      learning_eligible: profile.learning_eligible,
      audio_retained: profile.audio_retained,
    },
  };
}

function metricTone(count, warningTone = "warning") {
  return Number(count) > 0 ? warningTone : "success";
}

function SpeakerMetricCard({ label, value, tone = "neutral", detail }) {
  return (
    <div className={`speaker-metric-card speaker-metric-card-${tone}`}>
      <span className="fact-grid-label">{label}</span>
      <strong>{value}</strong>
      {detail ? <span className="muted">{detail}</span> : null}
    </div>
  );
}

function SpeakerProfileCard({ profile, deleteConfirmId, busy, onConfirmDelete, onExport }) {
  const confirming = deleteConfirmId === profile.profile_id;
  const readiness = profile.enrollment_readiness || {};
  const readinessTone = readiness.production_ready ? "success" : readiness.can_enroll ? "warning" : "danger";
  return (
    <article className="speaker-profile-card">
      <div className="speaker-profile-card-header">
        <div className="speaker-profile-title-block">
          <h3>{valueOrEmpty(profile.display_name, profile.profile_id)}</h3>
          <code className="inline-code">{valueOrEmpty(profile.speaker_public_id)}</code>
        </div>
        <span className={`status-pill status-pill-${readinessTone}`}>
          {readiness.production_ready ? "production ready" : readiness.can_enroll ? "usable" : "not ready"}
        </span>
      </div>
      <div className="speaker-profile-card-facts">
        <span>
          <strong>Samples</strong>
          {valueOrEmpty(profile.accepted_sample_count ?? profile.sample_count, "0")}
        </span>
        <span>
          <strong>Speech</strong>
          {formatDurationMs(profile.total_accepted_speech_duration_ms)}
        </span>
        <span>
          <strong>Provider</strong>
          {providerLabel(profile.provider_id)}
        </span>
        <span>
          <strong>Model</strong>
          {valueOrEmpty(profile.model_id)}
        </span>
        <span>
          <strong>Version</strong>
          {valueOrEmpty(profile.profile_version)}
        </span>
        <span>
          <strong>Phrase Set</strong>
          {valueOrEmpty(profile.phrase_set_version, DEFAULT_PHRASE_SET_VERSION)}
        </span>
        <span>
          <strong>Age Band</strong>
          {valueOrEmpty(profile.age_band, "not set")}
        </span>
        <span>
          <strong>Restriction</strong>
          {valueOrEmpty(profile.age_restriction_class, "unknown")}
        </span>
        <span>
          <strong>Admin</strong>
          {profile.admin_eligible ? "eligible" : "not eligible"}
        </span>
        <span>
          <strong>Review Due</strong>
          {formatLocalDateTime(profile.next_voice_profile_review_at)}
        </span>
        <span>
          <strong>Labels</strong>
          {profileLabels(profile)}
        </span>
        <span>
          <strong>Storage</strong>
          {profile.audio_retained ? "audio retained" : "templates only"}
        </span>
        <span>
          <strong>Updated</strong>
          {formatLocalDateTime(profile.updated_at)}
        </span>
      </div>
      <div className="compact-actions">
        <button className="btn btn-secondary btn-compact" type="button" onClick={() => onExport(profile)}>
          Export Metadata
        </button>
        <button
          className={confirming ? "btn btn-primary btn-compact" : "btn btn-ghost btn-compact"}
          type="button"
          disabled={busy}
          onClick={() => onConfirmDelete(profile.profile_id, confirming)}
        >
          {confirming ? "Confirm Delete" : "Delete"}
        </button>
      </div>
      {confirming ? (
        <div className="callout callout-warning">
          Delete removes local Speaker ID templates for this profile. Raw enrollment audio is not retained.
        </div>
      ) : null}
    </article>
  );
}

export function SpeakerIdDashboardSection({ onRefresh }) {
  const [activeTab, setActiveTab] = useState("overview");
  const [status, setStatus] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [phraseSet, setPhraseSet] = useState(DEFAULT_ENROLLMENT_PHRASE_SET);
  const [endpoints, setEndpoints] = useState([]);
  const [selectedEndpointId, setSelectedEndpointId] = useState("esp-pe-1");
  const [captureStartedAt, setCaptureStartedAt] = useState("");
  const [captureCandidates, setCaptureCandidates] = useState([]);
  const [activeBatchIndex, setActiveBatchIndex] = useState(0);
  const [config, setConfig] = useState({
    enabled: false,
    provider: "deterministic_signal",
    identify_min_confidence: 0.82,
    identify_min_margin: 0.08,
    verify_min_score: 0.86,
  });
  const [enrollment, setEnrollment] = useState(DEFAULT_ENROLLMENT);
  const [deleteConfirmId, setDeleteConfirmId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const outcomes = useMemo(
    () => (Array.isArray(status?.recent_identification_outcomes) ? status.recent_identification_outcomes : []),
    [status],
  );
  const unknownOutcomeCount = outcomes.filter((outcome) => outcome?.status === "unknown").length;
  const lowConfidenceCount = outcomes.filter((outcome) => outcome?.reason === "low_confidence").length;
  const identifiedOutcomeCount = outcomes.filter((outcome) => outcome?.status === "identified").length;
  const unavailable = !status;
  const samples = enrollment.samples;
  const enrollmentPhrases = Array.isArray(phraseSet?.enrollment) ? phraseSet.enrollment : DEFAULT_ENROLLMENT_PHRASE_SET.enrollment;
  const batchCount = Math.ceil(enrollmentPhrases.length / 3);
  const activeBatchPhrases = enrollmentPhrases.slice(activeBatchIndex * 3, activeBatchIndex * 3 + 3);
  const endpointOptions = useMemo(() => {
    const seen = new Set();
    const options = [];
    endpoints.forEach((endpoint) => {
      const endpointId = String(endpoint?.endpoint_id || "").trim();
      if (!endpointId || seen.has(endpointId)) {
        return;
      }
      seen.add(endpointId);
      options.push({
        endpoint_id: endpointId,
        display_name: endpoint.display_name || endpoint.name || endpointId,
      });
    });
    if (!seen.has("esp-pe-1")) {
      options.unshift({ endpoint_id: "esp-pe-1", display_name: "esp-pe-1" });
    }
    return options;
  }, [endpoints]);
  const canEnroll =
    !unavailable &&
    enrollment.displayName.trim() &&
    enrollment.consentAccepted &&
    samples.length >= MIN_ENROLLMENT_SAMPLES;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [statusPayload, profilesPayload, phrasePayload] = await Promise.all([
        getSpeakerIdStatus(),
        getSpeakerIdProfiles(),
        getSpeakerIdPhraseSets(),
      ]);
      const thresholds = statusPayload.thresholds || {};
      const activeVersion = phrasePayload.active_version || DEFAULT_PHRASE_SET_VERSION;
      const activePhraseSet =
        Array.isArray(phrasePayload.phrase_sets) &&
        phrasePayload.phrase_sets.find((candidate) => candidate?.version === activeVersion);
      setStatus(statusPayload);
      setProfiles(Array.isArray(profilesPayload.profiles) ? profilesPayload.profiles : []);
      setPhraseSet(activePhraseSet || DEFAULT_ENROLLMENT_PHRASE_SET);
      setConfig({
        enabled: Boolean(statusPayload.enabled),
        provider: statusPayload.provider || "deterministic_signal",
        identify_min_confidence: thresholds.identify_min_confidence ?? 0.82,
        identify_min_margin: thresholds.identify_min_margin ?? 0.08,
        verify_min_score: thresholds.verify_min_score ?? 0.86,
      });
      setError("");
    } catch (err) {
      setStatus(null);
      setProfiles([]);
      setPhraseSet(DEFAULT_ENROLLMENT_PHRASE_SET);
      setError(String(err.message || err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    async function refreshVisibleSpeakerId() {
      if (!mounted) {
        return;
      }
      await load();
    }
    refreshVisibleSpeakerId();
    const timer = window.setInterval(refreshVisibleSpeakerId, 5000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [load]);

  useEffect(() => {
    let mounted = true;
    getEndpointRegistry()
      .then((payload) => {
        if (!mounted) {
          return;
        }
        const nextEndpoints = Array.isArray(payload?.endpoints) ? payload.endpoints : [];
        setEndpoints(nextEndpoints);
        if (!selectedEndpointId && nextEndpoints[0]?.endpoint_id) {
          setSelectedEndpointId(nextEndpoints[0].endpoint_id);
        }
      })
      .catch(() => {
        if (mounted) {
          setEndpoints([]);
        }
      });
    return () => {
      mounted = false;
    };
  }, [selectedEndpointId]);

  function updateEnrollment(field, value) {
    setEnrollment((current) => ({ ...current, [field]: value }));
  }

  async function loadCaptureCandidates({ since = captureStartedAt } = {}) {
    setBusy("captures");
    setError("");
    try {
      const payload = await getSpeakerIdEnrollmentCaptures({
        endpointId: selectedEndpointId,
        since,
        limit: 12,
      });
      setCaptureCandidates(Array.isArray(payload.captures) ? payload.captures : []);
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function handleStartCaptureWindow() {
    setBusy("captures");
    setNotice("");
    setError("");
    try {
      const payload = await startSpeakerIdEnrollmentCaptureWindow({
        endpointId: selectedEndpointId,
        ttlSeconds: 300,
      });
      const window = payload.window || {};
      const startedAt = window.started_at || new Date().toISOString();
      setCaptureStartedAt(startedAt);
      setCaptureCandidates([]);
      setNotice("Endpoint enrollment capture is active. Assistant replies are muted for this capture window.");
      await loadCaptureCandidates({ since: startedAt });
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function handleCapturePhrase() {
    if (!captureStartedAt) {
      await handleStartCaptureWindow();
    }
    setBusy("capture-phrase");
    setNotice("");
    setError("");
    try {
      const result = await startEndpointListen(selectedEndpointId);
      if (!result.accepted) {
        throw new Error(result.reason || result.status || "endpoint_listen_failed");
      }
      setNotice("Endpoint capture started.");
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function handleAddCaptureSample(capture) {
    const sampleId = `endpoint-${capture.recording_id}`;
    if (samples.some((sample) => sample.sample_id === sampleId)) {
      setNotice("Capture already added as an enrollment sample.");
      return;
    }
    setBusy(sampleId);
    setNotice("");
    setError("");
    try {
      const response = await fetch(wakeRecordingAudioUrl(capture.recording_id));
      if (!response.ok) {
        throw new Error(`capture_fetch_failed_${response.status}`);
      }
      const audioBase64 = await blobToBase64(await response.blob());
      setEnrollment((current) => ({
        ...current,
        samples: [
          ...current.samples,
          {
            sample_id: sampleId,
            audio_base64: audioBase64,
            encoding: "audio/wav",
            phrase_set_version: phraseSet.version || DEFAULT_PHRASE_SET_VERSION,
            phrase_id: enrollmentPhrases[current.samples.length]?.phrase_id || null,
            phrase_text: enrollmentPhrases[current.samples.length]?.text || null,
            phrase_status: "accepted",
          },
        ],
      }));
      setNotice("Endpoint capture added as an enrollment sample.");
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function handleConfigSubmit(event) {
    event.preventDefault();
    setBusy("config");
    setNotice("");
    setError("");
    try {
      const result = await updateSpeakerIdConfig({
        enabled: config.enabled,
        provider: config.provider,
        identify_min_confidence: numberOrDefault(config.identify_min_confidence, 0.82),
        identify_min_margin: numberOrDefault(config.identify_min_margin, 0.08),
        verify_min_score: numberOrDefault(config.verify_min_score, 0.86),
      });
      setStatus(result);
      setNotice("Speaker ID settings saved.");
      await onRefresh?.();
      await load();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function handleInstallProviderDependencies() {
    setBusy("install-provider");
    setNotice("");
    setError("");
    try {
      const result = await installService("speaker_id");
      if (!result.accepted) {
        throw new Error(result.detail || result.status || "speaker_id_install_failed");
      }
      setNotice(result.detail || "Speaker ID provider dependencies installed.");
      await load();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function handleEnroll(event) {
    event.preventDefault();
    if (!canEnroll || busy) {
      return;
    }
    setBusy("enroll");
    setNotice("");
    setError("");
    try {
      await enrollSpeakerIdProfile({
        schema_version: 1,
        request_id: `speaker-enroll-${Date.now()}`,
        phrase_set_version: phraseSet.version || DEFAULT_PHRASE_SET_VERSION,
        profile: {
          display_name: enrollment.displayName.trim(),
          speaker_public_id: enrollment.speakerPublicId.trim() || null,
          age_band: enrollment.ageBand || "unknown",
          guardian_managed: enrollment.guardianManaged,
          admin_eligible: enrollment.adminEligible,
          labels: enrollment.labels
            .split(",")
            .map((label) => label.trim())
            .filter(Boolean),
        },
        consent: {
          consent_id: enrollment.consentId.trim() || `speaker-id-consent-${Date.now()}`,
          consent_version: enrollment.consentVersion.trim() || "speaker-id-consent-v1",
          consented_at: new Date().toISOString(),
          consented_by: enrollment.consentedBy.trim() || "operator",
          retention_policy: "embeddings_only",
        },
        samples,
      });
      setEnrollment(DEFAULT_ENROLLMENT);
      setCaptureCandidates([]);
      setCaptureStartedAt("");
      setActiveTab("profiles");
      setNotice("Speaker profile enrolled.");
      await load();
      await onRefresh?.();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function handleConfirmDelete(profileId, confirming) {
    if (!confirming) {
      setDeleteConfirmId(profileId);
      return;
    }
    setBusy(`delete:${profileId}`);
    setNotice("");
    setError("");
    try {
      await deleteSpeakerIdProfile(profileId);
      setDeleteConfirmId("");
      setNotice("Speaker profile deleted.");
      await load();
      await onRefresh?.();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  function handleExportProfile(profile) {
    const blob = new Blob([JSON.stringify(buildProfileExport(profile), null, 2)], {
      type: "application/json",
    });
    const href = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = href;
    link.download = `${profile.profile_id || "speaker-profile"}.json`;
    link.click();
    URL.revokeObjectURL(href);
  }

  function removeSample(sampleId) {
    setEnrollment((current) => ({
      ...current,
      samples: current.samples.filter((sample) => sample.sample_id !== sampleId),
    }));
  }

  function renderOverview() {
    const serviceUnavailable = unavailable && isSpeakerServiceUnavailable(error);
    return (
      <section className="speaker-workflow-grid">
        <section className="panel stack">
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Recognition Health</p>
              <h3 className="section-title">Current State</h3>
            </div>
            <span className={`status-pill status-pill-${statusTone(status)}`}>
              {loading ? "loading" : status?.enabled ? "enabled" : unavailable ? "unavailable" : "disabled"}
            </span>
          </div>
          {serviceUnavailable ? (
            <div className="speaker-recovery-card">
              <div>
                <p className="panel-kicker">Recovery</p>
                <h3 className="section-title">Speaker ID service is not running</h3>
              </div>
              <div className="callout callout-warning">
                Recognition and enrollment are paused until the local Speaker ID service is available.
              </div>
              <div className="fact-grid">
                <div className="fact-grid-item">
                  <span className="fact-grid-label">Provider</span>
                  <span className="fact-grid-value">{providerLabel(config.provider)}</span>
                </div>
                <div className="fact-grid-item">
                  <span className="fact-grid-label">Last error</span>
                  <span className="fact-grid-value">{speakerServiceErrorDetail(error)}</span>
                </div>
              </div>
              <div className="actions">
                <button className="btn btn-primary" type="button" onClick={() => setActiveTab("admin")}>
                  Open Admin
                </button>
                <button className="btn btn-secondary" type="button" onClick={handleInstallProviderDependencies} disabled={Boolean(busy)}>
                  {busy === "install-provider" ? "Repairing..." : "Install or Repair"}
                </button>
                <button className="btn btn-ghost" type="button" onClick={load} disabled={Boolean(busy)}>
                  Refresh
                </button>
              </div>
            </div>
          ) : null}
          <div className="speaker-metric-grid speaker-overview-metric-grid">
            <SpeakerMetricCard
              label="Service"
              value={loading ? "loading" : status?.enabled ? "enabled" : unavailable ? "down" : "disabled"}
              tone={unavailable ? "danger" : status?.enabled ? "success" : "warning"}
            />
            <SpeakerMetricCard label="Provider" value={providerLabel(status?.provider || config.provider)} tone={unavailable ? "warning" : "success"} />
            <SpeakerMetricCard label="Profiles" value={profiles.length} tone={profiles.length ? "success" : "neutral"} />
            <SpeakerMetricCard label="Identified" value={identifiedOutcomeCount} tone="success" />
            <SpeakerMetricCard label="Unknown" value={unknownOutcomeCount} tone={metricTone(unknownOutcomeCount)} />
            <SpeakerMetricCard label="Low Confidence" value={lowConfidenceCount} tone={metricTone(lowConfidenceCount)} />
          </div>
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">Provider</span>
              <span className="fact-grid-value">{providerLabel(status?.provider)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Model</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.model_id)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Transport</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.transport?.mode)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Raw Audio</span>
              <span className="fact-grid-value">disabled</span>
            </div>
          </div>
        </section>

        <section className="panel stack">
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Recent Outcomes</p>
              <h3 className="section-title">Recognition Stream</h3>
            </div>
          </div>
          {outcomes.length === 0 ? (
            <div className="speaker-empty-state">
              <strong>No recognition outcomes</strong>
              <span>{unavailable ? "The stream will populate after Speaker ID is running." : "Identify and verify results will appear here."}</span>
            </div>
          ) : (
            <div className="speaker-outcome-list">
              {outcomes.slice(0, 8).map((outcome, index) => (
                <div className="speaker-outcome-row" key={`${outcome.request_id || "outcome"}-${index}`}>
                  <span className={`status-pill status-pill-${outcome.status === "unknown" ? "warning" : "success"}`}>
                    {valueOrEmpty(outcome.status)}
                  </span>
                  <span>{outcomeText(outcome)}</span>
                  <span className="muted">{formatLocalDateTime(outcome.recorded_at)}</span>
                </div>
              ))}
            </div>
          )}
          <div className="actions">
            <button className="btn btn-primary" type="button" onClick={() => setActiveTab("enrollment")}>
              Start Enrollment
            </button>
            <button className="btn btn-secondary" type="button" onClick={() => setActiveTab("profiles")}>
              Review Profiles
            </button>
            <button className="btn btn-ghost" type="button" onClick={load} disabled={Boolean(busy)}>
              Refresh
            </button>
          </div>
        </section>
      </section>
    );
  }

  function renderEnrollment() {
    return (
      <section className="speaker-enrollment-layout">
        <form className="panel stack" onSubmit={handleEnroll}>
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Live Capture</p>
              <h3 className="section-title">Endpoint Enrollment</h3>
            </div>
            <span className={`status-pill status-pill-${samples.length ? "success" : "neutral"}`}>
              {samples.length} samples
            </span>
          </div>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">Display Name</span>
              <input
                className="field-input"
                value={enrollment.displayName}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => updateEnrollment("displayName", event.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">Public Speaker ID</span>
              <input
                className="field-input"
                value={enrollment.speakerPublicId}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => updateEnrollment("speakerPublicId", event.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">Labels</span>
              <input
                className="field-input"
                value={enrollment.labels}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => updateEnrollment("labels", event.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">Age Band</span>
              <select
                className="field-input"
                value={enrollment.ageBand}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) =>
                  setEnrollment((current) => ({
                    ...current,
                    ageBand: event.target.value,
                    adminEligible: event.target.value === "adult" ? current.adminEligible : false,
                  }))
                }
              >
                <option value="unknown">Unknown</option>
                <option value="child">Child, under 13</option>
                <option value="teen">Teen, 13-17</option>
                <option value="adult">Adult, 18+</option>
              </select>
            </label>
            <label className="field">
              <span className="field-label">Endpoint</span>
              <select
                className="field-input"
                value={selectedEndpointId}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => setSelectedEndpointId(event.target.value)}
              >
                {endpointOptions.map((endpoint) => (
                  <option key={endpoint.endpoint_id} value={endpoint.endpoint_id}>
                    {endpoint.display_name} / {endpoint.endpoint_id}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Consent ID</span>
              <input
                className="field-input"
                value={enrollment.consentId}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => updateEnrollment("consentId", event.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">Consented By</span>
              <input
                className="field-input"
                value={enrollment.consentedBy}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => updateEnrollment("consentedBy", event.target.value)}
              />
            </label>
          </div>

          <div className="settings-grid settings-grid-compact">
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={enrollment.guardianManaged}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => updateEnrollment("guardianManaged", event.target.checked)}
              />
              Guardian managed profile
            </label>
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={enrollment.adminEligible}
                disabled={unavailable || busy === "enroll" || enrollment.ageBand !== "adult"}
                onChange={(event) => updateEnrollment("adminEligible", event.target.checked)}
              />
              Adult admin eligible
            </label>
          </div>

          <section className="speaker-endpoint-capture">
            <div className="section-heading">
              <div>
                <p className="panel-kicker">
                  Batch {activeBatchIndex + 1} of {batchCount} / {phraseSet.version || DEFAULT_PHRASE_SET_VERSION}
                </p>
                <h4 className="section-title">Three-Phrase Capture</h4>
              </div>
              <span className={`status-pill status-pill-${captureStartedAt ? "success" : "neutral"}`}>
                {captureStartedAt ? "window open" : "ready"}
              </span>
            </div>
            <div className="speaker-batch-controls">
              <button
                className="btn btn-ghost btn-compact"
                type="button"
                disabled={activeBatchIndex === 0}
                onClick={() => setActiveBatchIndex((index) => Math.max(0, index - 1))}
              >
                Previous
              </button>
              <span className="status-pill status-pill-neutral">{activeBatchPhrases.length} phrases</span>
              <button
                className="btn btn-ghost btn-compact"
                type="button"
                disabled={activeBatchIndex >= batchCount - 1}
                onClick={() => setActiveBatchIndex((index) => Math.min(batchCount - 1, index + 1))}
              >
                Next
              </button>
            </div>
            <div className="speaker-prompt-grid">
              {activeBatchPhrases.map((prompt, index) => (
                <div className="speaker-prompt-card" key={prompt.phrase_id || prompt.text}>
                  <span className="fact-grid-label">Phrase {activeBatchIndex * 3 + index + 1}</span>
                  <span className="fact-grid-value">{prompt.text}</span>
                </div>
              ))}
            </div>
            <div className="actions">
              <button
                className="btn btn-secondary"
                type="button"
                disabled={unavailable || busy === "captures"}
                onClick={handleStartCaptureWindow}
              >
                Start Batch Capture
              </button>
              <button
                className="btn btn-primary"
                type="button"
                disabled={unavailable || busy === "capture-phrase" || busy === "captures"}
                onClick={handleCapturePhrase}
              >
                {busy === "capture-phrase" ? "Starting..." : "Capture Phrase"}
              </button>
              <button
                className="btn btn-ghost"
                type="button"
                disabled={unavailable || busy === "captures"}
                onClick={() => loadCaptureCandidates()}
              >
                {busy === "captures" ? "Refreshing..." : "Refresh Captures"}
              </button>
            </div>
            {captureStartedAt ? (
              <div className="callout callout-neutral">
                Capture window started at {formatLocalDateTime(captureStartedAt)} for {selectedEndpointId}.
              </div>
            ) : null}
            {captureCandidates.length ? (
              <div className="speaker-capture-list">
                {captureCandidates.map((capture) => (
                  <div className="speaker-capture-row" key={capture.recording_id}>
                    <div>
                      <span className="fact-grid-label">{formatLocalDateTime(capture.recorded_at)}</span>
                      <span className="fact-grid-value">
                        {capture.recording_id} / {formatDurationMs(capture.duration_ms)}
                      </span>
                      {capture.transcript?.text ? <span className="muted">{capture.transcript.text}</span> : null}
                    </div>
                    <button
                      className="btn btn-secondary btn-compact"
                      type="button"
                      disabled={busy === `endpoint-${capture.recording_id}`}
                      onClick={() => handleAddCaptureSample(capture)}
                    >
                      {busy === `endpoint-${capture.recording_id}` ? "Adding..." : "Add Sample"}
                    </button>
                  </div>
                ))}
              </div>
            ) : captureStartedAt ? (
              <div className="callout callout-neutral">No endpoint captures found for this window.</div>
            ) : null}
          </section>

          <label className="field">
            <span className="toggle-row">
              <input
                type="checkbox"
                checked={enrollment.consentAccepted}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => updateEnrollment("consentAccepted", event.target.checked)}
              />
              Consent recorded for local biometric Speaker ID templates
            </span>
          </label>

          {samples.length ? (
            <div className="speaker-sample-list">
              {samples.map((sample) => (
                <div className="speaker-sample-row" key={sample.sample_id}>
                  <span>{sample.sample_id}</span>
                  <span className="muted">{sample.encoding || "audio/wav"}</span>
                  <button className="btn btn-ghost btn-compact" type="button" onClick={() => removeSample(sample.sample_id)}>
                    Remove
                  </button>
                </div>
              ))}
            </div>
          ) : null}

          <div className="actions">
            <button className="btn btn-primary" type="submit" disabled={!canEnroll || busy === "enroll"}>
              {busy === "enroll" ? "Enrolling..." : "Create Profile"}
            </button>
          </div>
        </form>

        <section className="panel stack">
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Capture Quality</p>
              <h3 className="section-title">Readiness</h3>
            </div>
            <span className={`status-pill status-pill-${canEnroll ? "success" : "warning"}`}>
              {canEnroll ? "ready" : "incomplete"}
            </span>
          </div>
          <div className="speaker-enrollment-steps">
            <div className={`speaker-step ${enrollment.displayName.trim() ? "speaker-step-done" : ""}`}>
              <strong>Profile</strong>
              <span>{enrollment.displayName.trim() || "name required"}</span>
            </div>
            <div className={`speaker-step ${captureStartedAt ? "speaker-step-done" : ""}`}>
              <strong>Ambient Check</strong>
              <span>{captureStartedAt ? "capture window started" : "pending"}</span>
            </div>
            <div className={`speaker-step ${samples.length ? "speaker-step-done" : ""}`}>
              <strong>Samples</strong>
              <span>
                {samples.length} / {MIN_ENROLLMENT_SAMPLES} accepted
              </span>
            </div>
            <div className={`speaker-step ${samples.length >= 12 ? "speaker-step-done" : ""}`}>
              <strong>Recommended</strong>
              <span>{RECOMMENDED_ENROLLMENT_SAMPLE_RANGE} samples</span>
            </div>
            <div className={`speaker-step ${enrollment.consentAccepted ? "speaker-step-done" : ""}`}>
              <strong>Consent</strong>
              <span>{enrollment.consentAccepted ? "recorded" : "required"}</span>
            </div>
          </div>
          <div className="callout callout-neutral">
            Endpoint capture is the normal enrollment path. File upload and pasted audio are not part of this workflow.
          </div>
        </section>
      </section>
    );
  }

  function renderProfiles() {
    return (
      <section className="panel stack">
        <div className="section-heading">
          <div>
            <p className="panel-kicker">Profiles</p>
            <h3 className="section-title">Local Speaker Templates</h3>
          </div>
          <span className="status-pill status-pill-neutral">{profiles.length} profiles</span>
        </div>
        {profiles.length === 0 ? (
          <div className="callout callout-neutral">No local Speaker ID profiles enrolled.</div>
        ) : (
          <div className="speaker-profile-card-grid">
            {profiles.map((profile) => (
              <SpeakerProfileCard
                key={profile.profile_id}
                profile={profile}
                deleteConfirmId={deleteConfirmId}
                busy={busy === `delete:${profile.profile_id}`}
                onConfirmDelete={handleConfirmDelete}
                onExport={handleExportProfile}
              />
            ))}
          </div>
        )}
      </section>
    );
  }

  function renderAdmin() {
    const providerMessage = providerStatusMessage(status);
    const canInstallProvider = status?.provider_status?.reason === "missing_optional_dependency";
    return (
      <section className="speaker-workflow-grid">
        <form className="panel stack" onSubmit={handleConfigSubmit}>
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Admin</p>
              <h3 className="section-title">Provider and Thresholds</h3>
            </div>
            <span className={`status-pill status-pill-${status?.ready ? "success" : "danger"}`}>
              {status?.ready ? "ready" : "not ready"}
            </span>
          </div>
          {providerMessage ? (
            <div className={`callout ${status?.provider_status?.reason === "missing_optional_dependency" ? "callout-danger" : "callout-warning"}`}>
              {providerMessage}
            </div>
          ) : null}
          <div className="form-grid">
            <label className="field field-span-2">
              <span className="field-label">Service State</span>
              <span className="toggle-row">
                <input
                  type="checkbox"
                  checked={config.enabled}
                  disabled={unavailable || busy === "config"}
                  onChange={(event) => setConfig((current) => ({ ...current, enabled: event.target.checked }))}
                />
                {config.enabled ? "Enabled" : "Disabled"}
              </span>
            </label>
            <label className="field">
              <span className="field-label">Provider</span>
              <select
                className="field-input"
                value={config.provider}
                disabled={unavailable || busy === "config"}
                onChange={(event) => setConfig((current) => ({ ...current, provider: event.target.value }))}
              >
                {PROVIDERS.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Model</span>
              <input className="field-input" value={valueOrEmpty(status?.model_id)} readOnly />
            </label>
            <label className="field">
              <span className="field-label">Identify Confidence</span>
              <input
                className="field-input"
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={config.identify_min_confidence}
                disabled={unavailable || busy === "config"}
                onChange={(event) =>
                  setConfig((current) => ({ ...current, identify_min_confidence: event.target.value }))
                }
              />
            </label>
            <label className="field">
              <span className="field-label">Identify Margin</span>
              <input
                className="field-input"
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={config.identify_min_margin}
                disabled={unavailable || busy === "config"}
                onChange={(event) => setConfig((current) => ({ ...current, identify_min_margin: event.target.value }))}
              />
            </label>
            <label className="field">
              <span className="field-label">Verify Score</span>
              <input
                className="field-input"
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={config.verify_min_score}
                disabled={unavailable || busy === "config"}
                onChange={(event) => setConfig((current) => ({ ...current, verify_min_score: event.target.value }))}
              />
            </label>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Raw Debug Audio</span>
              <span className="fact-grid-value">one-day retention</span>
            </div>
          </div>
          <div className="actions">
            <button className="btn btn-primary" type="submit" disabled={unavailable || busy === "config"}>
              {busy === "config" ? "Saving..." : "Save Settings"}
            </button>
            {canInstallProvider ? (
              <button
                className="btn btn-secondary"
                type="button"
                onClick={handleInstallProviderDependencies}
                disabled={Boolean(busy)}
              >
                {busy === "install-provider" ? "Installing..." : "Install missing dependencies"}
              </button>
            ) : null}
            <button className="btn btn-ghost" type="button" onClick={load} disabled={Boolean(busy)}>
              Refresh
            </button>
          </div>
        </form>

        <section className="panel stack">
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Diagnostics</p>
              <h3 className="section-title">Provider Status</h3>
            </div>
          </div>
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">Socket</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.transport?.socket_path)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Dimensions</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.model?.embedding_dimensions)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Sample Rate</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.model?.sample_rate_hz)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Profiles Path</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.profiles_path)}</span>
            </div>
          </div>
          <pre className="code-panel">{JSON.stringify(status?.provider_status || {}, null, 2)}</pre>
        </section>
      </section>
    );
  }

  return (
    <section className="speaker-id-dashboard stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Voice Service</p>
          <h2 className="panel-title">Speaker ID</h2>
        </div>
        <span className={`status-pill status-pill-${statusTone(status)}`}>
          {loading ? "loading" : status?.enabled ? "enabled" : unavailable ? "unavailable" : "disabled"}
        </span>
      </div>

      <div className="speaker-tabs" role="tablist" aria-label="Speaker ID workflows">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`speaker-tab ${activeTab === tab.id ? "speaker-tab-active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {status?.last_error ? <div className="callout callout-danger">{status.last_error}</div> : null}
      {error && !isSpeakerServiceUnavailable(error) ? <div className="callout callout-danger">{error}</div> : null}
      {notice ? <div className="callout callout-success">{notice}</div> : null}

      {activeTab === "overview" ? renderOverview() : null}
      {activeTab === "enrollment" ? renderEnrollment() : null}
      {activeTab === "profiles" ? renderProfiles() : null}
      {activeTab === "admin" ? renderAdmin() : null}
    </section>
  );
}
