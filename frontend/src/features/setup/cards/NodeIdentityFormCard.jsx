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
  migration,
  onMigrationFile,
  onMigrationDestinationChange,
  onMigrationImport,
  setupBootstrap,
}) {
  const retryableFailures = setupBootstrap?.retryable_failures || [];
  const pendingDownloads = setupBootstrap?.pending_downloads || [];
  const completedActions = setupBootstrap?.completed_actions || [];

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Node Identity</h2>
        <span className="pill">UI {uiPort || 8084}</span>
      </div>
      {setupBootstrap ? (
        <>
          <div className="section-heading">
            <h2>Install Prep</h2>
            <span className="pill">{setupBootstrap.phase || "idle"}</span>
          </div>
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">Current</span>
              <span className="fact-grid-value">{setupBootstrap.current_action || "ready"}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Pending</span>
              <span className="fact-grid-value">{pendingDownloads.length ? pendingDownloads.join(", ") : "none"}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Completed</span>
              <span className="fact-grid-value">{completedActions.length}</span>
            </div>
          </div>
          {retryableFailures.length ? (
            <div className="callout callout-warning">
              Retryable setup items: {retryableFailures.map((failure) => failure.id).join(", ")}
            </div>
          ) : null}
        </>
      ) : null}
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
          {starting ? "Starting..." : "Start new-node onboarding"}
        </button>
      </div>
      {notice ? <div className="callout callout-success">{notice}</div> : null}
      {error ? <div className="callout callout-danger">{error}</div> : null}
      {requiredInputs.length > 0 ? (
        <div className="callout callout-warning">Required before onboarding: {requiredInputs.join(", ")}</div>
      ) : null}
      <div className="section-divider" />
      <div className="section-heading">
        <h2>Migration</h2>
        <span className="pill">{migration?.summary || "optional"}</span>
      </div>
      <label className="field">
        <span className="field-label">Migration bundle</span>
        <input className="field-input" type="file" accept="application/json,.json" onChange={onMigrationFile} />
      </label>
      <div className="form-grid">
        <label className="field">
          <span className="field-label">Destination API base URL</span>
          <input
            className="field-input"
            value={migration?.destinationForm?.destination_api_base_url || ""}
            onChange={(event) => onMigrationDestinationChange("destination_api_base_url", event.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Destination UI endpoint</span>
          <input
            className="field-input"
            value={migration?.destinationForm?.destination_ui_endpoint || ""}
            onChange={(event) => onMigrationDestinationChange("destination_ui_endpoint", event.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Destination hostname</span>
          <input
            className="field-input"
            value={migration?.destinationForm?.destination_hostname || ""}
            onChange={(event) => onMigrationDestinationChange("destination_hostname", event.target.value)}
            placeholder="optional"
          />
        </label>
      </div>
      <div className="actions">
        <button
          className="btn btn-secondary"
          type="button"
          onClick={onMigrationImport}
          disabled={migration?.busy || !migration?.bundleLoaded}
        >
          {migration?.busy ? "Importing..." : "Import Migration"}
        </button>
      </div>
    </article>
  );
}
