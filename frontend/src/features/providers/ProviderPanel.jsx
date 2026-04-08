export function ProviderPanel() {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="panel-kicker">Provider Boundary</p>
          <h2 className="panel-title">Providers</h2>
        </div>
        <span className="status-pill status-pill-neutral">voice</span>
      </div>
      <p className="panel-copy">
        Voice provider setup, selection, health, and capability wiring will live here under the explicit provider
        namespace.
      </p>
      <div className="callout">Task 016 will replace this placeholder with the real provider setup surfaces.</div>
    </section>
  );
}
