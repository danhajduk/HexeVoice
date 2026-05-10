import { useEffect, useMemo, useState } from "react";

import { saveTtsSettings } from "../../api/client";

const SAMPLE_RATE_LABELS = {
  48000: "48 kHz",
  22050: "22.05 kHz",
  16000: "16 kHz",
};

function valueOrEmpty(value, fallback = "none") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

function formatSampleRate(value) {
  if (!value) {
    return "unknown";
  }
  return SAMPLE_RATE_LABELS[value] || `${value} Hz`;
}

function modelLabel(model) {
  return model?.display_name || model?.model_id || "unknown";
}

function ModelCard({ model, warm, onOpen }) {
  return (
    <button className="tts-model-card" type="button" onClick={() => onOpen(model)}>
      <div className="tts-model-card-header">
        <span className={`status-pill status-pill-${warm ? "success" : "neutral"}`}>{warm ? "warm" : "cold"}</span>
        <span className="tts-model-rate">{formatSampleRate(model.raw_sample_rate_hz)}</span>
      </div>
      <div className="tts-model-card-title-block">
        <span className="intent-title">{modelLabel(model)}</span>
        <code className="inline-code">{valueOrEmpty(model.model_id)}</code>
      </div>
      <div className="tts-model-card-facts">
        <span>
          <strong>Language</strong>
          {valueOrEmpty(model.language)}
        </span>
        <span>
          <strong>Dataset</strong>
          {valueOrEmpty(model.dataset)}
        </span>
      </div>
    </button>
  );
}

