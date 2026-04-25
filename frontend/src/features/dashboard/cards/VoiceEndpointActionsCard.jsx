export function VoiceEndpointActionsCard({ onRefresh }) {
  return (
    <section className="card stack panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoint Actions</p>
          <h2 className="panel-title">Voice Controls</h2>
        </div>
      </div>
      <div className="actions">
        <button className="btn btn-ghost" type="button" onClick={onRefresh}>
          Refresh endpoint
        </button>
        <button className="btn btn-ghost" type="button" disabled>
          Test assistant turn
        </button>
        <button className="btn btn-ghost" type="button" disabled>
          Open device console
        </button>
      </div>
      <div className="callout">
        Placeholder for live endpoint actions. We can wire real device and assistant controls here next.
      </div>
    </section>
  );
}
