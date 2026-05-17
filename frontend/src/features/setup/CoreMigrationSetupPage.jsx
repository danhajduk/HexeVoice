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

export function MigrationSetupPage({ onImportComplete }) {
  const [sourceMode, setSourceMode] = useState("upload");
  const [sourceUrl, setSourceUrl] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [bundle, setBundle] = useState(null);
  const [summary, setSummary] = useState("");
  const [form, setForm] = useState({ destination_core_base_url: "", destination_api_base_url: "", destination_ui_endpoint: "", destination_hostname: "" });
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function handleFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setError("");
    setNotice("");
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

  function providerPlanLabel(provider) {
    if (!provider?.present) return "not included";
    return [provider.provider, provider.model || provider.default_voice || provider.default_wakeword, provider.device].filter(Boolean).join(" / ") || "included";
  }

  function bundleSchemaLabel() {
    if (!bundle) return "pending";
    return bundle.schema_version ? `schema ${bundle.schema_version}` : "schema missing";
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
    setNotice("");
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
    setNotice("");
    try {
      const payload = compactPayload({ bundle, ...form, dry_run: kind === "preflight" });
      const nextResult = kind === "preflight" ? await preflightSetupMigration(payload) : await importSetupMigration(payload);
      setResult(nextResult);
      if (kind === "import" && nextResult.imported) {
        setNotice(nextResult.node_id ? "Migration imported. Continuing to Core re-auth." : "Migration imported. Continuing to node onboarding.");
        onImportComplete?.(nextResult);
      }
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
      {notice ? <div className="callout callout-success">{notice}</div> : null}
      <div className="callout callout-warning">Migration bundles with trust tokens are rejected. Core re-auth is required after import.</div>
      <label className="field">
        <span className="field-label">Migration source</span>
        <select className="field-input" value={sourceMode} onChange={(event) => setSourceMode(event.target.value)}>
          <option value="upload">Upload bundle</option>
          <option value="local">Local backup path</option>
          <option value="core">Old node/Core fetch</option>
        </select>
      </label>
      {sourceMode === "local" ? (
        <div className="stack">
          <label className="field">
            <span className="field-label">Local migration path</span>
            <input
              className="field-input"
              value={localPath}
              onChange={(event) => setLocalPath(event.target.value)}
              placeholder="runtime/migration/backups/<backup-id>/migration-bundle.json"
            />
          </label>
          <div className="callout callout-neutral">Local path loading is held until setup has a constrained backend read path. Upload the file to review it now.</div>
        </div>
      ) : null}
      {sourceMode === "core" ? (
        <div className="stack">
          <label className="field">
            <span className="field-label">Old node/Core export URL</span>
            <input className="field-input" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="http://old-node:9004" />
          </label>
          <div className="callout callout-neutral">Fetch is disabled until the old node/Core advertises a supported redacted migration export path.</div>
        </div>
      ) : null}
      <label className="field">
        <span className="field-label">Migration bundle</span>
        <input className="field-input" type="file" accept="application/json,.json" onChange={handleFile} disabled={busy !== ""} />
      </label>
      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Bundle schema</span>
          <span className="fact-grid-value">{bundleSchemaLabel()}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Source node</span>
          <span className="fact-grid-value">{bundle?.source?.node_name || bundle?.source?.node_id || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Trust secrets</span>
          <span className="fact-grid-value">{bundle?.contains_trust_secrets ? "present - blocked" : bundle ? "not included" : "pending"}</span>
        </div>
      </div>
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
        <div className="stack">
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">Result</span>
              <span className="fact-grid-value">{migrationResultLabel(result)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Writes</span>
              <span className="fact-grid-value">{(result.planned_writes || result.files_imported || []).join(", ") || "none"}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Voice intents</span>
              <span className="fact-grid-value">
                {result.import_plan?.voice_intents?.present ? `${result.import_plan.voice_intents.count || 0} included` : "not included"}
              </span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Re-auth</span>
              <span className="fact-grid-value">{result.import_plan?.required_reauth ? "required" : "not required"}</span>
            </div>
          </div>
          <div className="fact-grid">
            {(result.import_plan?.imported_data || []).map((item) => (
              <div className="fact-grid-item" key={item.id}>
                <span className="fact-grid-label">{item.label}</span>
                <span className="fact-grid-value">{item.will_import ? "will import" : item.present ? "present but blocked" : "not included"}</span>
              </div>
            ))}
          </div>
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">STT</span>
              <span className="fact-grid-value">{providerPlanLabel(result.import_plan?.provider_settings?.stt)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">TTS</span>
              <span className="fact-grid-value">{providerPlanLabel(result.import_plan?.provider_settings?.tts)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Wake</span>
              <span className="fact-grid-value">{providerPlanLabel(result.import_plan?.provider_settings?.wake)}</span>
            </div>
          </div>
          {result.import_plan?.skipped_secrets?.length ? (
            <div className="callout callout-neutral">Skipped secrets/tokens: {result.import_plan.skipped_secrets.join(", ")}</div>
          ) : null}
          {result.import_plan?.runtime_asset_expectations?.length ? (
            <div className="callout callout-warning">{result.import_plan.runtime_asset_expectations.join(" ")}</div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
