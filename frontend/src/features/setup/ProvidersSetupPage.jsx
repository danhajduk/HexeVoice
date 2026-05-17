import { useEffect, useState } from "react";
import { applySetupProviders, getSetupProvidersStatus, registerSetupSupervisorRuntime, saveSetupProvidersConfig } from "../../api/client";

const providerMetadata = {
  voice: { label: "Voice", role: "Pipeline" },
  faster_whisper: { label: "STT", role: "Speech to text" },
  external_faster_whisper: { label: "STT", role: "Speech to text" },
  piper: { label: "TTS", role: "Text to speech" },
  openwakeword: { label: "Wake", role: "Wake word" },
  supervised_openwakeword: { label: "Wake", role: "Wake word" },
};

const fallbackProviderChoices = ["voice", "external_faster_whisper", "piper", "openwakeword"];
const sttProfileOptions = ["cpu_default", "cuda_fast_intent", "cuda_accurate_fallback"];
const sttModelOptions = ["tiny.en", "base.en", "small.en", "medium.en", "large-v3"];
const ttsVoiceOptions = ["en_US-kathleen-low", "en_US-lessac-medium", "en_US-jenny-high"];
const wakeModelOptions = ["Hexe"];

function stateTone(state) {
  if (state === "healthy" || state === "ready" || state === "downloaded") return "success";
  if (state === "failed" || state === "blocked") return "danger";
  if (state === "warning" || state === "pending" || state === "missing" || state === "downloading" || state === "preloading") return "warning";
  return "neutral";
}

function splitList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function providerChoicesFor(status) {
  const supported = status?.provider_setup?.supported_providers?.length
    ? status.provider_setup.supported_providers
    : fallbackProviderChoices;
  return supported.map((id) => ({
    id,
    label: providerMetadata[id]?.label || id,
    role: providerMetadata[id]?.role || "Provider",
  }));
}

function defaultProviderConfigs() {
  return {
    faster_whisper: {
      profile: "cpu_default",
      fallback_profile: "",
      model: "base.en",
      language: "en",
      device: "cpu",
      cuda_mode: "auto",
      compute_type: "int8",
      warm_model: true,
      warm_models_text: "",
    },
    external_faster_whisper: {
      profile: "cpu_default",
      fallback_profile: "",
      model: "base.en",
      language: "en",
      device: "cpu",
      cuda_mode: "auto",
      compute_type: "int8",
      warm_model: true,
      warm_models_text: "",
    },
    piper: {
      model: "en_US-kathleen-low",
      default_voice: "en_US-kathleen-low",
      language: "en_US",
      warm_models_text: "",
    },
    openwakeword: {
      model: "Hexe",
      default_wakeword: "Hexe",
      threshold: 0.5,
      warm_model: true,
      warm_models_text: "",
    },
    supervised_openwakeword: {
      model: "Hexe",
      default_wakeword: "Hexe",
      threshold: 0.5,
      warm_model: true,
      warm_models_text: "",
    },
  };
}

function providerConfigsFromStatus(status) {
  const next = defaultProviderConfigs();
  const saved = status?.provider_setup?.provider_configs || {};
  for (const providerId of Object.keys(next)) {
    const config = saved[providerId] || {};
    next[providerId] = {
      ...next[providerId],
      ...config,
      warm_models_text: Array.isArray(config.warm_models) ? config.warm_models.join(", ") : next[providerId].warm_models_text,
    };
  }
  return next;
}

function providerConfigPayload(providerConfigs, enabled, defaultProvider) {
  const payload = {};
  for (const [providerId, config] of Object.entries(providerConfigs)) {
    payload[providerId] = {
      enabled: enabled.includes(providerId),
      default: defaultProvider === providerId,
      ...config,
      warm_models: splitList(config.warm_models_text),
    };
    delete payload[providerId].warm_models_text;
  }
  return payload;
}

function providerStateFor(status, providerId) {
  return (status?.provider_states || []).find((provider) => provider.provider_id === providerId) || null;
}

