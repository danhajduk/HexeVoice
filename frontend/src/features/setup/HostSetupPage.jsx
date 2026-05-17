import { useEffect, useMemo, useState } from "react";
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

function toneForStatus(status) {
  if (status === "pass") return "success";
  if (status === "fail") return "danger";
  if (status === "warn") return "warning";
  return "neutral";
}

function checkLabel(checks, id) {
  const check = checks.find((item) => item.id === id);
  return check?.status || "unknown";
}

export function HostSetupPage() {
  const [readiness, setReadiness] = useState(null);
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

  const checks = readiness?.checks || [];
  const summaryChecks = useMemo(
    () => [
      ["backend", "Backend"],
      ["frontend", "Frontend"],
      ["runtime_dirs", "Runtime dirs"],
      ["docker", "Docker"],
      ["cuda", "CUDA"],
      ["systemd", "systemd"],
      ["supervisor", "Supervisor"],
      ["host_alias", "Host alias"],
      ["firmware", "Firmware"],
      ["stt_model", "STT"],
      ["tts_model", "TTS"],
      ["wake_model", "Wake"],
    ],
    [],
  );

  async function refresh() {
    const payload = await getSetupHostReadiness();
    setReadiness(payload);
    setForm((current) => ({
      ...current,
      setup_mode: payload.setup_mode || current.setup_mode,
      lifecycle_mode: payload.lifecycle_mode || current.lifecycle_mode,
      core_base_url: current.core_base_url || "",
    }));
  }

  useEffect(() => {
    let mounted = true;
    getSetupHostReadiness()
      .then((payload) => {
        if (mounted) {
          setReadiness(payload);
          setForm((current) => ({
            ...current,
            setup_mode: payload.setup_mode || current.setup_mode,
            lifecycle_mode: payload.lifecycle_mode || current.lifecycle_mode,
          }));
        }
      })
      .catch((err) => {
        if (mounted) setError(String(err.message || err));
      });
    return () => {
      mounted = false;
    };
  }, []);

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
        setReadiness(payload.readiness);
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
    const base = form.core_base_url || readiness?.enrollment_token_url?.replace(/\/system\/supervisors\/enrollment-tokens$/, "");
    const url = readiness?.enrollment_token_url || (base ? `${base.replace(/\/$/, "")}/system/supervisors/enrollment-tokens` : "");
    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Host Setup</h2>
        <span className={`status-pill status-pill-${readiness?.ok ? "success" : "warning"}`}>
          {readiness?.ok ? "ready" : "needs attention"}
        </span>
      </div>
      {notice ? <div className="callout callout-success">{notice}</div> : null}
      {error ? <div className="callout callout-danger">{error}</div> : null}

      <div className="host-readiness-pills">
        {summaryChecks.map(([id, label]) => {
          const status = checkLabel(checks, id);
          return (
            <span className={`status-pill status-pill-${toneForStatus(status)}`} key={id}>
              {label}: {status}
            </span>
          );
        })}
      </div>

      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Host</span>
          <span className="fact-grid-value">{readiness?.hostname || "loading"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">LAN</span>
          <span className="fact-grid-value">{readiness?.lan_host || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Production setup</span>
          <span className="fact-grid-value">{readiness?.production_setup_url || "/setup/host"}</span>
        </div>
      </div>

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
          <button className="btn btn-secondary" type="button" onClick={openEnrollmentToken} disabled={!form.core_base_url && !readiness?.enrollment_token_url}>
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

      {readiness?.blockers?.length ? <div className="callout callout-danger">Blockers: {readiness.blockers.join(", ")}</div> : null}
      {readiness?.warnings?.length ? <div className="callout callout-warning">Warnings: {readiness.warnings.join(", ")}</div> : null}
    </article>
  );
}
