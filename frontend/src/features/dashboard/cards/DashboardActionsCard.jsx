export function DashboardActionsCard({ openSetup, openVoiceEndpoint, onRefresh }) {
  return (
    <section className="card stack panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Operator Actions</p>
          <h2 className="panel-title">Dashboard Actions</h2>
        </div>
      </div>
      <p className="panel-copy">
        Keep the node healthy, revisit setup when needed, or jump straight into the voice endpoint view.
      </p>
      <div className="actions">
        <button className="btn btn-ghost" type="button" onClick={openVoiceEndpoint}>
          Open Voice Endpoint
        </button>
        <button className="btn btn-ghost" type="button" onClick={onRefresh}>
          Refresh dashboard
        </button>
        <button className="btn btn-ghost" type="button" onClick={openSetup}>
          Return to setup
        </button>
      </div>
      <div className="callout">
        Additional runtime, provider, and diagnostics actions will land in their own dashboard sections next.
      </div>
    </section>
  );
}
