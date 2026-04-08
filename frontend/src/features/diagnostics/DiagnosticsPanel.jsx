export function DiagnosticsPanel({ status, onboarding, operational, onRefresh }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="panel-kicker">Node Context</p>
          <h2 className="panel-title">Diagnostics</h2>
        </div>
      </div>
      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Node ID</span>
          <span className="fact-grid-value">
            <code className="inline-code">{status?.node_id || "pending"}</code>
          </span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Provider Namespace</span>
          <span className="fact-grid-value">
            <code className="inline-code">voice</code>
          </span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Current step</span>
          <span className="fact-grid-value">{onboarding?.current_step_label || status?.current_step_label || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Operational freshness</span>
          <span className="fact-grid-value">{operational?.governance_freshness_state || status?.governance_freshness_state || "pending"}</span>
        </div>
      </div>
      <div className="state-grid">
        <div className="state-row">
          <span className="state-label">Trust state</span>
          <span className="state-value">{status?.trust_state || "pending"}</span>
        </div>
        <div className="state-row">
          <span className="state-label">Readiness blockers</span>
          <span className="state-value">{(status?.blocking_reasons || []).join(", ") || "none"}</span>
        </div>
      </div>
      <div className="action-group">
        <button className="btn btn-secondary" type="button" onClick={onRefresh}>
          Refresh all panels
        </button>
        <button className="btn btn-secondary" type="button" disabled>
          Export diagnostics next
        </button>
      </div>
    </section>
  );
}
