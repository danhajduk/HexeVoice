import { useState } from "react";
import { importSetupMigration, preflightSetupMigration, saveSetupCoreConnection } from "../../api/client";

function compactPayload(payload) {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== "" && value !== null && value !== undefined));
}

export function CoreSetupPage() {
  const [coreUrl, setCoreUrl] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setBusy(true);
    setError("");
    try {
      setResult(await saveSetupCoreConnection({ core_base_url: coreUrl }));
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Core Connection</h2>
        <span className={`status-pill status-pill-${result?.reachable ? "success" : "warning"}`}>
          {result?.reachable ? "reachable" : "offline ok"}
        </span>
      </div>
      {error ? <div className="callout callout-danger">{error}</div> : null}
      {result?.warnings?.length ? <div className="callout callout-warning">{result.warnings.join(", ")}</div> : null}
      <label className="field">
        <span className="field-label">Core base URL</span>
        <input className="field-input" value={coreUrl} onChange={(event) => setCoreUrl(event.target.value)} placeholder="http://10.0.0.100:9001" />
      </label>
      <div className="form-actions">
        <button className="btn btn-primary" type="button" onClick={save} disabled={busy || !coreUrl}>
          {busy ? "Saving..." : "Save Core"}
        </button>
      </div>
      {result ? (
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Configured</span>
            <span className="fact-grid-value">{result.configured ? "yes" : "no"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Registration support</span>
            <span className="fact-grid-value">{result.registration_supported ? "detected" : "pending"}</span>
          </div>
        </div>
      ) : null}
    </article>
  );
}

export function MigrationSetupPage() {
  const [bundle, setBundle] = useState(null);
  const [summary, setSummary] = useState("");
  const [form, setForm] = useState({ destination_core_base_url: "", destination_api_base_url: "", destination_ui_endpoint: "", destination_hostname: "" });
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function handleFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setError("");
    try {
      const parsed = JSON.parse(await file.text());
      setBundle(parsed);
      setSummary(parsed?.source?.node_name || file.name);
    } catch (err) {
      setBundle(null);
      setSummary("");
      setError(String(err.message || err));
    }
  }

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function run(kind) {
    if (!bundle) {
      setError("migration_bundle_required");
      return;
    }
    setBusy(kind);
    setError("");
    try {
      const payload = compactPayload({ bundle, ...form, dry_run: kind === "preflight" });
      setResult(kind === "preflight" ? await preflightSetupMigration(payload) : await importSetupMigration(payload));
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Migration Source</h2>
        <span className="pill">{summary || "no bundle"}</span>
      </div>
      {error ? <div className="callout callout-danger">{error}</div> : null}
      <div className="callout callout-warning">Migration bundles with trust tokens are rejected. Core re-auth is required after import.</div>
      <label className="field">
        <span className="field-label">Migration bundle</span>
        <input className="field-input" type="file" accept="application/json,.json" onChange={handleFile} />
      </label>
      <div className="form-grid">
        {Object.keys(form).map((field) => (
          <label className="field" key={field}>
            <span className="field-label">{field}</span>
            <input className="field-input" value={form[field]} onChange={(event) => update(field, event.target.value)} />
          </label>
        ))}
      </div>
      <div className="form-actions">
        <button className="btn btn-secondary" type="button" onClick={() => run("preflight")} disabled={busy !== "" || !bundle}>
          {busy === "preflight" ? "Checking..." : "Preflight"}
        </button>
        <button className="btn btn-primary" type="button" onClick={() => run("import")} disabled={busy !== "" || !bundle}>
          {busy === "import" ? "Importing..." : "Import"}
        </button>
      </div>
      {result ? (
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Result</span>
            <span className="fact-grid-value">{result.ok === false ? "blocked" : result.imported ? "imported" : "checked"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Writes</span>
            <span className="fact-grid-value">{(result.planned_writes || result.files_imported || []).join(", ") || "none"}</span>
          </div>
        </div>
      ) : null}
    </article>
  );
}
