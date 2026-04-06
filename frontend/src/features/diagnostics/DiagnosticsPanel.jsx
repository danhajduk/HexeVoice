export function DiagnosticsPanel({ status }) {
  return (
    <section className="panel">
      <h2>Diagnostics</h2>
      <p>Node ID: <code>{status?.node_id || "pending"}</code></p>
      <p>Default provider boundary: <code>voice</code></p>
    </section>
  );
}
