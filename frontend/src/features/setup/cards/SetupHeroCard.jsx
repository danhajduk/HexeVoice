export function SetupHeroCard({
  nodeState,
  onboarding,
  status,
  restartSetup,
  restartingSetup,
  dashboardEnabled,
  openDashboard,
  openProvider,
}) {
  return (
    <section className="hero card">
      <div>
        <div className="hero-topline">
          <div className="eyebrow">Hexe Voice Node</div>
          <div className={`status-pill tone-${nodeState.tone}`}>state: {nodeState.label}</div>
        </div>
        <h1>HexeVoice Setup</h1>
        <p className="hero-copy">
          Configure the target Core, start onboarding, and move the voice node from local setup into trusted
          operational status.
        </p>
      </div>
      <div className="hero-actions">
        <div className="hero-status">
          <div className={`status-pill tone-${nodeState.tone}`}>
            onboarding: {onboarding?.onboarding_state || "loading"}
          </div>
          <div className={`status-pill tone-${status?.trust_state === "trusted" ? "success" : "warning"}`}>
            trust: {status?.trust_state || "loading"}
          </div>
        </div>
        <button className="btn btn-ghost" type="button" onClick={restartSetup} disabled={restartingSetup}>
          {restartingSetup ? "Restarting..." : "Restart Setup"}
        </button>
        {dashboardEnabled ? (
          <button className="btn btn-ghost" type="button" onClick={openDashboard}>
            Dashboard
          </button>
        ) : null}
        <button className="btn btn-ghost" type="button" onClick={openProvider} disabled>
          Setup Provider
        </button>
      </div>
    </section>
  );
}
