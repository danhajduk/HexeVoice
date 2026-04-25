export function ProviderPanel({ status, providerSetup, capabilities }) {
  const enabledProviders = providerSetup?.enabled_providers || [];
  const supportedProviders = providerSetup?.supported_providers || ["voice"];

  return (
    <section className="card stack panel">
      <div className="section-heading">
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
      <dl className="facts">
        <div>
          <dt>Supported providers</dt>
          <dd>{supportedProviders.join(", ")}</dd>
        </div>
        <div>
          <dt>Enabled providers</dt>
          <dd>{enabledProviders.join(", ") || "none"}</dd>
        </div>
        <div>
          <dt>Default provider</dt>
          <dd>{providerSetup?.default_provider || "pending"}</dd>
        </div>
        <div>
          <dt>Capability status</dt>
          <dd>{capabilities?.capability_status || status?.capability_status || "missing"}</dd>
        </div>
      </dl>
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
      <div className="actions">
        <button className="btn btn-ghost" type="button" disabled>
          Manage provider credentials next
        </button>
      </div>
    </section>
  );
}
