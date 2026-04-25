function healthIndicatorClass(connected) {
  return connected ? "health-connected" : "health-pending";
}

export function NodeHealthStripCard({ status, onboarding, providerSetup, governance, operational }) {
  const lifecycleLabel = status?.operational_ready
    ? "operational"
    : onboarding?.current_step_label || status?.current_step_label || "pending";
  const coreConnected = Boolean(status?.trust_state === "trusted" || onboarding?.session_id);
  const governanceFresh = (operational?.governance_freshness_state || status?.governance_freshness_state) === "fresh";
  const providersConfigured = Boolean(providerSetup?.enabled_providers?.length);

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
          <span className="muted tiny">Capabilities</span>
          <code>{status?.capability_status || onboarding?.capability_status || "missing"}</code>
        </div>
        <div className="node-health-strip-item">
          <span className="muted tiny">Node ID</span>
          <code>{status?.node_id || "pending"}</code>
        </div>
      </div>
    </article>
  );
}
