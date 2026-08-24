import { useCallback, useEffect, useMemo, useState } from "react";

import {
  deleteSpeakerIdProfile,
  enrollSpeakerIdProfile,
  getSpeakerIdProfiles,
  getSpeakerIdStatus,
  updateSpeakerIdConfig,
} from "../../api/client";

const PROVIDERS = [
  { id: "deterministic_signal", label: "Deterministic Signal" },
  { id: "speechbrain_ecapa_tdnn", label: "SpeechBrain ECAPA-TDNN" },
  { id: "wespeaker", label: "WeSpeaker" },
  { id: "pyannote_audio", label: "pyannote.audio" },
  { id: "nvidia_nemo_speaker", label: "NVIDIA NeMo" },
];

const DEFAULT_ENROLLMENT = {
  displayName: "",
  speakerPublicId: "",
  labels: "",
  consentId: "",
  consentVersion: "speaker-id-consent-v1",
  consentedBy: "",
  consentAccepted: false,
  pastedAudioBase64: "",
  samples: [],
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

function profileLabels(profile) {
  return Array.isArray(profile?.labels) && profile.labels.length ? profile.labels.join(", ") : "none";
}

function outcomeText(outcome) {
  const match = outcome?.match || {};
  const speaker = match.display_name || match.speaker_public_id;
  return [outcome?.status, speaker, outcome?.reason].filter(Boolean).join(" / ") || "none";
}

function fileToSample(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const [, base64 = result] = result.split(",");
      resolve({
        sample_id: file.name,
        audio_base64: base64,
        encoding: file.type || "audio/wav",
      });
    };
    reader.onerror = () => reject(reader.error || new Error("file_read_failed"));
    reader.readAsDataURL(file);
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
      created_at: profile.created_at,
      updated_at: profile.updated_at,
      provider_id: profile.provider_id,
      model_id: profile.model_id,
      embedding_dimensions: profile.embedding_dimensions,
      sample_count: profile.sample_count,
      audio_retained: profile.audio_retained,
    },
  };
}

