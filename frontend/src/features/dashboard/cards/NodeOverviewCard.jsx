export function NodeOverviewCard({ status, onboarding, operational }) {
  return (
    <section className="card stack panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Node Overview</p>
          <h2 className="panel-title">Voice Node Status</h2>
        </div>
        <span className={`status-pill ${status?.operational_ready ? "status-pill-success" : "status-pill-warning"}`}>
          {status?.operational_ready ? "ready" : "not ready"}
        </span>
      </div>
      <dl className="facts">
        <div>
          <dt>Node ID</dt>
          <dd>{status?.node_id || "pending"}</dd>
        </div>
        <div>
          <dt>Trust state</dt>
          <dd>{status?.trust_state || "pending"}</dd>
        </div>
        <div>
          <dt>Lifecycle</dt>
          <dd>{status?.lifecycle_state || onboarding?.lifecycle_state || "pending"}</dd>
        </div>
        <div>
          <dt>Current stage</dt>
          <dd>{onboarding?.current_step_label || status?.current_step_label || "pending"}</dd>
        </div>
      </dl>
      <div className="callout callout-success">
        {operational?.operational_ready ?? status?.operational_ready
          ? "The voice node is trusted and operationally ready."
          : "The voice node is online, but readiness is still blocked by post-trust requirements."}
      </div>
    </section>
  );
}
