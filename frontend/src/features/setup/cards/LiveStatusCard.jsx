export function LiveStatusCard({ status }) {
  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Live Status</h2>
        <span className="pill">{status?.node_type || "voice-node"}</span>
      </div>
      <dl className="facts">
        <div>
          <dt>Node name</dt>
          <dd>{status?.node_name || "Not set"}</dd>
        </div>
        <div>
          <dt>Version</dt>
          <dd>0.1.0</dd>
        </div>
        <div>
          <dt>Trust state</dt>
          <dd>{status?.trust_state || "untrusted"}</dd>
        </div>
        <div>
          <dt>Node ID</dt>
          <dd>{status?.node_id || "Pending"}</dd>
        </div>
        <div>
          <dt>Lifecycle</dt>
          <dd>{status?.lifecycle_state || "pending"}</dd>
        </div>
        <div>
          <dt>Providers</dt>
          <dd>voice</dd>
        </div>
      </dl>
    </article>
  );
}
