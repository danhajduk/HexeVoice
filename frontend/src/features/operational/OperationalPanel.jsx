export function OperationalPanel({ status, operational, governance }) {
  const blockers = status?.blocking_reasons || [];

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="panel-kicker">Readiness</p>
          <h2 className="panel-title">Operational</h2>
        </div>
        <span className={`status-pill ${status?.operational_ready ? "status-pill-success" : "status-pill-warning"}`}>
          {status?.operational_ready ? "Ready" : "Blocked"}
        </span>
      </div>
      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Readiness Flag</span>
          <span className="fact-grid-value">{String(operational?.operational_ready ?? status?.operational_ready ?? false)}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Governance Freshness</span>
          <span className="fact-grid-value">{operational?.governance_freshness_state || status?.governance_freshness_state || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Capability State</span>
          <span className="fact-grid-value">{operational?.capability_status || status?.capability_status || "missing"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Governance Version</span>
          <span className="fact-grid-value">{operational?.active_governance_version || governance?.governance_version || "pending"}</span>
        </div>
      </div>
      <div className="state-grid">
        <div className="state-row">
          <span className="state-label">Operational lifecycle</span>
          <span className="state-value">{operational?.lifecycle_state || status?.lifecycle_state || "pending"}</span>
        </div>
        <div className="state-row">
          <span className="state-label">Governance status</span>
          <span className="state-value">{operational?.governance_status || status?.governance_sync_status || "pending_capability"}</span>
        </div>
        <div className="state-row">
          <span className="state-label">Last governance issue</span>
          <span className="state-value">{operational?.last_governance_issued_at || governance?.issued_timestamp || "pending"}</span>
        </div>
        <div className="state-row">
          <span className="state-label">Last refresh request</span>
          <span className="state-value">{operational?.last_governance_refresh_request_at || "pending"}</span>
        </div>
      </div>
      {operational?.governance_outdated ? (
        <div className="warning-card">
          <strong>Governance is outdated.</strong>
          <span>Core remains visible, but governance-dependent work stays blocked until refresh succeeds.</span>
        </div>
      ) : (
        <div className="callout callout-warning">Blocking reasons: {blockers.join(", ") || "none"}</div>
      )}
      <div className="action-group">
        <button className="btn btn-secondary" type="button" disabled>
          Runtime controls next
        </button>
        <button className="btn btn-secondary" type="button" disabled>
          Telemetry actions next
        </button>
      </div>
    </section>
  );
}
