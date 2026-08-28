function healthIndicatorClass(connected) {
  return connected ? "health-connected" : "health-pending";
}

function providerHealth(provider, fallback = "pending") {
  if (!provider || typeof provider !== "object") {
    return { label: fallback, healthy: false };
  }
  if (provider.healthy === false || provider.configured === false || provider.error || provider.last_error) {
    return { label: "degraded", healthy: false };
  }
  return {
    label: provider.provider || provider.provider_id || provider.model || "ready",
    healthy: true,
  };
}

function wakeLabel(voiceStatus) {
  const models = voiceStatus?.wake_provider?.models;
  if (Array.isArray(models) && models.length) {
    return models[0];
  }
  return voiceStatus?.wake_provider?.healthy ? "ready" : "wake";
}

export function NodeHealthStripCard({ status, onboarding, governance, operational, voiceStatus }) {
  const lifecycleLabel = status?.operational_ready
    ? "operational"
    : onboarding?.current_step_label || status?.current_step_label || "pending";
  const governanceFresh = (operational?.governance_freshness_state || status?.governance_freshness_state) === "fresh";
  const wakeHealth = providerHealth({ ...voiceStatus?.wake_provider, provider: wakeLabel(voiceStatus) }, "wake");
  const sttHealth = providerHealth(
    { ...voiceStatus?.turn_pipeline?.stt, provider: voiceStatus?.turn_pipeline?.stt?.model },
    "stt",
  );
  const assistantHealth = providerHealth(
    { ...voiceStatus?.turn_pipeline?.assistant, provider: "local" },
    "assistant",
  );
  const ttsHealth = providerHealth(
    { ...voiceStatus?.turn_pipeline?.tts, provider: voiceStatus?.turn_pipeline?.tts?.provider || "piper" },
    "tts",
  );
  const endpointsOnline = Boolean(voiceStatus?.connection_count);
  const endpointLabel = endpointsOnline ? `${voiceStatus.connection_count} online` : "none";

  return (
    <article className="card node-health-strip operational-content-header">
      <div className="node-health-strip-grid">
        <div className="node-health-strip-item">
          <span className="muted tiny">Lifecycle</span>
          <span className="severity-indicator severity-success">
            <span className="status-badge status-operational">{lifecycleLabel}</span>
          </span>
        </div>
        <div className="node-health-strip-item">
          <span className="muted tiny">Trust</span>
          <span className={status?.trust_state === "trusted" ? "severity-indicator severity-success" : "severity-indicator severity-warning"}>
            <span className="status-badge status-trusted">{status?.trust_state || "untrusted"}</span>
          </span>
        </div>
        <div className="node-health-strip-item">
          <span className="muted tiny">Governance</span>
          <span className={governanceFresh ? "severity-indicator severity-success" : "severity-indicator severity-warning"}>
            <span className={`health-indicator ${governanceFresh ? "health-fresh" : "health-pending"}`}>
              <span className="health-dot" />
              {operational?.governance_freshness_state || status?.governance_freshness_state || governance?.governance_version || "pending"}
            </span>
          </span>
        </div>
        <div className="node-health-strip-item">
          <span className="muted tiny">Wake</span>
          <span className={wakeHealth.healthy ? "severity-indicator severity-success" : "severity-indicator severity-warning"}>
            <span className={`health-indicator ${wakeHealth.healthy ? "health-connected" : "health-pending"}`}>
              <span className="health-dot" />
              {wakeHealth.label}
            </span>
          </span>
        </div>
        <div className="node-health-strip-item">
          <span className="muted tiny">STT</span>
          <span className={sttHealth.healthy ? "severity-indicator severity-success" : "severity-indicator severity-warning"}>
            <span className={`health-indicator ${sttHealth.healthy ? "health-connected" : "health-pending"}`}>
              <span className="health-dot" />
              {sttHealth.label}
            </span>
          </span>
        </div>
        <div className="node-health-strip-item">
          <span className="muted tiny">Assistant</span>
          <span className={assistantHealth.healthy ? "severity-indicator severity-success" : "severity-indicator severity-warning"}>
            <span className={`health-indicator ${assistantHealth.healthy ? "health-connected" : "health-pending"}`}>
              <span className="health-dot" />
              {assistantHealth.label}
            </span>
          </span>
        </div>
        <div className="node-health-strip-item">
          <span className="muted tiny">TTS</span>
          <span className={ttsHealth.healthy ? "severity-indicator severity-success" : "severity-indicator severity-warning"}>
            <span className={`health-indicator ${ttsHealth.healthy ? "health-connected" : "health-pending"}`}>
              <span className="health-dot" />
              {ttsHealth.label}
            </span>
          </span>
        </div>
        <div className="node-health-strip-item">
          <span className="muted tiny">Endpoints</span>
          <span className={endpointsOnline ? "severity-indicator severity-success" : "severity-indicator severity-warning"}>
            <span className={`health-indicator ${healthIndicatorClass(endpointsOnline)}`}>
              <span className="health-dot" />
              {endpointLabel}
            </span>
          </span>
        </div>
      </div>
    </article>
  );
}
