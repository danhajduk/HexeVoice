export function StatusCard({ status, error }) {
  const blockers = status?.blocking_reasons || [];

  return (
    <section className="panel">
      <h2>Status</h2>
      {error ? <p className="error">{error}</p> : null}
      <p>
        Lifecycle: <strong>{status?.lifecycle_state || "loading"}</strong>
      </p>
      <p>
        Trust: <strong>{status?.trust_state || "loading"}</strong>
      </p>
      <p>
        Ready: <strong>{String(status?.operational_ready ?? false)}</strong>
      </p>
      <p>
        Blockers: <strong>{blockers.length}</strong>
      </p>
      <pre>{JSON.stringify(status || { status: "loading" }, null, 2)}</pre>
    </section>
  );
}
