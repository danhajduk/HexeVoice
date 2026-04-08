export function OperationalPanel({ status }) {
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
          <span className="fact-grid-value">{String(status?.operational_ready ?? false)}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Governance Freshness</span>
          <span className="fact-grid-value">{status?.governance_freshness_state || "pending"}</span>
        </div>
      </div>
      <div className="callout callout-warning">
        Blocking reasons: {(status?.blocking_reasons || []).join(", ") || "none"}
      </div>
    </section>
  );
}