export function ProvidersSetupPage() {
  const [status, setStatus] = useState(null);
  const [enabled, setEnabled] = useState(["voice"]);
  const [defaultProvider, setDefaultProvider] = useState("voice");
  const [providerConfigs, setProviderConfigs] = useState(defaultProviderConfigs);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    const payload = await getSetupProvidersStatus();
    setStatus(payload);
    setEnabled(payload.provider_setup?.enabled_providers?.length ? payload.provider_setup.enabled_providers : ["voice"]);
    setDefaultProvider(payload.provider_setup?.default_provider || "voice");
    setProviderConfigs(providerConfigsFromStatus(payload));
  }

  useEffect(() => {
    let mounted = true;
    getSetupProvidersStatus()
      .then((payload) => {
        if (!mounted) return;
        setStatus(payload);
        setEnabled(payload.provider_setup?.enabled_providers?.length ? payload.provider_setup.enabled_providers : ["voice"]);
        setDefaultProvider(payload.provider_setup?.default_provider || "voice");
        setProviderConfigs(providerConfigsFromStatus(payload));
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
        const next = current.filter((item) => item !== providerId);
        if (defaultProvider === providerId) {
          setDefaultProvider(next[0] || "voice");
        }
        return next;
      }
      const next = [...current, providerId];
      if (!defaultProvider || !next.includes(defaultProvider)) {
        setDefaultProvider(providerId);
      }
      return next;
    });
  }

  function updateProviderConfig(providerId, field, value) {
    setProviderConfigs((current) => ({
      ...current,
      [providerId]: {
        ...(current[providerId] || {}),
        [field]: value,
      },
    }));
  }

  function buildSavePayload(configs = providerConfigs, enabledProviders = enabled, defaultProviderId = defaultProvider) {
    return {
      enabled_providers: enabledProviders,
      default_provider: defaultProviderId,
      provider_configs: providerConfigPayload(configs, enabledProviders, defaultProviderId),
    };
  }

  async function saveConfig() {
    setBusy("config");
    setError("");
    setNotice("");
    try {
      const payload = await saveSetupProvidersConfig(buildSavePayload());
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
    setBusy(target || (action === "install" ? "apply" : action));
    setError("");
    setNotice("");
    try {
      await applySetupProviders(target ? { target, action } : { action });
      setNotice("Provider action queued.");
      await refresh();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function switchCudaProfile(mode) {
    if (!sttProviderId) {
      setError("No faster-whisper provider is available.");
      return;
    }
    const nextConfig = {
      ...(providerConfigs[sttProviderId] || {}),
      cuda_mode: mode,
      device: mode === "cuda" ? "cuda" : "cpu",
      compute_type: mode === "cuda" ? "float16" : "int8",
      profile: mode === "cuda" ? "cuda_fast_intent" : "cpu_default",
    };
    const nextConfigs = {
      ...providerConfigs,
      [sttProviderId]: nextConfig,
    };
    setProviderConfigs(nextConfigs);
    setBusy(`profile-${mode}`);
    setError("");
    setNotice("");
    try {
      const payload = await saveSetupProvidersConfig(buildSavePayload(nextConfigs));
      setStatus((current) => ({ ...(current || {}), provider_setup: payload }));
      setNotice(`${mode === "cuda" ? "CUDA" : "CPU"} provider profile saved.`);
      await refresh();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function registerSupervisor() {
    setBusy("supervisor-registration");
    setError("");
    setNotice("");
    try {
      const payload = await registerSetupSupervisorRuntime();
      setNotice(`Supervisor registration: ${payload.status || "requested"}.`);
      await refresh();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  const providerChoices = providerChoicesFor(status);
  const defaultProviderChoices = providerChoices.filter((provider) => enabled.includes(provider.id));
  const sttProviderId = providerChoices.some((provider) => provider.id === "external_faster_whisper")
    ? "external_faster_whisper"
    : providerChoices.some((provider) => provider.id === "faster_whisper")
      ? "faster_whisper"
      : "";
  const wakeProviderId = providerChoices.some((provider) => provider.id === "supervised_openwakeword")
    ? "supervised_openwakeword"
    : providerChoices.some((provider) => provider.id === "openwakeword")
      ? "openwakeword"
      : "";
  const sttState = sttProviderId ? providerStateFor(status, sttProviderId) : null;
  const ttsState = providerStateFor(status, "piper");
  const wakeState = wakeProviderId ? providerStateFor(status, wakeProviderId) : null;
  const cudaProfile = status?.cuda_profile || {};

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

      <section className="stack">
        <div className="section-heading-inline">
          <div>
            <p className="panel-kicker">Apply Plan</p>
            <h3 className="section-title">Provider setup actions</h3>
          </div>
        </div>
        <div className="fact-grid">
          {(status?.apply_plan || []).map((item) => (
            <div className="fact-grid-item" key={item.id}>
              <span className="fact-grid-label">{item.label}</span>
              <span className={`status-pill status-pill-${stateTone(item.status)}`}>{item.status}</span>
              <span className="fact-grid-value">{item.items?.length ? item.items.join(", ") : item.detail}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="stack">
        <div className="section-heading-inline">
          <div>
            <p className="panel-kicker">Provider Assets</p>
            <h3 className="section-title">Model download and preload state</h3>
          </div>
        </div>
        <div className="fact-grid">
          {(status?.asset_progress || []).map((asset) => {
            const assetState = busy === "download-models" && asset.state === "missing" ? "downloading" : asset.state;
            return (
              <div className="fact-grid-item" key={`${asset.provider_id}:${asset.asset_type}:${asset.asset_id}`}>
                <span className="fact-grid-label">{asset.provider_id}</span>
                <span className={`status-pill status-pill-${stateTone(assetState)}`}>{assetState}</span>
                <span className="fact-grid-value">{asset.asset_id}</span>
              </div>
            );
          })}
          {!(status?.asset_progress || []).length ? (
            <div className="fact-grid-item">
              <span className="fact-grid-label">Assets</span>
              <span className="fact-grid-value">Save provider selections to build the asset list.</span>
            </div>
          ) : null}
        </div>
      </section>

      <section className="stack">
        <div className="section-heading-inline">
          <div>
            <p className="panel-kicker">Supervisor Registration</p>
            <h3 className="section-title">Runtime services</h3>
          </div>
          <span className={`status-pill status-pill-${status?.supervisor_registration?.registered ? "success" : "warning"}`}>
            {status?.supervisor_registration?.registered ? "registered" : "pending"}
          </span>
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Node ID</span>
            <span className="fact-grid-value">{status?.supervisor_registration?.node_id || "waiting"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Services</span>
            <span className="fact-grid-value">{status?.supervisor_registration?.service_ids?.join(", ") || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Last error</span>
            <span className="fact-grid-value">{status?.supervisor_registration?.last_error || "none"}</span>
          </div>
        </div>
        <div className="form-actions">
          <button className="btn btn-secondary" type="button" onClick={registerSupervisor} disabled={busy !== "" || status?.supervisor_registration?.blocked}>
            {busy === "supervisor-registration" ? "Registering..." : "Register runtime services"}
          </button>
        </div>
      </section>

      <section className="stack">
        <div className="section-heading-inline">
          <div>
            <p className="panel-kicker">Recovery Actions</p>
            <h3 className="section-title">Provider repair tools</h3>
          </div>
        </div>
        <div className="form-actions">
          <button className="btn btn-secondary" type="button" onClick={() => apply(null, "download-models")} disabled={busy !== ""}>
            {busy === "download-models" ? "Downloading..." : "Download selected models"}
          </button>
          <button className="btn btn-secondary" type="button" onClick={() => apply(null, "preload")} disabled={busy !== ""}>
            {busy === "preload" ? "Preloading..." : "Preload selected models"}
          </button>
          <button className="btn btn-secondary" type="button" onClick={() => apply(null, "restart")} disabled={busy !== ""}>
            {busy === "restart" ? "Restarting..." : "Restart providers"}
          </button>
          <button className="btn btn-secondary" type="button" onClick={() => apply(null, "recreate")} disabled={busy !== ""}>
            {busy === "recreate" ? "Recreating..." : "Recreate containers"}
          </button>
          <button className="btn btn-secondary" type="button" onClick={() => apply(null, "rebuild-env")} disabled={busy !== ""}>
            {busy === "rebuild-env" ? "Rebuilding..." : "Rebuild env"}
          </button>
          <button className="btn btn-secondary" type="button" onClick={() => switchCudaProfile("cpu")} disabled={busy !== "" || !sttProviderId}>
            {busy === "profile-cpu" ? "Saving..." : "Force CPU profile"}
          </button>
          <button className="btn btn-secondary" type="button" onClick={() => switchCudaProfile("cuda")} disabled={busy !== "" || !sttProviderId}>
            {busy === "profile-cuda" ? "Saving..." : "Force CUDA profile"}
          </button>
          <button className="btn btn-secondary" type="button" onClick={registerSupervisor} disabled={busy !== "" || status?.supervisor_registration?.blocked}>
            {busy === "supervisor-registration" ? "Registering..." : "Re-register services"}
          </button>
        </div>
      </section>

      <div className="fact-grid">
        {providerChoices.map((provider) => (
          <label className="fact-grid-item" key={provider.id}>
            <span className="fact-grid-label">{provider.label}</span>
            <span className="fact-grid-value">
              <input type="checkbox" checked={enabled.includes(provider.id)} onChange={() => toggleProvider(provider.id)} /> {provider.id}
            </span>
            <span className="fact-grid-label">{provider.role}</span>
          </label>
        ))}
      </div>

      <label className="field">
        <span className="field-label">Default provider</span>
        <select className="field-input" value={defaultProvider} onChange={(event) => setDefaultProvider(event.target.value)}>
          {(defaultProviderChoices.length ? defaultProviderChoices : providerChoices).map((provider) => (
            <option value={provider.id} key={provider.id}>{provider.label}</option>
          ))}
        </select>
      </label>

      {sttProviderId ? (
        <section className="stack">
          <div className="section-heading-inline">
            <div>
              <p className="panel-kicker">STT Engine</p>
              <h3 className="section-title">faster-whisper</h3>
            </div>
            <span className={`status-pill status-pill-${stateTone(sttState?.state)}`}>{sttState?.state || "not enabled"}</span>
          </div>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">Profile</span>
              <select className="field-input" value={providerConfigs[sttProviderId]?.profile || "cpu_default"} onChange={(event) => updateProviderConfig(sttProviderId, "profile", event.target.value)}>
                {sttProfileOptions.map((profile) => <option key={profile} value={profile}>{profile}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Model</span>
              <select className="field-input" value={providerConfigs[sttProviderId]?.model || "base.en"} onChange={(event) => updateProviderConfig(sttProviderId, "model", event.target.value)}>
                {sttModelOptions.map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="field-label">CPU/GPU mode</span>
              <select className="field-input" value={providerConfigs[sttProviderId]?.device || "cpu"} onChange={(event) => updateProviderConfig(sttProviderId, "device", event.target.value)}>
                <option value="cpu">CPU</option>
                <option value="cuda">CUDA GPU</option>
              </select>
            </label>
            <label className="field">
              <span className="field-label">CUDA runtime</span>
              <select className="field-input" value={providerConfigs[sttProviderId]?.cuda_mode || "auto"} onChange={(event) => updateProviderConfig(sttProviderId, "cuda_mode", event.target.value)}>
                <option value="auto">Auto detect</option>
                <option value="cpu">Force CPU image</option>
                <option value="cuda">Force CUDA image</option>
                <option value="skip">Skip CUDA check</option>
              </select>
            </label>
            <label className="field">
              <span className="field-label">Compute type</span>
              <select className="field-input" value={providerConfigs[sttProviderId]?.compute_type || "int8"} onChange={(event) => updateProviderConfig(sttProviderId, "compute_type", event.target.value)}>
                <option value="int8">int8</option>
                <option value="float16">float16</option>
                <option value="int8_float16">int8_float16</option>
                <option value="float32">float32</option>
              </select>
            </label>
            <label className="field">
              <span className="field-label">Language</span>
              <input className="field-input" value={providerConfigs[sttProviderId]?.language || ""} onChange={(event) => updateProviderConfig(sttProviderId, "language", event.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">Fallback profile</span>
              <select className="field-input" value={providerConfigs[sttProviderId]?.fallback_profile || ""} onChange={(event) => updateProviderConfig(sttProviderId, "fallback_profile", event.target.value)}>
                <option value="">None</option>
                {sttProfileOptions.map((profile) => <option key={profile} value={profile}>{profile}</option>)}
              </select>
            </label>
          </div>
          <label className="field">
            <span className="field-label">Preload/download models</span>
            <input className="field-input" value={providerConfigs[sttProviderId]?.warm_models_text || ""} onChange={(event) => updateProviderConfig(sttProviderId, "warm_models_text", event.target.value)} placeholder="base.en, small.en" />
          </label>
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">CUDA recommendation</span>
              <span className="fact-grid-value">{cudaProfile.recommended_mode || "pending"}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Selected image</span>
              <span className="fact-grid-value">{cudaProfile.selected_image || "cpu"}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Docker CUDA validation</span>
              <span className="fact-grid-value">{cudaProfile.validation_state || "not_checked"}</span>
            </div>
          </div>
          {cudaProfile.warning ? <div className="callout callout-warning">{cudaProfile.warning}</div> : null}
          <div className="form-actions">
            <button className="btn btn-secondary" type="button" onClick={() => apply(sttState?.target, "cuda-preflight")} disabled={busy !== "" || !sttState?.target}>
              {busy === sttState?.target ? "Checking..." : "Validate CUDA"}
            </button>
          </div>
        </section>
      ) : null}

      {providerChoices.some((provider) => provider.id === "piper") ? (
        <section className="stack">
          <div className="section-heading-inline">
            <div>
              <p className="panel-kicker">TTS Engine</p>
              <h3 className="section-title">Piper</h3>
            </div>
            <span className={`status-pill status-pill-${stateTone(ttsState?.state)}`}>{ttsState?.state || "not enabled"}</span>
          </div>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">Voice/model</span>
              <select className="field-input" value={providerConfigs.piper?.default_voice || "en_US-kathleen-low"} onChange={(event) => {
                updateProviderConfig("piper", "default_voice", event.target.value);
                updateProviderConfig("piper", "model", event.target.value);
              }}>
                {ttsVoiceOptions.map((voice) => <option key={voice} value={voice}>{voice}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Language</span>
              <input className="field-input" value={providerConfigs.piper?.language || "en_US"} onChange={(event) => updateProviderConfig("piper", "language", event.target.value)} />
            </label>
            <label className="field field-span-2">
              <span className="field-label">Preload/download voices</span>
              <input className="field-input" value={providerConfigs.piper?.warm_models_text || ""} onChange={(event) => updateProviderConfig("piper", "warm_models_text", event.target.value)} placeholder="en_US-kathleen-low" />
            </label>
          </div>
        </section>
      ) : null}

      {wakeProviderId ? (
        <section className="stack">
          <div className="section-heading-inline">
            <div>
              <p className="panel-kicker">Wake Word</p>
              <h3 className="section-title">openWakeWord</h3>
            </div>
            <span className={`status-pill status-pill-${stateTone(wakeState?.state)}`}>{wakeState?.state || "not enabled"}</span>
          </div>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">Wake model</span>
              <select className="field-input" value={providerConfigs[wakeProviderId]?.default_wakeword || "Hexe"} onChange={(event) => {
                updateProviderConfig(wakeProviderId, "default_wakeword", event.target.value);
                updateProviderConfig(wakeProviderId, "model", event.target.value);
              }}>
                {wakeModelOptions.map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Threshold</span>
              <input className="field-input" type="number" min="0" max="1" step="0.05" value={providerConfigs[wakeProviderId]?.threshold ?? 0.5} onChange={(event) => updateProviderConfig(wakeProviderId, "threshold", Number(event.target.value))} />
            </label>
            <label className="field field-span-2">
              <span className="field-label">Preload/download wake models</span>
              <input className="field-input" value={providerConfigs[wakeProviderId]?.warm_models_text || ""} onChange={(event) => updateProviderConfig(wakeProviderId, "warm_models_text", event.target.value)} placeholder="Hexe" />
            </label>
          </div>
        </section>
      ) : null}

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
            <span className="fact-grid-value">{provider.component?.model_display_name || provider.component?.model || provider.component?.provider || "configured"}</span>
            <span className="fact-grid-label">
              {provider.component?.socket_path || provider.component?.base_url || provider.component?.host || "local"}
              {provider.component?.port ? `:${provider.component.port}` : ""}
            </span>
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
