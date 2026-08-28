export function SetupHeroCard({
  nodeState,
  onboarding,
  status,
  mode = "setup",
  restartSetup,
  restartingSetup,
  dashboardEnabled,
  openDashboard,
  onRefresh,
  openProvider,
}) {
  const dashboardMode = mode === "dashboard";
  const title = dashboardMode ? "HexeVoice" : "HexeVoice Setup";
  const copy = dashboardMode
    ? "Monitor live endpoint activity, voice pipeline health, and node readiness from one operational view."
    : "Configure the target Core, start onboarding, and move the voice node from local setup into trusted operational status.";

  return (
    <section className={`hero card ${dashboardMode ? "hero-dashboard" : ""}`}>
      <div>
        <div className="hero-topline">
          <div className="eyebrow">Hexe Voice Node</div>
          <div className={`status-pill tone-${nodeState.tone}`}>state: {nodeState.label}</div>
        </div>
        <h1>{title}</h1>
        <p className="hero-copy">{copy}</p>
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
        {dashboardMode ? (
          <button className="btn btn-ghost" type="button" onClick={onRefresh}>
            Refresh
          </button>
        ) : null}
        <button className="btn btn-ghost" type="button" onClick={restartSetup} disabled={restartingSetup}>
          {restartingSetup ? "Restarting..." : "Restart Setup"}
        </button>
        {!dashboardMode && dashboardEnabled ? (
          <button className="btn btn-ghost" type="button" onClick={openDashboard}>
            Dashboard
          </button>
        ) : null}
        {!dashboardMode ? (
          <button className="btn btn-ghost" type="button" onClick={openProvider} disabled>
            Setup Provider
          </button>
        ) : null}
      </div>
    </section>
  );
}
