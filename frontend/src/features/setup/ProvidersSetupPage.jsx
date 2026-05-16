import { useEffect, useState } from "react";
import { applySetupProviders, getSetupProvidersStatus, saveSetupProvidersConfig } from "../../api/client";

const providerChoices = [
  { id: "voice", label: "Voice" },
  { id: "external_faster_whisper", label: "STT" },
  { id: "piper", label: "TTS" },
  { id: "supervised_openwakeword", label: "Wake" },
];

function stateTone(state) {
  if (state === "healthy") return "success";
  if (state === "failed") return "danger";
  if (state === "warning") return "warning";
  return "neutral";
}

export function ProvidersSetupPage() {
  const [status, setStatus] = useState(null);
  const [enabled, setEnabled] = useState(["voice"]);
  const [defaultProvider, setDefaultProvider] = useState("voice");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    const payload = await getSetupProvidersStatus();
    setStatus(payload);
    setEnabled(payload.provider_setup?.enabled_providers?.length ? payload.provider_setup.enabled_providers : ["voice"]);
    setDefaultProvider(payload.provider_setup?.default_provider || "voice");
  }

  useEffect(() => {
    let mounted = true;
    getSetupProvidersStatus()
      .then((payload) => {
        if (!mounted) return;
        setStatus(payload);
        setEnabled(payload.provider_setup?.enabled_providers?.length ? payload.provider_setup.enabled_providers : ["voice"]);
        setDefaultProvider(payload.provider_setup?.default_provider || "voice");
      })
      .catch((err) => {
        if (mounted) setError(String(err.message || err));
      });
    const interval = window.setInterval(() => {
      getSetupProvidersStatus().then((payload) => mounted && setStatus(payload)).catch(() => {});
    }, 3000);
    return () => {
      mounted = false;
      window.clearInterval(interval);
    };
  }, []);

  function toggleProvider(providerId) {
    setEnabled((current) => {
      if (current.includes(providerId)) {
        return current.filter((item) => item !== providerId);
      }
      return [...current, providerId];
    });
  }

  async function saveConfig() {
    setBusy("config");
    setError("");
    setNotice("");
    try {
      const payload = await saveSetupProvidersConfig({ enabled_providers: enabled, default_provider: defaultProvider });
      setStatus((current) => ({ ...(current || {}), provider_setup: payload }));
      setNotice("Provider configuration saved.");
      await refresh();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function apply(target, action = "install") {
    setBusy(target || "apply");
    setError("");
    setNotice("");
    try {
      await applySetupProviders(target ? { target, action } : {});
      setNotice("Provider action queued.");
      await refresh();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Provider Setup</h2>
        <span className={`status-pill status-pill-${status?.continue_blocked ? "warning" : "success"}`}>
          {status?.continue_blocked ? "blocked" : "ready"}
        </span>
      </div>
      {notice ? <div className="callout callout-success">{notice}</div> : null}
      {error ? <div className="callout callout-danger">{error}</div> : null}
      {status?.blockers?.length ? <div className="callout callout-warning">Blockers: {status.blockers.join(", ")}</div> : null}

      <div className="fact-grid">
        {providerChoices.map((provider) => (
          <label className="fact-grid-item" key={provider.id}>
            <span className="fact-grid-label">{provider.label}</span>
            <span className="fact-grid-value">
              <input type="checkbox" checked={enabled.includes(provider.id)} onChange={() => toggleProvider(provider.id)} /> {provider.id}
            </span>
          </label>
        ))}
      </div>

      <label className="field">
        <span className="field-label">Default provider</span>
        <select className="field-input" value={defaultProvider} onChange={(event) => setDefaultProvider(event.target.value)}>
          {providerChoices.map((provider) => (
            <option value={provider.id} key={provider.id}>{provider.label}</option>
          ))}
        </select>
      </label>

      <div className="form-actions">
        <button className="btn btn-secondary" type="button" onClick={saveConfig} disabled={busy !== ""}>
          {busy === "config" ? "Saving..." : "Save config"}
        </button>
        <button className="btn btn-primary" type="button" onClick={() => apply()} disabled={busy !== ""}>
          {busy === "apply" ? "Applying..." : "Apply enabled"}
        </button>
      </div>

      <div className="fact-grid">
        {(status?.provider_states || []).map((provider) => (
          <div className="fact-grid-item" key={provider.provider_id}>
            <span className="fact-grid-label">{provider.provider_id}</span>
            <span className={`status-pill status-pill-${stateTone(provider.state)}`}>{provider.state}</span>
            {provider.target ? (
              <button className="btn btn-ghost" type="button" onClick={() => apply(provider.target, "restart")} disabled={busy !== ""}>
                Restart
              </button>
            ) : null}
          </div>
        ))}
      </div>
    </article>
  );
}
