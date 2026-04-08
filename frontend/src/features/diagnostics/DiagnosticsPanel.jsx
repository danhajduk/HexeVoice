export function DiagnosticsPanel({ status }) {
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
      </div>
      <ul className="list-inline">
        <li>API and runtime diagnostics stay visible during onboarding.</li>
        <li>The same shell will carry the post-setup operational overview.</li>
      </ul>
    </section>
  );
}
