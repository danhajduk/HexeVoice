import { useEffect, useState } from "react";
import { getSetupHostReadiness, runSetupHostReadinessAction } from "../../api/client";

const setupModes = [
  { value: "new_node", label: "New Voice Node" },
  { value: "migrate_existing", label: "Migrate Existing" },
];

const lifecycleModes = [
  { value: "existing_supervisor", label: "Use Supervisor" },
  { value: "joined_supervisor", label: "Join Core Supervisor" },
  { value: "standalone_supervisor", label: "Standalone Supervisor" },
  { value: "unsupervised_systemd", label: "Unsupervised systemd" },
];

export function HostSetupPage({ readiness, onReadinessChange, onRefreshReadiness }) {
  const [localReadiness, setLocalReadiness] = useState(null);
  const [form, setForm] = useState({
    setup_mode: "new_node",
    lifecycle_mode: "unsupervised_systemd",
    core_base_url: "",
    supervisor_id: "",
    enrollment_token: "",
  });
  const [busyAction, setBusyAction] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [readinessSynced, setReadinessSynced] = useState(false);
  const activeReadiness = readiness || localReadiness;

  function syncFormFromReadiness(payload) {
    setReadinessSynced(true);
    setForm((current) => ({
      ...current,
      setup_mode: payload.setup_mode || current.setup_mode,
      lifecycle_mode: payload.lifecycle_mode || current.lifecycle_mode,
      core_base_url: current.core_base_url || "",
    }));
  }

  function applyReadiness(payload) {
    setLocalReadiness(payload);
    onReadinessChange?.(payload);
    syncFormFromReadiness(payload);
  }

  async function refresh() {
    const payload = onRefreshReadiness ? await onRefreshReadiness() : await getSetupHostReadiness();
    applyReadiness(payload);
  }

  useEffect(() => {
    let mounted = true;
    if (readiness && !readinessSynced) {
      syncFormFromReadiness(readiness);
    } else if (!readiness && !readinessSynced) {
      getSetupHostReadiness()
        .then((payload) => {
          if (mounted) {
            applyReadiness(payload);
          }
        })
        .catch((err) => {
          if (mounted) setError(String(err.message || err));
        });
    }
    return () => {
      mounted = false;
    };
  }, [readiness, readinessSynced]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function runAction(action) {
    setBusyAction(action);
    setNotice("");
    setError("");
    try {
      const payload = await runSetupHostReadinessAction(action, form);
      if (payload.readiness) {
        applyReadiness(payload.readiness);
      } else {
        await refresh();
      }
      if (!payload.accepted) {
        setError(payload.message || "setup_host_action_failed");
      } else {
        setNotice(payload.message || "Action completed.");
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusyAction("");
    }
  }

  function openEnrollmentToken() {
    const base = form.core_base_url || activeReadiness?.enrollment_token_url?.replace(/\/system\/supervisors\/enrollment-tokens$/, "");
    const url = activeReadiness?.enrollment_token_url || (base ? `${base.replace(/\/$/, "")}/system/supervisors/enrollment-tokens` : "");
    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Host Setup</h2>
        <span className={`status-pill status-pill-${activeReadiness?.ok ? "success" : "warning"}`}>
          {activeReadiness?.ok ? "ready" : "needs attention"}
        </span>
      </div>
      {notice ? <div className="callout callout-success">{notice}</div> : null}
      {error ? <div className="callout callout-danger">{error}</div> : null}

      <div className="form-grid">
        <label className="field">
          <span className="field-label">Setup mode</span>
          <select className="field-input" value={form.setup_mode} onChange={(event) => update("setup_mode", event.target.value)}>
            {setupModes.map((mode) => (
              <option key={mode.value} value={mode.value}>{mode.label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Lifecycle mode</span>
          <select className="field-input" value={form.lifecycle_mode} onChange={(event) => update("lifecycle_mode", event.target.value)}>
            {lifecycleModes.map((mode) => (
              <option key={mode.value} value={mode.value}>{mode.label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Core URL</span>
          <input className="field-input" value={form.core_base_url} onChange={(event) => update("core_base_url", event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Supervisor ID</span>
          <input className="field-input" value={form.supervisor_id} onChange={(event) => update("supervisor_id", event.target.value)} />
        </label>
      </div>

      {form.lifecycle_mode === "joined_supervisor" ? (
        <div className="form-actions">
          <label className="field">
            <span className="field-label">Enrollment token</span>
            <input className="field-input" value={form.enrollment_token} onChange={(event) => update("enrollment_token", event.target.value)} />
          </label>
          <button className="btn btn-secondary" type="button" onClick={openEnrollmentToken} disabled={!form.core_base_url && !activeReadiness?.enrollment_token_url}>
            Open Core enrollment token
          </button>
        </div>
      ) : null}

      <div className="form-actions">
        <button className="btn btn-ghost" type="button" onClick={() => runAction("prepare-runtime-dirs")} disabled={busyAction !== ""}>
          Runtime dirs
        </button>
        <button className="btn btn-ghost" type="button" onClick={() => runAction("check-cuda")} disabled={busyAction !== ""}>
          Check CUDA
        </button>
        <button className="btn btn-ghost" type="button" onClick={() => runAction("install-host-alias")} disabled={busyAction !== ""}>
          Add HexeVoice alias
        </button>
        {form.lifecycle_mode === "standalone_supervisor" ? (
          <button className="btn btn-secondary" type="button" onClick={() => runAction("install-standalone-supervisor")} disabled={busyAction !== ""}>
            Install Supervisor
          </button>
        ) : null}
        {form.lifecycle_mode === "joined_supervisor" ? (
          <button className="btn btn-secondary" type="button" onClick={() => runAction("install-joined-supervisor")} disabled={busyAction !== ""}>
            Join Supervisor
          </button>
        ) : null}
        <button className="btn btn-primary" type="button" onClick={() => runAction("continue")} disabled={busyAction !== ""}>
          {busyAction === "continue" ? "Saving..." : "Continue"}
        </button>
      </div>

      {activeReadiness?.blockers?.length ? <div className="callout callout-danger">Blockers: {activeReadiness.blockers.join(", ")}</div> : null}
      {activeReadiness?.warnings?.length ? <div className="callout callout-warning">Warnings: {activeReadiness.warnings.join(", ")}</div> : null}
    </article>
  );
}
