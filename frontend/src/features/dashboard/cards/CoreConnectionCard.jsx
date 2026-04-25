export function CoreConnectionCard({ status, onboarding, governance, operational }) {
  return (
    <section className="card stack panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Core Link</p>
          <h2 className="panel-title">Core And Governance</h2>
        </div>
      </div>
      <dl className="facts">
        <div>
          <dt>Capability state</dt>
          <dd>{status?.capability_status || onboarding?.capability_status || "missing"}</dd>
        </div>
        <div>
          <dt>Governance state</dt>
          <dd>{onboarding?.governance_sync_status || status?.governance_sync_status || "pending"}</dd>
        </div>
        <div>
          <dt>Governance version</dt>
          <dd>{operational?.active_governance_version || governance?.governance_version || "pending"}</dd>
        </div>
        <div>
          <dt>Freshness</dt>
          <dd>{operational?.governance_freshness_state || status?.governance_freshness_state || "pending"}</dd>
        </div>
      </dl>
      <div className="state-grid">
        <div className="state-row">
          <span className="state-label">Last issue</span>
          <span className="state-value">{operational?.last_governance_issued_at || governance?.issued_timestamp || "pending"}</span>
        </div>
        <div className="state-row">
          <span className="state-label">Last refresh request</span>
          <span className="state-value">{operational?.last_governance_refresh_request_at || "pending"}</span>
        </div>
      </div>
    </section>
  );
}
