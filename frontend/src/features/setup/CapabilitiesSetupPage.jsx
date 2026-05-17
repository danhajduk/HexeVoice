import { useEffect, useState } from "react";
import {
  declareSetupCapabilities,
  getSetupCapabilitiesStatus,
  saveSetupCapabilitySelection,
  syncSetupGovernance,
} from "../../api/client";

function toneForStatus(value) {
  if (value === true || value === "accepted" || value === "issued" || value === "fresh") return "success";
  if (value === "denied" || value === "rejected" || value === "failed") return "danger";
  if (value === false || value === "missing" || value === "pending" || value === "pending_capability") return "warning";
  return "neutral";
}

function normalizeSelection(status) {
  return status?.capabilities?.selected?.length
    ? status.capabilities.selected
    : status?.capabilities?.available || [];
}

function joinList(value) {
  return Array.isArray(value) && value.length ? value.join(", ") : "pending";
}

function formatSummaryItems(value) {
  if (!Array.isArray(value) || !value.length) {
    return "none";
  }
  return value
    .map((item) => {
      if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
        return String(item);
      }
      if (item && typeof item === "object") {
        return item.id || item.name || item.key || item.status || JSON.stringify(item);
      }
      return String(item);
    })
    .join(", ");
}

export function CapabilitiesSetupPage() {
  const [status, setStatus] = useState(null);
  const [selected, setSelected] = useState([]);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function refresh({ syncSelection = false } = {}) {
    const payload = await getSetupCapabilitiesStatus();
    setStatus(payload);
    if (syncSelection) {
      setSelected(normalizeSelection(payload));
    }
    return payload;
  }

  useEffect(() => {
    let mounted = true;
    getSetupCapabilitiesStatus()
      .then((payload) => {
        if (!mounted) return;
        setStatus(payload);
        setSelected(normalizeSelection(payload));
      })
      .catch((err) => {
        if (mounted) setError(String(err.message || err));
      });
    const interval = window.setInterval(() => {
      getSetupCapabilitiesStatus().then((payload) => mounted && setStatus(payload)).catch(() => {});
    }, 3000);
    return () => {
      mounted = false;
      window.clearInterval(interval);
    };
  }, []);

  function toggleCapability(capabilityId) {
    setSelected((current) => {
      if (current.includes(capabilityId)) {
        return current.filter((item) => item !== capabilityId);
      }
      return [...current, capabilityId];
    });
  }

  async function runAction(action) {
    setBusy(action);
    setError("");
    setNotice("");
    try {
      let payload;
      if (action === "selection") {
        payload = await saveSetupCapabilitySelection({ selected_capabilities: selected });
      } else if (action === "declare") {
        payload = await declareSetupCapabilities();
      } else {
        payload = await syncSetupGovernance();
      }
      if (payload?.status) {
        setStatus(payload.status);
        setSelected(normalizeSelection(payload.status));
      } else {
        await refresh({ syncSelection: true });
      }
      if (payload?.accepted === false) {
        setError(payload.error || "Action did not complete.");
      } else {
        setNotice(`${action === "sync-governance" ? "Governance sync" : action} complete.`);
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  const capabilities = status?.capabilities || {};
  const governance = status?.governance || {};
  const governanceSummary = status?.governance_summary || {};
  const manifestPreview = status?.manifest_preview || {};
  const coreSummary = manifestPreview.core_visible_summary || {};
  const available = capabilities.available || [];
  const declared = capabilities.declared || [];
  const providerModels = manifestPreview.providers?.models || [];
  const runtimeProviders = manifestPreview.runtime?.providers || {};

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Capability Setup</h2>
        <span className={`status-pill status-pill-${status?.continue_blocked ? "warning" : "success"}`}>
          {status?.continue_blocked ? "blocked" : "ready"}
        </span>
      </div>
      {notice ? <div className="callout callout-success">{notice}</div> : null}
      {error ? <div className="callout callout-danger">{error}</div> : null}
      {status?.blockers?.length ? <div className="callout callout-warning">Blockers: {status.blockers.join(", ")}</div> : null}

      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Capability declaration</span>
          <span className={`status-pill status-pill-${toneForStatus(capabilities.capability_status)}`}>
            {capabilities.capability_status || "missing"}
          </span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Governance</span>
          <span className={`status-pill status-pill-${toneForStatus(governance.governance_sync_status)}`}>
            {governance.governance_sync_status || "pending"}
          </span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Capability profile</span>
          <span className="fact-grid-value">{capabilities.capability_profile_id || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Governance version</span>
          <span className="fact-grid-value">{governance.governance_version || capabilities.governance_version || "pending"}</span>
        </div>
      </div>

      <section className="stack">
        <div className="section-heading">
          <h3>Core-Visible Summary</h3>
          <span className={`status-pill status-pill-${toneForStatus(status?.capability_current)}`}>
            {status?.capability_current ? "declared" : "pending"}
          </span>
        </div>
        <div className="fact-grid">
          {(coreSummary.provided_services || []).map((service) => (
            <div className="fact-grid-item" key={service.service_id}>
              <span className="fact-grid-label">{service.label}</span>
              <span className={`status-pill status-pill-${toneForStatus(service.enabled)}`}>
                {service.enabled ? "enabled" : "disabled"}
              </span>
              <span className="fact-grid-value">{service.provider_id || "pending"}</span>
              <span className="fact-grid-label">{joinList(service.models)}</span>
            </div>
          ))}
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Available models</span>
            <span className="fact-grid-value">
              {joinList((coreSummary.available_models || []).map((model) => `${model.provider_id}:${model.model_id}`))}
            </span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Enabled capabilities</span>
            <span className="fact-grid-value">{joinList(coreSummary.enabled_capabilities)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Disabled capabilities</span>
            <span className="fact-grid-value">{joinList(coreSummary.disabled_capabilities)}</span>
          </div>
        </div>
      </section>

      <section className="stack">
        <div className="section-heading">
          <h3>Manifest Preview</h3>
          <span className={`status-pill status-pill-${toneForStatus(status?.capability_current)}`}>
            {status?.capability_current ? "current" : "preview"}
          </span>
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Node</span>
            <span className="fact-grid-value">{manifestPreview.node_identity?.node_name || "pending"}</span>
            <span className="fact-grid-label">{manifestPreview.node_identity?.node_id || "waiting for node id"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Enabled providers</span>
            <span className="fact-grid-value">{joinList(manifestPreview.providers?.enabled)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Selected capabilities</span>
            <span className="fact-grid-value">{joinList(manifestPreview.capabilities?.selected)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Runtime API</span>
            <span className="fact-grid-value">{manifestPreview.runtime?.api_base_url || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Governance</span>
            <span className="fact-grid-value">{manifestPreview.governance?.governance_version || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Budget declaration</span>
            <span className="fact-grid-value">{manifestPreview.budget_declaration?.node_id ? "included" : "pending"}</span>
          </div>
        </div>
        <div className="fact-grid">
          {providerModels.map((provider) => (
            <div className="fact-grid-item" key={provider.provider_id}>
              <span className="fact-grid-label">{provider.provider_id}</span>
              <span className="fact-grid-value">{provider.model || "pending"}</span>
              <span className="fact-grid-label">
                {[provider.profile, provider.device, provider.cuda_mode, provider.compute_type, provider.language]
                  .filter(Boolean)
                  .join(" / ") || "default"}
              </span>
            </div>
          ))}
        </div>
        <div className="fact-grid">
          {Object.entries(runtimeProviders).map(([role, provider]) => (
            <div className="fact-grid-item" key={role}>
              <span className="fact-grid-label">{role}</span>
              <span className="fact-grid-value">{provider?.base_url || provider?.socket_path || provider?.host || "local"}</span>
              <span className="fact-grid-label">{provider?.port ? `port ${provider.port}` : provider?.provider || "runtime"}</span>
            </div>
          ))}
        </div>
        <pre className="code-panel">{JSON.stringify(manifestPreview.declaration_payload || {}, null, 2)}</pre>
      </section>

      <section className="stack">
        <div className="section-heading">
          <h3>Governance Sync</h3>
          <span className={`status-pill status-pill-${toneForStatus(governanceSummary.status)}`}>
            {governanceSummary.status || "pending"}
          </span>
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Accepted</span>
            <span className="fact-grid-value">{formatSummaryItems(governanceSummary.accepted)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Denied</span>
            <span className="fact-grid-value">{formatSummaryItems(governanceSummary.denied)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Pending</span>
            <span className="fact-grid-value">{formatSummaryItems(governanceSummary.pending)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Local required changes</span>
            <span className="fact-grid-value">{formatSummaryItems(governanceSummary.local_required_changes)}</span>
          </div>
        </div>
      </section>

      <div className="stack">
        <div className="section-heading">
          <h3>Capabilities</h3>
          <span className={`status-pill status-pill-${toneForStatus(status?.capability_current)}`}>
            {status?.capability_current ? "current" : "needs declaration"}
          </span>
        </div>
        <div className="fact-grid">
          {available.map((capabilityId) => (
            <label className="fact-grid-item" key={capabilityId}>
              <span className="fact-grid-label">{capabilityId}</span>
              <span className="fact-grid-value">
                <input
                  type="checkbox"
                  checked={selected.includes(capabilityId)}
                  onChange={() => toggleCapability(capabilityId)}
                />{" "}
                {declared.includes(capabilityId) ? "declared" : "selected"}
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className="form-actions">
        <button className="btn btn-secondary" type="button" onClick={() => runAction("selection")} disabled={busy !== ""}>
          {busy === "selection" ? "Saving..." : "Save selection"}
        </button>
        <button className="btn btn-primary" type="button" onClick={() => runAction("declare")} disabled={busy !== ""}>
          {busy === "declare" ? "Declaring..." : "Declare capabilities"}
        </button>
        <button className="btn btn-secondary" type="button" onClick={() => runAction("sync-governance")} disabled={busy !== ""}>
          {busy === "sync-governance" ? "Syncing..." : "Sync governance"}
        </button>
      </div>

      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Governance current</span>
          <span className={`status-pill status-pill-${toneForStatus(status?.governance_current)}`}>
            {status?.governance_current ? "current" : "pending"}
          </span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Last refresh</span>
          <span className="fact-grid-value">{governance.last_refresh_request_at || "pending"}</span>
        </div>
      </div>
    </article>
  );
}
