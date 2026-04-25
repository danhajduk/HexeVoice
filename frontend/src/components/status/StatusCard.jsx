export function StatusCard({ status, onboarding, error }) {
  const blockers = status?.blocking_reasons || [];

  return (
    <section className="card stack status-card">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Runtime Summary</p>
          <h2>Node Status</h2>
        </div>
        <span className="pill">{status?.node_id || "node pending"}</span>
      </div>
      {error ? <div className="callout callout-danger">{error}</div> : null}
      <dl className="facts">
        <div>
          <dt>Current step</dt>
          <dd>{onboarding?.current_step_label || status?.current_step_label || "loading"}</dd>
        </div>
        <div>
          <dt>Lifecycle</dt>
          <dd>{onboarding?.lifecycle_state || status?.lifecycle_state || "loading"}</dd>
        </div>
        <div>
          <dt>Capability state</dt>
          <dd>{status?.capability_status || "missing"}</dd>
        </div>
        <div>
          <dt>Governance state</dt>
          <dd>{onboarding?.governance_sync_status || status?.governance_sync_status || "pending"}</dd>
        </div>
      </dl>
      <div className={`callout ${blockers.length ? "callout-warning" : "callout-success"}`}>
        {blockers.length ? `Current blockers: ${blockers.join(", ")}` : "No current readiness blockers are reported."}
      </div>
    </section>
  );
}
