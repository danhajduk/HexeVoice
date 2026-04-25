export function NodeIdentityFormCard({
  uiPort,
  form,
  handleChange,
  saveConfiguration,
  saving,
  startOnboarding,
  starting,
  requiredInputs,
  notice,
  error,
}) {
  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Node Identity</h2>
        <span className="pill">UI {uiPort || 8084}</span>
      </div>
      <label className="field">
        <span className="field-label">Core base URL</span>
        <input
          className="field-input"
          name="core_base_url"
          value={form.core_base_url}
          onChange={(event) => handleChange("core_base_url", event.target.value)}
          placeholder="http://192.168.1.10:9001"
          required
        />
      </label>
      <label className="field">
        <span className="field-label">Node name</span>
        <input
          className="field-input"
          name="node_name"
          value={form.node_name}
          onChange={(event) => handleChange("node_name", event.target.value)}
          placeholder="kitchen-voice-node"
          required
        />
      </label>
      <div className="actions">
        <button className="btn btn-ghost" type="button" onClick={saveConfiguration} disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </button>
        <button className="btn btn-primary" type="button" onClick={startOnboarding} disabled={starting}>
          {starting ? "Starting..." : "Start Onboarding"}
        </button>
      </div>
      {notice ? <div className="callout callout-success">{notice}</div> : null}
      {error ? <div className="callout callout-danger">{error}</div> : null}
      {requiredInputs.length > 0 ? (
        <div className="callout callout-warning">Required before onboarding: {requiredInputs.join(", ")}</div>
      ) : null}
    </article>
  );
}