function ModelDetailPopout({ model, warm, onToggleWarm, onClose }) {
  if (!model) {
    return null;
  }

  return (
    <div className="tts-model-detail-backdrop" role="presentation" onClick={onClose}>
      <section
        className="tts-model-detail-popout"
        role="dialog"
        aria-modal="true"
        aria-label="TTS model details"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="section-heading">
          <div>
            <p className="panel-kicker">TTS Model</p>
            <h2 className="panel-title">{modelLabel(model)}</h2>
          </div>
          <div className="hero-actions">
            <button className="btn btn-secondary btn-compact" type="button" onClick={() => onToggleWarm(model.model_id)}>
              {warm ? "Remove Warm" : "Keep Warm"}
            </button>
            <button className="btn btn-ghost btn-compact" type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
        <div className="tts-model-detail-summary">
          <span className={`status-pill status-pill-${warm ? "success" : "neutral"}`}>{warm ? "warm" : "cold"}</span>
          <code className="inline-code">{valueOrEmpty(model.model_id)}</code>
        </div>
        <div className="tts-model-detail-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Raw Rate</span>
            <span className="fact-grid-value">{formatSampleRate(model.raw_sample_rate_hz)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Language</span>
            <span className="fact-grid-value">{valueOrEmpty(model.language)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Dataset</span>
            <span className="fact-grid-value">{valueOrEmpty(model.dataset)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Quality</span>
            <span className="fact-grid-value">{valueOrEmpty(model.quality)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Provider</span>
            <span className="fact-grid-value">{valueOrEmpty(model.provider)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Speaker</span>
            <span className="fact-grid-value">{valueOrEmpty(model.speaker_id)}</span>
          </div>
        </div>
        <section className="tts-model-detail-section">
          <p className="panel-kicker">Raw Model Metadata</p>
          <pre className="code-panel">{JSON.stringify(model, null, 2)}</pre>
        </section>
      </section>
    </div>
  );
}

function sameStrings(left, right) {
  const leftValues = [...left].sort();
  const rightValues = [...right].sort();
  return leftValues.length === rightValues.length && leftValues.every((value, index) => value === rightValues[index]);
}

function sameNumbers(left, right) {
  const leftValues = [...left].map(Number).sort((a, b) => a - b);
  const rightValues = [...right].map(Number).sort((a, b) => a - b);
  return leftValues.length === rightValues.length && leftValues.every((value, index) => value === rightValues[index]);
}

export function TtsProviderDashboardSection({ providerSetup, capabilities, ttsSettings, onRefresh }) {
  const models = useMemo(() => (Array.isArray(ttsSettings?.models) ? ttsSettings.models : []), [ttsSettings]);
  const allowedRates = useMemo(
    () => (Array.isArray(ttsSettings?.allowed_conversion_sample_rates_hz) ? ttsSettings.allowed_conversion_sample_rates_hz : []),
    [ttsSettings],
  );
  const [warmVoices, setWarmVoices] = useState([]);
  const [conversionRates, setConversionRates] = useState([]);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [selectedModel, setSelectedModel] = useState(null);

  useEffect(() => {
    setWarmVoices(Array.isArray(ttsSettings?.warm_voices) ? ttsSettings.warm_voices : []);
    setConversionRates(
      Array.isArray(ttsSettings?.conversion_sample_rates_hz) ? ttsSettings.conversion_sample_rates_hz : [],
    );
  }, [ttsSettings]);

  const dirty =
    !sameStrings(warmVoices, ttsSettings?.warm_voices || []) ||
    !sameNumbers(conversionRates, ttsSettings?.conversion_sample_rates_hz || []);

  function toggleWarmVoice(modelId) {
    setWarmVoices((current) =>
      current.includes(modelId) ? current.filter((voice) => voice !== modelId) : [...current, modelId],
    );
  }

  function toggleConversionRate(rate) {
    setConversionRates((current) =>
      current.includes(rate) ? current.filter((value) => value !== rate) : [...current, rate],
    );
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (saving) {
      return;
    }
    setSaving(true);
    setNotice("");
    setError("");
    try {
      await saveTtsSettings({
        warm_voices: warmVoices,
        conversion_sample_rates_hz: conversionRates,
      });
      setNotice("TTS settings saved.");
      await onRefresh?.();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="voice-endpoint-panel stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Providers</p>
          <h2 className="panel-title">TTS Runtime</h2>
        </div>
        <span className={`status-pill status-pill-${ttsSettings?.restart_required ? "warning" : "success"}`}>
          {ttsSettings?.restart_required ? "restart required" : "ready"}
        </span>
      </div>

      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Provider</span>
          <span className="fact-grid-value">{valueOrEmpty(ttsSettings?.provider)}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Enabled Providers</span>
          <span className="fact-grid-value">{providerSetup?.enabled_providers?.join(", ") || "none"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Models</span>
          <span className="fact-grid-value">{models.length}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Task Families</span>
          <span className="fact-grid-value">{capabilities?.declared_task_families?.join(", ") || "none"}</span>
        </div>
      </div>

      <form className="stack" onSubmit={handleSubmit}>
        <section className="stack">
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Warm Models</p>
              <h3 className="section-title">Piper Voices</h3>
            </div>
          </div>
          {models.length === 0 ? (
            <div className="callout callout-neutral">No Piper models found.</div>
          ) : (
            <div className="tts-model-card-grid">
              {models.map((model) => (
                <ModelCard
                  key={model.model_id}
                  model={model}
                  warm={warmVoices.includes(model.model_id)}
                  onOpen={setSelectedModel}
                />
              ))}
            </div>
          )}
        </section>

        <section className="stack">
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Audio Variants</p>
              <h3 className="section-title">Conversion Rates</h3>
            </div>
          </div>
          <div className="fact-grid">
            {allowedRates.map((rate) => (
              <label className="fact-grid-item tts-checkbox-item" key={rate}>
                <input
                  type="checkbox"
                  checked={conversionRates.includes(rate)}
                  onChange={() => toggleConversionRate(rate)}
                />
                <span>
                  <span className="fact-grid-label">Sample Rate</span>
                  <span className="fact-grid-value">{formatSampleRate(rate)}</span>
                </span>
              </label>
            ))}
          </div>
        </section>

        {error ? <div className="callout callout-danger">{error}</div> : null}
        {notice ? <div className="callout callout-success">{notice}</div> : null}

        <div className="actions">
          <button className="btn btn-primary" type="submit" disabled={saving || !dirty}>
            {saving ? "Saving..." : "Save TTS Settings"}
          </button>
          <button className="btn btn-ghost" type="button" onClick={onRefresh} disabled={saving}>
            Refresh
          </button>
        </div>
      </form>
      <ModelDetailPopout
        model={selectedModel}
        warm={selectedModel ? warmVoices.includes(selectedModel.model_id) : false}
        onToggleWarm={toggleWarmVoice}
        onClose={() => setSelectedModel(null)}
      />
    </section>
  );
}
