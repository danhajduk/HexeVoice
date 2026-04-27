export function VoiceEndpointStatusCard({ status, providerSetup, capabilities, voiceStatus, endpointStatus }) {
  const storage = endpointStatus?.capabilities?.storage || {};

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
          <dd>{endpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "not connected"}</dd>
        </div>
        <div>
          <dt>Endpoint FW</dt>
          <dd>{endpointStatus?.firmware_version || "unknown"}</dd>
        </div>
        <div>
          <dt>Device state</dt>
          <dd>{endpointStatus?.device_state || "unknown"}</dd>
        </div>
        <div>
          <dt>File transfer</dt>
          <dd>{storage.media_transfer_active ? "downloading file" : storage.media_transfer_status || "idle"}</dd>
        </div>
        <div>
          <dt>Connection</dt>
          <dd>{voiceStatus?.state_projection?.connection_state || voiceStatus?.connection_state || "offline"}</dd>
        </div>
        <div>
          <dt>UX</dt>
          <dd>{voiceStatus?.state_projection?.ux_state || voiceStatus?.ux_state || "idle"}</dd>
        </div>
        <div>
          <dt>Session state</dt>
          <dd>{voiceStatus?.state_projection?.session_state || "none"}</dd>
        </div>
        <div>
          <dt>Last heartbeat</dt>
          <dd>{endpointStatus?.last_seen_at || "none"}</dd>
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
