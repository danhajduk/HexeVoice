export function OperationalWarningsCard({ status, onboarding }) {
  const blockers = status?.blocking_reasons || [];

  if (blockers.length === 0) {
    return (
      <section className="card stack panel">
        <div className="section-heading">
          <div>
            <p className="panel-kicker">Warnings</p>
            <h2 className="panel-title">Operational Warnings</h2>
          </div>
        </div>
        <div className="callout callout-success">No current operator warnings are reported for this node.</div>
      </section>
    );
  }

  return (
    <section className="card stack panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Warnings</p>
          <h2 className="panel-title">Operational Warnings</h2>
        </div>
      </div>
      <div className="callout callout-warning">
        Blocking reasons: {blockers.join(", ")}
      </div>
      <div className="state-grid">
        <div className="state-row">
          <span className="state-label">Current step</span>
          <span className="state-value">{onboarding?.current_step_label || status?.current_step_label || "pending"}</span>
        </div>
      </div>
    </section>
  );
}
