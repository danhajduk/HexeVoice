import { useEffect, useState } from "react";
import { applySetupProviders, getSetupProvidersStatus, registerSetupSupervisorRuntime, saveSetupProvidersConfig } from "../../api/client";

const providerMetadata = {
  voice: { label: "Voice pipeline", role: "Required voice runtime" },
  faster_whisper: { label: "STT", role: "Speech to text" },
  external_faster_whisper: { label: "STT", role: "Speech to text" },
  piper: { label: "TTS", role: "Text to speech" },
  openwakeword: { label: "Wake", role: "Wake word" },
  supervised_openwakeword: { label: "Wake", role: "Wake word" },
};

const fallbackProviderChoices = ["voice", "external_faster_whisper", "piper", "openwakeword"];
const providerSteps = [
  { id: "providers", label: "Choose providers" },
  { id: "stt", label: "Setup STT" },
  { id: "tts", label: "Setup TTS" },
  { id: "wake", label: "Setup wake" },
];
const requiredProviderId = "voice";
const providerAliasGroups = [["openwakeword", "supervised_openwakeword"]];
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

function aliasesForProvider(providerId) {
  return providerAliasGroups.find((group) => group.includes(providerId)) || [providerId];
}

function providerSelected(enabledProviders, providerId) {
  const aliases = aliasesForProvider(providerId);
  return aliases.some((alias) => enabledProviders.includes(alias));
}

function expandProviderAliases(providers) {
  const next = [];
  for (const provider of providers || []) {
    for (const alias of aliasesForProvider(provider)) {
      if (!alias || next.includes(alias)) continue;
      next.push(alias);
    }
  }
  return next;
}

function providerChoicesFor(status) {
  const supported = status?.provider_setup?.supported_providers?.length
    ? status.provider_setup.supported_providers
    : fallbackProviderChoices;
  const displaySupported = supported.includes("supervised_openwakeword")
    ? supported.filter((id) => id !== "openwakeword")
    : supported;
  return displaySupported.map((id) => ({
    id,
    label: providerMetadata[id]?.label || id,
    role: providerMetadata[id]?.role || "Provider",
  }));
}

function normalizedEnabledProviders(providers) {
  const next = [];
  for (const provider of [requiredProviderId, ...expandProviderAliases(providers || [])]) {
    if (!provider || next.includes(provider)) continue;
    next.push(provider);
  }
  return next;
}

