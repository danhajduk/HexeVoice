function formatLocalDateTime(value) {
  if (!value) {
    return "pending";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function CoreConnectionCard({ status, onboarding, governance, operational, openSetup }) {
  const issuedAt = formatLocalDateTime(operational?.last_governance_issued_at || governance?.issued_timestamp);
  const refreshedAt = formatLocalDateTime(operational?.last_governance_refresh_request_at);

  return (
    <section className="card stack panel overview-panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Core Link</p>
          <h2 className="panel-title">Core And Governance</h2>
        </div>
        <button className="btn btn-ghost btn-compact" type="button" onClick={openSetup}>
          Setup
        </button>
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
      <div className="governance-history">
        <div className="state-row">
          <span className="state-label">Last issue</span>
          <span className="state-value">{issuedAt}</span>
        </div>
        <div className="state-row">
          <span className="state-label">Last refresh request</span>
          <span className="state-value">{refreshedAt}</span>
        </div>
      </div>
    </section>
  );
}
