import { useState } from "react";
import { importSetupMigration, preflightSetupMigration, saveSetupCoreConnection } from "../../api/client";

function compactPayload(payload) {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== "" && value !== null && value !== undefined));
}

function normalizedCorePublicUrl(raw) {
  const trimmed = String(raw || "").trim();
  if (!trimmed) {
    return "";
  }
  try {
    const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
    const url = new URL(withScheme);
    url.pathname = url.pathname.replace(/\/$/, "");
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return trimmed.replace(/\/$/, "");
  }
}

function normalizedCoreApiUrl(raw) {
  const normalized = normalizedCorePublicUrl(raw);
  if (!normalized) {
    return "";
  }
  try {
    const url = new URL(normalized);
    if (!url.port && url.protocol === "http:") {
      url.port = "9001";
    }
    return url.toString().replace(/\/$/, "");
  } catch {
    return normalized;
  }
}

export function CoreSetupPage({ onContinue }) {
  const [coreUrl, setCoreUrl] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setBusy(true);
    setError("");
    try {
      const normalized = normalizedCorePublicUrl(coreUrl);
      setCoreUrl(normalized);
      const payload = await saveSetupCoreConnection({ core_base_url: normalized });
      setResult(payload);
      onContinue?.();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  const coreIdentity = result?.core_identity || {};
  const testedEndpoint = result?.tested_endpoints?.[0]?.url || result?.metadata?.probes?.registration?.url || normalizedCoreApiUrl(coreUrl);
  const supportFacts = result
    ? [
        ["Core identity", coreIdentity.core_name || coreIdentity.platform_name || coreIdentity.core_id || "pending"],
        ["Core version", result.core_version || "unknown"],
        ["LAN/public URL", result.core_public_url || "pending"],
        ["API URL", result.core_api_url || result.core_base_url || "pending"],
        ["UI URL", result.core_ui_url || result.core_public_url || "pending"],
        ["Endpoint tested", testedEndpoint || "pending"],
        ["Registration support", result.registration_supported ? "detected" : "pending"],
        ["Re-auth support", result.reauth_supported ? "detected" : "pending"],
        ["Supervisor enrollment", result.supervisor_enrollment_supported ? "detected" : "pending"],
        ["Capability/governance", result.capability_governance_supported ? "detected" : "pending"],
      ]
    : [];

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
        <span className="field-label">Core LAN/public URL</span>
        <input className="field-input" value={coreUrl} onChange={(event) => setCoreUrl(event.target.value)} placeholder="http://10.0.0.100" />
      </label>
      {coreUrl && normalizedCorePublicUrl(coreUrl) !== coreUrl.replace(/\/$/, "") ? (
        <div className="callout callout-warning">Using normalized Core URL: {normalizedCorePublicUrl(coreUrl)}</div>
      ) : null}
      {coreUrl ? (
        <div className="callout callout-neutral">Core API will be checked at {normalizedCoreApiUrl(coreUrl) || "pending"}.</div>
      ) : null}
      <div className="form-actions">
        <button className="btn btn-primary" type="button" onClick={save} disabled={busy || !coreUrl}>
          {busy ? "Saving..." : "Save & Continue"}
        </button>
      </div>
      {result ? (
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Configured</span>
            <span className="fact-grid-value">{result.configured ? "yes" : "no"}</span>
          </div>
          {supportFacts.map(([label, value]) => (
            <div className="fact-grid-item" key={label}>
              <span className="fact-grid-label">{label}</span>
              <span className="fact-grid-value">{value}</span>
            </div>
          ))}
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
      setBusy("auto-preflight");
      const payload = compactPayload({ bundle: parsed, ...form, dry_run: true });
      setResult(await preflightSetupMigration(payload));
    } catch (err) {
      setBundle(null);
      setSummary("");
      setError(String(err.message || err));
      setResult(null);
    } finally {
      setBusy("");
    }
  }

  function migrationResultLabel(payload) {
    if (!payload) return "";
    if (payload.ok === false) return "blocked";
    if (payload.imported) return "imported";
    return "ready";
  }

  function migrationSummaryText(payload) {
    if (!payload) return "Upload a migration bundle to preview the import.";
    const writes = payload.planned_writes || payload.files_imported || [];
    const errors = payload.errors || [];
    const warnings = payload.warnings || [];
    if (errors.length) return errors.join(", ");
    if (writes.length) return `Planned writes: ${writes.join(", ")}`;
    if (warnings.length) return `Warnings: ${warnings.join(", ")}`;
    return "Preflight completed with no planned writes.";
  }

  function importDisabled() {
    if (busy !== "" || !bundle) return true;
    if (!result) return true;
    return result.ok === false;
  }

  async function rerunPreflight(currentBundle = bundle) {
    if (!currentBundle) {
      setError("migration_bundle_required");
      return;
    }
    setBusy("preflight");
    setError("");
    try {
      const payload = compactPayload({ bundle: currentBundle, ...form, dry_run: true });
      setResult(await preflightSetupMigration(payload));
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
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
        <input className="field-input" type="file" accept="application/json,.json" onChange={handleFile} disabled={busy !== ""} />
      </label>
      <div className={`callout callout-${result?.ok === false ? "danger" : result ? "success" : "neutral"}`}>
        {busy === "auto-preflight" ? "Checking migration bundle..." : migrationSummaryText(result)}
      </div>
      <div className="form-grid">
        {Object.keys(form).map((field) => (
          <label className="field" key={field}>
            <span className="field-label">{field}</span>
            <input className="field-input" value={form[field]} onChange={(event) => update(field, event.target.value)} />
          </label>
        ))}
      </div>
      <div className="form-actions">
        <button className="btn btn-secondary" type="button" onClick={() => rerunPreflight()} disabled={busy !== "" || !bundle}>
          {busy === "preflight" ? "Checking..." : "Preflight"}
        </button>
        <button className="btn btn-primary" type="button" onClick={() => run("import")} disabled={importDisabled()}>
          {busy === "import" ? "Importing..." : "Import"}
        </button>
      </div>
      {result ? (
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Result</span>
            <span className="fact-grid-value">{migrationResultLabel(result)}</span>
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