function initialEnabledProviders(status) {
  const saved = status?.provider_setup?.enabled_providers || [];
  if (status?.provider_setup?.configured && saved.length) {
    return normalizedEnabledProviders(saved);
  }
  return normalizedEnabledProviders(providerChoicesFor(status).map((provider) => provider.id));
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
  const savedWithAliases = {
    ...saved,
    supervised_openwakeword: saved.supervised_openwakeword || saved.openwakeword,
    openwakeword: saved.openwakeword || saved.supervised_openwakeword,
  };
  for (const providerId of Object.keys(next)) {
    const config = savedWithAliases[providerId] || {};
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

function assetMatchesProvider(asset, providerId) {
  return aliasesForProvider(providerId).includes(asset.provider_id);
}

function busyKeyFor(target, action) {
  return target ? `${target}:${action}` : action === "install" ? "apply" : action;
}

function EngineActions({ state, busy, onAction }) {
  const target = state?.target;
  const disabled = !target || busy !== "";
  const actions = [
    ["download-models", "Download models"],
    ["preload", "Preload models"],
    ["restart", "Restart"],
    ["install", "Rebuild container"],
  ];
  return (
    <div className="engine-card-actions">
      {actions.map(([action, label]) => (
        <button className="btn btn-secondary" type="button" key={action} onClick={() => onAction(target, action)} disabled={disabled}>
          {busy === busyKeyFor(target, action) ? "Working..." : label}
        </button>
      ))}
    </div>
  );
}

function ProviderAssets({ status, providerId, busy }) {
  const assets = (status?.asset_progress || []).filter((asset) => assetMatchesProvider(asset, providerId));
  return (
    <div className="fact-grid">
      {assets.map((asset) => {
        const assetState = busy === busyKeyFor(providerStateFor(status, providerId)?.target, "download-models") && asset.state === "missing"
          ? "downloading"
          : asset.state;
        return (
          <div className="fact-grid-item" key={`${asset.provider_id}:${asset.asset_type}:${asset.asset_id}`}>
            <span className="fact-grid-label">{asset.asset_type}</span>
            <span className={`status-pill status-pill-${stateTone(assetState)}`}>{assetState}</span>
            <span className="fact-grid-value">{asset.asset_id}</span>
          </div>
        );
      })}
      {!assets.length ? (
        <div className="fact-grid-item">
          <span className="fact-grid-label">Assets</span>
          <span className="fact-grid-value">Save this step to build the asset list.</span>
        </div>
      ) : null}
    </div>
  );
}

export function ProvidersSetupPage() {
  const [status, setStatus] = useState(null);
  const [enabled, setEnabled] = useState([requiredProviderId]);
  const [providerConfigs, setProviderConfigs] = useState(defaultProviderConfigs);
  const [providerStep, setProviderStep] = useState("providers");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    const payload = await getSetupProvidersStatus();
    setStatus(payload);
    setEnabled(initialEnabledProviders(payload));
    setProviderConfigs(providerConfigsFromStatus(payload));
    return payload;
  }

  useEffect(() => {
    let mounted = true;
    getSetupProvidersStatus()
      .then((payload) => {
        if (!mounted) return;
        setStatus(payload);
        setEnabled(initialEnabledProviders(payload));
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

  const providerChoices = providerChoicesFor(status);
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
  const enabledSteps = providerSteps.filter((step) => {
    if (step.id === "stt") return sttProviderId && providerSelected(enabled, sttProviderId);
    if (step.id === "tts") return providerSelected(enabled, "piper");
    if (step.id === "wake") return wakeProviderId && providerSelected(enabled, wakeProviderId);
    return true;
  });

  function nextStepAfter(stepId) {
    const index = enabledSteps.findIndex((step) => step.id === stepId);
    return enabledSteps[index + 1]?.id || "";
  }

  function toggleProvider(providerId) {
    if (providerId === requiredProviderId) return;
    setEnabled((current) => {
      if (providerSelected(current, providerId)) {
        const aliases = aliasesForProvider(providerId);
        return normalizedEnabledProviders(current.filter((item) => !aliases.includes(item)));
      }
      return normalizedEnabledProviders([...current, providerId]);
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

  function buildSavePayload(configs = providerConfigs, enabledProviders = enabled) {
    const normalizedEnabled = normalizedEnabledProviders(enabledProviders);
    return {
      enabled_providers: normalizedEnabled,
      default_provider: requiredProviderId,
      provider_configs: providerConfigPayload(configs, normalizedEnabled, requiredProviderId),
    };
  }

  async function persistProviderConfig(configs = providerConfigs, enabledProviders = enabled) {
    const payload = await saveSetupProvidersConfig(buildSavePayload(configs, enabledProviders));
    setStatus((current) => ({ ...(current || {}), provider_setup: payload }));
    await refresh();
    return payload;
  }

  async function saveConfig() {
    setBusy("config");
    setError("");
    setNotice("");
    try {
      await persistProviderConfig();
      setNotice("Provider configuration saved.");
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function continueSetup() {
    const nextStep = nextStepAfter(providerStep);
    setBusy(`continue-${providerStep}`);
    setError("");
    setNotice("");
    try {
      await persistProviderConfig();
      if (!nextStep) {
        await applySetupProviders({ action: "install" });
        setNotice("Provider setup saved and runtime install queued.");
      } else {
        setProviderStep(nextStep);
        setNotice("Provider setup saved.");
      }
      await refresh();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function apply(target, action = "install") {
    setBusy(busyKeyFor(target, action));
    setError("");
    setNotice("");
    try {
      await persistProviderConfig();
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
      await persistProviderConfig(nextConfigs);
      setNotice(`${mode === "cuda" ? "CUDA" : "CPU"} provider profile saved.`);
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

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Provider Setup</h2>
        <span className={`status-pill status-pill-${status?.continue_blocked ? "warning" : "success"}`}>
          {status?.continue_blocked ? "blocked" : "ready"}
        </span>
      </div>

      <div className="setup-substep-list">
        {providerSteps.map((step, index) => {
          const enabledStep = enabledSteps.some((enabledStepItem) => enabledStepItem.id === step.id);
          const active = providerStep === step.id;
          const complete = enabledSteps.findIndex((enabledStepItem) => enabledStepItem.id === providerStep) > enabledSteps.findIndex((enabledStepItem) => enabledStepItem.id === step.id);
          return (
            <button
              className={`setup-substep-pill ${active ? "setup-substep-pill-active" : ""} ${complete ? "setup-substep-pill-complete" : ""}`}
              type="button"
              key={step.id}
              onClick={() => enabledStep && setProviderStep(step.id)}
              disabled={!enabledStep}
            >
              <span>{index + 1}</span>
              {step.label}
            </button>
          );
        })}
      </div>

      {notice ? <div className="callout callout-success">{notice}</div> : null}
      {error ? <div className="callout callout-danger">{error}</div> : null}
      {status?.blockers?.length ? <div className="callout callout-warning">Blockers: {status.blockers.join(", ")}</div> : null}

      {providerStep === "providers" ? (
        <section className="stack">
          <div className="section-heading-inline">
            <div>
              <p className="panel-kicker">Provider Engines</p>
              <h3 className="section-title">Choose runtime blocks</h3>
            </div>
            <span className="status-pill status-pill-success">All supported providers start selected</span>
          </div>
          <div className="choice-list provider-choice-list">
            {providerChoices.map((provider) => {
              const selected = providerSelected(enabled, provider.id);
              const required = provider.id === requiredProviderId;
              return (
                <button
                  className={`choice-card provider-choice-card ${selected ? "choice-card-selected" : ""}`}
                  type="button"
                  key={provider.id}
                  onClick={() => toggleProvider(provider.id)}
                  disabled={required}
                >
                  <span className="choice-check">{selected ? "✓" : ""}</span>
                  <span className="choice-copy">
                    <strong>{provider.label}</strong>
                    <span>{provider.id}</span>
                    <span>{required ? "Required" : provider.role}</span>
                  </span>
                </button>
              );
            })}
          </div>
          <div className="form-actions">
            <button className="btn btn-primary" type="button" onClick={continueSetup} disabled={busy !== ""}>
              {busy === "continue-providers" ? "Saving..." : "Continue"}
            </button>
          </div>
        </section>
      ) : null}

      {providerStep === "stt" && sttProviderId ? (
        <section className="stack engine-setup-card">
          <div className="section-heading-inline">
            <div>
              <p className="panel-kicker">STT Engine</p>
              <h3 className="section-title">faster-whisper</h3>
            </div>
            <span className={`status-pill status-pill-${stateTone(sttState?.state)}`}>{sttState?.state || "not enabled"}</span>
          </div>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">Runtime preset</span>
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
            <label className="field field-span-2">
              <span className="field-label">Additional models to download/preload</span>
              <input className="field-input" value={providerConfigs[sttProviderId]?.warm_models_text || ""} onChange={(event) => updateProviderConfig(sttProviderId, "warm_models_text", event.target.value)} placeholder="base.en, small.en" />
            </label>
          </div>
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
          <ProviderAssets status={status} providerId={sttProviderId} busy={busy} />
          <EngineActions state={sttState} busy={busy} onAction={apply} />
          <div className="form-actions">
            <button className="btn btn-secondary" type="button" onClick={() => apply(sttState?.target, "cuda-preflight")} disabled={busy !== "" || !sttState?.target}>
              {busy === busyKeyFor(sttState?.target, "cuda-preflight") ? "Checking..." : "Validate CUDA"}
            </button>
            <button className="btn btn-secondary" type="button" onClick={() => switchCudaProfile("cpu")} disabled={busy !== ""}>
              {busy === "profile-cpu" ? "Saving..." : "Force CPU profile"}
            </button>
            <button className="btn btn-secondary" type="button" onClick={() => switchCudaProfile("cuda")} disabled={busy !== ""}>
              {busy === "profile-cuda" ? "Saving..." : "Force CUDA profile"}
            </button>
            <button className="btn btn-primary" type="button" onClick={continueSetup} disabled={busy !== ""}>
              {busy === "continue-stt" ? "Saving..." : "Continue"}
            </button>
          </div>
        </section>
      ) : null}

      {providerStep === "tts" && providerSelected(enabled, "piper") ? (
        <section className="stack engine-setup-card">
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
              <span className="field-label">Additional voices to download/preload</span>
              <input className="field-input" value={providerConfigs.piper?.warm_models_text || ""} onChange={(event) => updateProviderConfig("piper", "warm_models_text", event.target.value)} placeholder="en_US-kathleen-low" />
            </label>
          </div>
          <ProviderAssets status={status} providerId="piper" busy={busy} />
          <EngineActions state={ttsState} busy={busy} onAction={apply} />
          <div className="form-actions">
            <button className="btn btn-primary" type="button" onClick={continueSetup} disabled={busy !== ""}>
              {busy === "continue-tts" ? "Saving..." : "Continue"}
            </button>
          </div>
        </section>
      ) : null}

      {providerStep === "wake" && wakeProviderId ? (
        <section className="stack engine-setup-card">
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
              <span className="field-label">Additional wake models to download/preload</span>
              <input className="field-input" value={providerConfigs[wakeProviderId]?.warm_models_text || ""} onChange={(event) => updateProviderConfig(wakeProviderId, "warm_models_text", event.target.value)} placeholder="Hexe" />
            </label>
          </div>
          <ProviderAssets status={status} providerId={wakeProviderId} busy={busy} />
          <EngineActions state={wakeState} busy={busy} onAction={apply} />
          <div className="form-actions">
            <button className="btn btn-secondary" type="button" onClick={saveConfig} disabled={busy !== ""}>
              {busy === "config" ? "Saving..." : "Save config"}
            </button>
            <button className="btn btn-primary" type="button" onClick={continueSetup} disabled={busy !== ""}>
              {busy === "continue-wake" ? "Applying..." : "Continue"}
            </button>
          </div>
        </section>
      ) : null}

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
            <p className="panel-kicker">Provider State</p>
            <h3 className="section-title">Selected runtime health</h3>
          </div>
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
            </div>
          ))}
        </div>
      </section>
    </article>
  );
}
