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
          <div className="voice-endpoint-table-wrap">
            <table className="voice-endpoint-status-table">
              <thead>
                <tr>
                  <th scope="col">Warm</th>
                  <th scope="col">Model</th>
                  <th scope="col">Raw Rate</th>
                  <th scope="col">Quality</th>
                  <th scope="col">Language</th>
                </tr>
              </thead>
              <tbody>
                {models.length === 0 ? (
                  <tr>
                    <td colSpan="5">No Piper models found.</td>
                  </tr>
                ) : (
                  models.map((model) => (
                    <tr key={model.model_id}>
                      <td>
                        <input
                          type="checkbox"
                          checked={warmVoices.includes(model.model_id)}
                          onChange={() => toggleWarmVoice(model.model_id)}
                          aria-label={`Keep ${model.model_id} warm`}
                        />
                      </td>
                      <td>{model.model_id}</td>
                      <td>{formatSampleRate(model.raw_sample_rate_hz)}</td>
                      <td>{valueOrEmpty(model.quality)}</td>
                      <td>{valueOrEmpty(model.language)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
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
    </section>
  );
}
