export function ProviderPanel({ status, providerSetup, capabilities }) {
  const enabledProviders = providerSetup?.enabled_providers || [];
  const supportedProviders = providerSetup?.supported_providers || ["voice"];

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
        Voice provider setup, selection, health, and capability wiring live here under the explicit provider
        namespace.
      </p>
      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Supported providers</span>
          <span className="fact-grid-value">{supportedProviders.join(", ")}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Enabled providers</span>
          <span className="fact-grid-value">{enabledProviders.join(", ") || "none"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Default provider</span>
          <span className="fact-grid-value">{providerSetup?.default_provider || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Capability status</span>
          <span className="fact-grid-value">{capabilities?.capability_status || status?.capability_status || "missing"}</span>
        </div>
      </div>
      <div className="state-grid">
        <div className="state-row">
          <span className="state-label">Declaration allowed</span>
          <span className="state-value">{String(providerSetup?.declaration_allowed ?? false)}</span>
        </div>
        <div className="state-row">
          <span className="state-label">Declared capabilities</span>
          <span className="state-value">{capabilities?.declared?.join(", ") || "pending"}</span>
        </div>
      </div>
      {(providerSetup?.blocking_reasons || []).length > 0 ? (
        <div className="warning-card">
          <strong>Provider setup still blocks declaration.</strong>
          <span>{providerSetup.blocking_reasons.join(", ")}</span>
        </div>
      ) : null}
      <div className="action-group">
        <button className="btn btn-secondary" type="button" disabled>
          Manage provider credentials next
        </button>
      </div>
    </section>
  );
}
