export function VoiceEndpointStatusCard({ status, providerSetup, capabilities, voiceStatus }) {
  return (
    <section className="card stack panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoint Status</p>
          <h2 className="panel-title">Voice Endpoint</h2>
        </div>
        <span className="status-pill status-pill-neutral">{voiceStatus?.connection_state || "offline"}</span>
      </div>
      <dl className="facts">
        <div>
          <dt>Namespace</dt>
          <dd>voice</dd>
        </div>
        <div>
          <dt>Endpoint</dt>
          <dd>{voiceStatus?.endpoint_id || "not connected"}</dd>
        </div>
        <div>
          <dt>Enabled providers</dt>
          <dd>{providerSetup?.enabled_providers?.join(", ") || "none"}</dd>
        </div>
        <div>
          <dt>Declared capabilities</dt>
          <dd>{capabilities?.declared?.join(", ") || "pending"}</dd>
        </div>
        <div>
          <dt>Operational readiness</dt>
          <dd>{String(status?.operational_ready ?? false)}</dd>
        </div>
      </dl>
    </section>
  );
}
