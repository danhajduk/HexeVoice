export function NodeSetupCard({
  apiPort,
  onboarding,
  status,
  statusTone,
  children,
}) {
  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Node Setup</h2>
        <span className="pill">API {apiPort || 9004}</span>
      </div>
      <div className="status-rail">
        <div className={`status-pill tone-${statusTone(onboarding?.onboarding_state)}`}>
          lifecycle: {onboarding?.onboarding_state || "not_started"}
        </div>
        <div className={`status-pill tone-${statusTone(status?.trust_state)}`}>
          trust: {status?.trust_state || "untrusted"}
        </div>
        <div className={`status-pill tone-${statusTone(status?.governance_sync_status)}`}>
          governance: {status?.governance_sync_status || "pending"}
        </div>
        <div className={`status-pill tone-${status?.trust_state === "trusted" ? "success" : "neutral"}`}>
          core: {status?.trust_state === "trusted" ? "paired" : "not paired"}
        </div>
      </div>
      {children}
    </article>
  );
}
