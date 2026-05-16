import { useMemo, useState } from "react";
import { exportNodeMigrationBundle, importNodeMigrationBundle } from "../../api/client";

function defaultDestinationUrls() {
  if (typeof window === "undefined") {
    return {
      destination_api_base_url: "",
      destination_ui_endpoint: "",
    };
  }
  const { protocol, hostname, origin } = window.location;
  return {
    destination_api_base_url: `${protocol}//${hostname}:9004`,
    destination_ui_endpoint: origin,
  };
}

function bundleFilename(bundle) {
  const nodeId = bundle?.source?.node_id || bundle?.source?.node_name || "hexevoice-node";
  const safeNodeId = String(nodeId).replace(/[^a-z0-9._-]+/gi, "-").replace(/^-|-$/g, "");
  return `${safeNodeId || "hexevoice-node"}-migration.json`;
}

function downloadBundle(bundle) {
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = bundleFilename(bundle);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function nonEmptyPayload(payload) {
  return Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== "" && value !== null && value !== undefined),
  );
}

export function MigrationDashboardSection({ onRefresh }) {
  const [exportedBundle, setExportedBundle] = useState(null);
  const [importBundle, setImportBundle] = useState(null);
  const [destinationForm, setDestinationForm] = useState(() => ({
    destination_core_base_url: "",
    destination_hostname: "",
    ...defaultDestinationUrls(),
  }));
  const [busyState, setBusyState] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const importedSummary = useMemo(() => {
    if (!importBundle?.source) {
      return "No bundle selected";
    }
    return [importBundle.source.node_name, importBundle.source.node_id].filter(Boolean).join(" / ") || "Bundle selected";
  }, [importBundle]);

  function updateDestination(field, value) {
    setDestinationForm((current) => ({ ...current, [field]: value }));
  }

  async function handleExport() {
    setBusyState("export");
    setNotice("");
    setError("");
    try {
      const bundle = await exportNodeMigrationBundle();
      setExportedBundle(bundle);
      downloadBundle(bundle);
      setNotice("Migration bundle exported. Core re-auth is required after import.");
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusyState("");
    }
  }

  async function handleBundleFile(event) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    setNotice("");
    setError("");
    try {
      const text = await file.text();
      const bundle = JSON.parse(text);
      setImportBundle(bundle);
      setNotice("Migration bundle loaded.");
    } catch (err) {
      setImportBundle(null);
      setError(String(err.message || err));
    }
  }

  async function handleImport() {
    if (!importBundle) {
      setError("migration_bundle_required");
      return;
    }
    setBusyState("import");
    setNotice("");
    setError("");
    try {
      const payload = await importNodeMigrationBundle(
        nonEmptyPayload({
          bundle: importBundle,
          ...destinationForm,
        }),
      );
      setNotice(`Imported ${payload.files_imported.join(", ")}.`);
      if (onRefresh) {
        await onRefresh();
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusyState("");
    }
  }

  return (
    <section className="grid operational-dashboard-grid">
      <article className="card stack">
        <div className="section-heading">
          <h2>Migration</h2>
          <span className="pill">Node state</span>
        </div>
        {notice ? <div className="callout callout-success">{notice}</div> : null}
        {error ? <div className="callout callout-danger">{error}</div> : null}
        <div className="callout callout-warning">
          Migration bundles do not include trust secrets. Imported nodes must re-authorize with Core before becoming trusted.
        </div>
        <div className="form-actions">
          <button className="btn btn-primary" type="button" onClick={handleExport} disabled={busyState !== ""}>
            {busyState === "export" ? "Exporting..." : "Export Bundle"}
          </button>
        </div>
        {exportedBundle ? (
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">Node</span>
              <span className="fact-grid-value">{exportedBundle.source?.node_name || "unknown"}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Trust secrets</span>
              <span className="fact-grid-value">{exportedBundle.contains_trust_secrets ? "included" : "redacted"}</span>
            </div>
          </div>
        ) : null}
      </article>

      <article className="card stack">
        <div className="section-heading">
          <h2>Import</h2>
          <span className="pill">{importedSummary}</span>
        </div>
        <label className="field">
          <span className="field-label">Migration bundle</span>
          <input className="field-input" type="file" accept="application/json,.json" onChange={handleBundleFile} />
        </label>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">Destination API base URL</span>
            <input
              className="field-input"
              value={destinationForm.destination_api_base_url}
              onChange={(event) => updateDestination("destination_api_base_url", event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">Destination UI endpoint</span>
            <input
              className="field-input"
              value={destinationForm.destination_ui_endpoint}
              onChange={(event) => updateDestination("destination_ui_endpoint", event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">Core base URL</span>
            <input
              className="field-input"
              value={destinationForm.destination_core_base_url}
              onChange={(event) => updateDestination("destination_core_base_url", event.target.value)}
              placeholder="keep bundle value"
            />
          </label>
          <label className="field">
            <span className="field-label">Destination hostname</span>
            <input
              className="field-input"
              value={destinationForm.destination_hostname}
              onChange={(event) => updateDestination("destination_hostname", event.target.value)}
              placeholder="optional"
            />
          </label>
        </div>
        <div className="form-actions">
          <button className="btn btn-primary" type="button" onClick={handleImport} disabled={busyState !== "" || !importBundle}>
            {busyState === "import" ? "Importing..." : "Import Bundle"}
          </button>
        </div>
      </article>
    </section>
  );
}
