export function OnboardingPanel({ status }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="panel-kicker">Setup Progress</p>
          <h2 className="panel-title">Onboarding</h2>
        </div>
        <span className="status-pill status-pill-neutral">{status?.current_step_id || "node_identity"}</span>
      </div>
      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Current Stage</span>
          <span className="fact-grid-value">{status?.lifecycle_state || "bootstrap_required"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Trust State</span>
          <span className="fact-grid-value">{status?.trust_state || "untrusted"}</span>
        </div>
      </div>
      <div className="callout">
        The dedicated multi-step onboarding shell lands next. This panel is already using the shared card and status
        language it will inherit.
      </div>
    </section>
  );
}
