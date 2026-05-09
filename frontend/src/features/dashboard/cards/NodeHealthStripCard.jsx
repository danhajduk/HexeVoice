function healthIndicatorClass(connected) {
  return connected ? "health-connected" : "health-pending";
}

function providerHealth(provider) {
  if (!provider || typeof provider !== "object") {
    return { label: "pending", healthy: false };
  }
  if (provider.healthy === false || provider.configured === false || provider.error || provider.last_error) {
    return { label: "degraded", healthy: false };
  }
  return {
    label: provider.provider || provider.provider_id || provider.model || "ready",
    healthy: true,
  };
}

export function NodeHealthStripCard({ status, onboarding, providerSetup, governance, operational, voiceStatus }) {
  const lifecycleLabel = status?.operational_ready
    ? "operational"
    : onboarding?.current_step_label || status?.current_step_label || "pending";
  const coreConnected = Boolean(status?.trust_state === "trusted" || onboarding?.session_id);
  const governanceFresh = (operational?.governance_freshness_state || status?.governance_freshness_state) === "fresh";
  const providersConfigured = Boolean(providerSetup?.enabled_providers?.length);
  const sttHealth = providerHealth(voiceStatus?.turn_pipeline?.stt);
  const ttsHealth = providerHealth(voiceStatus?.turn_pipeline?.tts);

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
          <span className="muted tiny">Core API</span>
          <span className={coreConnected ? "severity-indicator severity-success" : "severity-indicator severity-warning"}>
            <span className={`health-indicator ${healthIndicatorClass(coreConnected)}`}>
              <span className="health-dot" />
              {coreConnected ? "connected" : "pending"}
            </span>
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
          <span className="muted tiny">Providers</span>
          <span className={providersConfigured ? "severity-indicator severity-meta" : "severity-indicator severity-warning"}>
            <span className="status-badge status-configured">
              {providersConfigured ? "configured" : "pending"}
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
          <span className="muted tiny">TTS</span>
          <span className={ttsHealth.healthy ? "severity-indicator severity-success" : "severity-indicator severity-warning"}>
            <span className={`health-indicator ${ttsHealth.healthy ? "health-connected" : "health-pending"}`}>
              <span className="health-dot" />
              {ttsHealth.label}
            </span>
          </span>
        </div>
      </div>
    </article>
  );
}
