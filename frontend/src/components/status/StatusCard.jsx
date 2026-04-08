export function StatusCard({ status, onboarding, error }) {
  const blockers = status?.blocking_reasons || [];

  return (
    <section className="status-card">
      <div className="status-header">
        <div>
          <p className="panel-kicker">Runtime Summary</p>
          <h2>Status Projection</h2>
        </div>
        <span className="status-pill status-pill-neutral">{status?.node_id || "node pending"}</span>
      </div>
      {error ? <div className="callout callout-danger">{error}</div> : null}
      <div className="status-grid">
        <div className="status-item">
          <span className="status-label">Current Step</span>
          <span className="status-value">{onboarding?.current_step_label || status?.current_step_label || "loading"}</span>
        </div>
        <div className="status-item">
          <span className="status-label">Blocking Reasons</span>
          <span className="status-value">{blockers.length}</span>
        </div>
        <div className="status-item">
          <span className="status-label">Capability State</span>
          <span className="status-value">{status?.capability_status || "missing"}</span>
        </div>
        <div className="status-item">
          <span className="status-label">Governance State</span>
          <span className="status-value">{onboarding?.governance_sync_status || status?.governance_sync_status || "pending"}</span>
        </div>
      </div>
      <div className="callout">
        Core readiness remains authoritative. The local node API now exposes the same setup gating details the next
        onboarding shell will use.
      </div>
      <pre className="code-panel">{JSON.stringify(status || { status: "loading" }, null, 2)}</pre>
    </section>
  );
}
