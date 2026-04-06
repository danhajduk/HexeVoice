export function OperationalPanel({ status }) {
  return (
    <section className="panel">
      <h2>Operational</h2>
      <p>Readiness: <strong>{String(status?.operational_ready ?? false)}</strong></p>
      <p>Blocking reasons: <strong>{(status?.blocking_reasons || []).join(", ") || "none"}</strong></p>
    </section>
  );
}
