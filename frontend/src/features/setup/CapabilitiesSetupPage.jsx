import { useEffect, useState } from "react";
import {
  declareSetupCapabilities,
  getSetupCapabilitiesStatus,
  saveSetupCapabilitySelection,
  syncSetupGovernance,
} from "../../api/client";

function toneForStatus(value) {
  if (value === true || value === "accepted" || value === "issued" || value === "fresh") return "success";
  if (value === false || value === "missing" || value === "pending_capability") return "warning";
  return "neutral";
}

function normalizeSelection(status) {
  return status?.capabilities?.selected?.length
    ? status.capabilities.selected
    : status?.capabilities?.available || [];
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
  const available = capabilities.available || [];
  const declared = capabilities.declared || [];

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
