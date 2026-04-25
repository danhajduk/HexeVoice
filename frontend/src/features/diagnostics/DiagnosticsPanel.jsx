export function DiagnosticsPanel({ status, onboarding, operational, onRefresh }) {
  return (
    <section className="card stack panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Node Context</p>
          <h2 className="panel-title">Diagnostics</h2>
        </div>
      </div>
      <dl className="facts">
        <div>
          <dt>Node ID</dt>
          <dd><code className="inline-code">{status?.node_id || "pending"}</code></dd>
        </div>
        <div>
          <dt>Provider namespace</dt>
          <dd><code className="inline-code">voice</code></dd>
        </div>
        <div>
          <dt>Current step</dt>
          <dd>{onboarding?.current_step_label || status?.current_step_label || "pending"}</dd>
        </div>
        <div>
          <dt>Operational freshness</dt>
          <dd>{operational?.governance_freshness_state || status?.governance_freshness_state || "pending"}</dd>
        </div>
      </dl>
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
      <div className="actions">
        <button className="btn btn-ghost" type="button" onClick={onRefresh}>
          Refresh all panels
        </button>
        <button className="btn btn-ghost" type="button" disabled>
          Export diagnostics next
        </button>
      </div>
    </section>
  );
}