function SpeakerProfileCard({ profile, deleteConfirmId, busy, onConfirmDelete, onExport }) {
  const confirming = deleteConfirmId === profile.profile_id;
  return (
    <article className="speaker-profile-card">
      <div className="speaker-profile-card-header">
        <div className="speaker-profile-title-block">
          <h3>{valueOrEmpty(profile.display_name, profile.profile_id)}</h3>
          <code className="inline-code">{valueOrEmpty(profile.speaker_public_id)}</code>
        </div>
        <span className={`status-pill status-pill-${profile.audio_retained ? "warning" : "success"}`}>
          {profile.audio_retained ? "audio retained" : "templates only"}
        </span>
      </div>
      <div className="speaker-profile-card-facts">
        <span>
          <strong>Samples</strong>
          {valueOrEmpty(profile.sample_count, "0")}
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
          <strong>Labels</strong>
          {profileLabels(profile)}
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
  const [status, setStatus] = useState(null);
  const [profiles, setProfiles] = useState([]);
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
  const unavailable = !status;
  const samples = enrollment.samples;
  const canEnroll =
    !unavailable &&
    enrollment.displayName.trim() &&
    enrollment.consentAccepted &&
    (samples.length > 0 || enrollment.pastedAudioBase64.trim());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [statusPayload, profilesPayload] = await Promise.all([getSpeakerIdStatus(), getSpeakerIdProfiles()]);
      const thresholds = statusPayload.thresholds || {};
      setStatus(statusPayload);
      setProfiles(Array.isArray(profilesPayload.profiles) ? profilesPayload.profiles : []);
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

  function updateEnrollment(field, value) {
    setEnrollment((current) => ({ ...current, [field]: value }));
  }

  async function handleFilesSelected(event) {
    const files = Array.from(event.target.files || []);
    if (!files.length) {
      return;
    }
    setBusy("files");
    setError("");
    try {
      const nextSamples = await Promise.all(files.map(fileToSample));
      setEnrollment((current) => ({ ...current, samples: [...current.samples, ...nextSamples] }));
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
      event.target.value = "";
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

  async function handleEnroll(event) {
    event.preventDefault();
    if (!canEnroll || busy) {
      return;
    }
    setBusy("enroll");
    setNotice("");
    setError("");
    try {
      const requestSamples = [...samples];
      if (enrollment.pastedAudioBase64.trim()) {
        requestSamples.push({
          sample_id: "pasted-sample",
          audio_base64: enrollment.pastedAudioBase64.trim(),
          encoding: "audio/wav",
        });
      }
      await enrollSpeakerIdProfile({
        schema_version: 1,
        request_id: `speaker-enroll-${Date.now()}`,
        profile: {
          display_name: enrollment.displayName.trim(),
          speaker_public_id: enrollment.speakerPublicId.trim() || null,
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
        samples: requestSamples,
      });
      setEnrollment(DEFAULT_ENROLLMENT);
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

      <div className="callout callout-warning">
        Speaker ID is biometric and local-only. HexeVoice stores local templates, not raw enrollment audio, unless a future provider explicitly changes that policy.
      </div>

      <section className="grid speaker-id-dashboard-grid">
        <form className="panel stack" onSubmit={handleConfigSubmit}>
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Configuration</p>
              <h3 className="section-title">Provider and Thresholds</h3>
            </div>
            <span className={`status-pill status-pill-${status?.ready ? "success" : "danger"}`}>
              {status?.ready ? "ready" : "not ready"}
            </span>
          </div>
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
              <span className="fact-grid-label">Raw Audio Retention</span>
              <span className="fact-grid-value">disabled</span>
            </div>
          </div>
          <div className="actions">
            <button className="btn btn-primary" type="submit" disabled={unavailable || busy === "config"}>
              {busy === "config" ? "Saving..." : "Save Speaker ID Settings"}
            </button>
            <button className="btn btn-ghost" type="button" onClick={load} disabled={Boolean(busy)}>
              Refresh
            </button>
          </div>
        </form>

        <section className="panel stack">
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Diagnostics</p>
              <h3 className="section-title">Health and Outcomes</h3>
            </div>
          </div>
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">Provider</span>
              <span className="fact-grid-value">{providerLabel(status?.provider)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Profiles</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.profiles_count, "0")}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Transport</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.transport?.mode)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Socket</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.transport?.socket_path)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Unknown Outcomes</span>
              <span className="fact-grid-value">{unknownOutcomeCount}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Low Confidence</span>
              <span className="fact-grid-value">{lowConfidenceCount}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Dimensions</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.model?.embedding_dimensions)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Sample Rate</span>
              <span className="fact-grid-value">{valueOrEmpty(status?.model?.sample_rate_hz)}</span>
            </div>
          </div>
          <section className="stack">
            <p className="panel-kicker">Recent Identification Outcomes</p>
            {outcomes.length === 0 ? (
              <div className="callout callout-neutral">No identify or verify outcomes recorded yet.</div>
            ) : (
              <div className="speaker-outcome-list">
                {outcomes.slice(0, 6).map((outcome, index) => (
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
          </section>
          {status?.last_error ? <div className="callout callout-danger">{status.last_error}</div> : null}
          {error ? <div className="callout callout-danger">{error}</div> : null}
          {notice ? <div className="callout callout-success">{notice}</div> : null}
          <pre className="code-panel">{JSON.stringify(status?.provider_status || {}, null, 2)}</pre>
        </section>
      </section>

      <section className="panel stack">
        <div className="section-heading">
          <div>
            <p className="panel-kicker">Enrollment</p>
            <h3 className="section-title">Speaker Profile</h3>
          </div>
          <span className={`status-pill status-pill-${samples.length ? "success" : "neutral"}`}>
            {samples.length} samples
          </span>
        </div>
        <form className="stack" onSubmit={handleEnroll}>
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
              <span className="field-label">Consent ID</span>
              <input
                className="field-input"
                value={enrollment.consentId}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => updateEnrollment("consentId", event.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">Consent Version</span>
              <input
                className="field-input"
                value={enrollment.consentVersion}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => updateEnrollment("consentVersion", event.target.value)}
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
            <label className="field field-span-2">
              <span className="field-label">Upload WAV Samples</span>
              <input
                className="field-input"
                type="file"
                accept="audio/wav,audio/x-wav,.wav"
                multiple
                disabled={unavailable || busy === "enroll" || busy === "files"}
                onChange={handleFilesSelected}
              />
            </label>
            <label className="field field-span-2">
              <span className="field-label">Pasted WAV Base64</span>
              <textarea
                className="field-input speaker-id-audio-textarea"
                value={enrollment.pastedAudioBase64}
                disabled={unavailable || busy === "enroll"}
                onChange={(event) => updateEnrollment("pastedAudioBase64", event.target.value)}
              />
            </label>
            <label className="field field-span-2">
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
          </div>

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
              {busy === "enroll" ? "Enrolling..." : "Enroll Speaker"}
            </button>
          </div>
        </form>
      </section>

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
    </section>
  );
}
