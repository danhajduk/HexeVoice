import { useEffect, useState } from "react";
import {
  acknowledgeSetupReadyWarnings,
  completeSetupReady,
  exportSetupReadyBundle,
  getSetupReadyStatus,
  runSetupReadyAction,
  runSetupReadySmokeTest,
} from "../../api/client";

function toneForCheck(status) {
  if (status === "pass") return "success";
  if (status === "fail") return "danger";
  if (status === "warn") return "warning";
  return "neutral";
}

function setupSectionFromRoute(route) {
  if (route === "/setup/trust/reauth") return "reauth";
  if (route === "/setup/trust") return "onboard";
  if (route?.startsWith("/setup/")) return route.replace("/setup/", "") || "host";
  return "ready";
}

export function ReadySetupPage({ onRefresh, onOpenSetupSection }) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    const payload = await getSetupReadyStatus();
    setStatus(payload);
    return payload;
  }

  useEffect(() => {
    let mounted = true;
    getSetupReadyStatus()
      .then((payload) => mounted && setStatus(payload))
      .catch((err) => mounted && setError(String(err.message || err)));
    const interval = window.setInterval(() => {
      getSetupReadyStatus().then((payload) => mounted && setStatus(payload)).catch(() => {});
    }, 3000);
    return () => {
      mounted = false;
      window.clearInterval(interval);
    };
  }, []);

  async function runSmokeTest() {
    setBusy("smoke");
    setError("");
    setNotice("");
    try {
      const payload = await runSetupReadySmokeTest();
      setStatus(payload.status);
      setNotice(payload.smoke?.ok ? "Smoke test passed." : "Smoke test found blockers.");
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function completeSetup() {
    setBusy("complete");
    setError("");
    setNotice("");
    try {
      const payload = await completeSetupReady();
      setStatus(payload.status);
      if (payload.accepted) {
        setNotice("Setup complete.");
        if (onRefresh) await onRefresh();
        if (typeof window !== "undefined") {
          window.history.replaceState(null, "", "/");
        }
      } else {
        setError(payload.message || "Setup is still blocked.");
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function acknowledgeWarnings() {
    setBusy("ack-warnings");
    setError("");
    setNotice("");
    try {
      const payload = await acknowledgeSetupReadyWarnings();
      setStatus(payload.status);
      setNotice("Warnings acknowledged.");
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function exportBundle() {
    setBusy("export");
    setError("");
    setNotice("");
    try {
      const payload = await exportSetupReadyBundle();
      setNotice("Final setup export created.");
      if (typeof window !== "undefined" && payload.download_url) {
        window.open(payload.download_url, "_blank", "noopener,noreferrer");
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function runRecoveryAction(action) {
    if (!action || action.disabled) {
      return;
    }
    if (action.kind === "external_url") {
      if (typeof window !== "undefined" && action.url) {
        window.open(action.url, "_blank", "noopener,noreferrer");
      }
      return;
    }
    if (action.kind === "setup_route") {
      if (onOpenSetupSection) {
        onOpenSetupSection(setupSectionFromRoute(action.route));
      } else if (typeof window !== "undefined" && action.route) {
        window.history.pushState(null, "", action.route);
      }
      return;
    }
    setBusy(action.id);
    setError("");
    setNotice("");
    try {
      const payload = await runSetupReadyAction(action.id);
      setStatus(payload.status);
      if (payload.accepted) {
        setNotice(`${action.label || action.id} complete.`);
        if (action.id === "export-setup-bundle" && payload.result?.export?.download_url && typeof window !== "undefined") {
          window.open(payload.result.export.download_url, "_blank", "noopener,noreferrer");
        }
      } else {
        setError(payload.message || `${action.label || action.id} did not complete.`);
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  const smoke = status?.last_smoke;
  const checks = smoke?.checks || [];
  const finalSummary = status?.final_summary || {};
  const warningAck = status?.warning_acknowledgement || {};
  const recovery = status?.recovery_actions || {};
  const recoveryActions = recovery.actions || [];

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Ready Check</h2>
        <span className={`status-pill status-pill-${status?.completed ? "success" : status?.continue_blocked ? "warning" : "success"}`}>
          {status?.completed ? "complete" : status?.continue_blocked ? "blocked" : "ready"}
        </span>
      </div>
      {notice ? <div className="callout callout-success">{notice}</div> : null}
      {error ? <div className="callout callout-danger">{error}</div> : null}

      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Smoke test</span>
          <span className={`status-pill status-pill-${smoke?.ok ? "success" : "warning"}`}>
            {smoke ? (smoke.ok ? "passed" : "blocked") : "not run"}
          </span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Summary</span>
          <span className="fact-grid-value">
            {smoke ? `${smoke.summary?.passed || 0} pass / ${smoke.summary?.failed || 0} fail / ${smoke.summary?.warnings || 0} warn` : "pending"}
          </span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Root redirect</span>
          <span className={`status-pill status-pill-${status?.setup_root_redirect_active ? "warning" : "success"}`}>
            {status?.setup_root_redirect_active ? "setup" : "dashboard"}
          </span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Last run</span>
          <span className="fact-grid-value">{smoke?.ran_at || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Warnings</span>
          <span className={`status-pill status-pill-${warningAck.required ? "warning" : "success"}`}>
            {warningAck.required ? "ack required" : "clear"}
          </span>
          <span className="fact-grid-label">{warningAck.acknowledged_at || "not acknowledged"}</span>
        </div>
      </div>

      <section className="stack">
        <div className="section-heading">
          <h3>Final Summary</h3>
          <span className={`status-pill status-pill-${status?.completed ? "success" : "warning"}`}>
            {status?.completed ? "complete" : "pending"}
          </span>
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Node</span>
            <span className="fact-grid-value">{finalSummary.node_name || "pending"}</span>
            <span className="fact-grid-label">{finalSummary.node_id || "waiting"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Core</span>
            <span className="fact-grid-value">{finalSummary.core_base_url || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Local API</span>
            <span className="fact-grid-value">{finalSummary.api_base_url || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Local UI</span>
            <span className="fact-grid-value">{finalSummary.ui_endpoint || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Providers</span>
            <span className={`status-pill status-pill-${finalSummary.provider_health?.blocked ? "warning" : "success"}`}>
              {finalSummary.provider_health?.blocked ? "blocked" : "ready"}
            </span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Capabilities</span>
            <span className="fact-grid-value">{finalSummary.capability_status || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Governance</span>
            <span className="fact-grid-value">{finalSummary.governance_status || "pending"}</span>
            <span className="fact-grid-label">{finalSummary.governance_version || "no version"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Lifecycle</span>
            <span className="fact-grid-value">{finalSummary.lifecycle_mode || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Runtime dirs</span>
            <span className={`status-pill status-pill-${finalSummary.runtime_dirs?.missing?.length ? "warning" : "success"}`}>
              {finalSummary.runtime_dirs?.missing?.length ? "missing" : "ready"}
            </span>
            <span className="fact-grid-label">{finalSummary.runtime_dirs?.root || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Accepted warnings</span>
            <span className="fact-grid-value">
              {finalSummary.accepted_warnings?.length
                ? finalSummary.accepted_warnings.map((warning) => warning.label || warning.id).join(", ")
                : "none"}
            </span>
          </div>
        </div>
      </section>

      <div className="form-actions">
        <button className="btn btn-primary" type="button" onClick={runSmokeTest} disabled={busy !== ""}>
          {busy === "smoke" ? "Running..." : "Run smoke test"}
        </button>
        <button className="btn btn-secondary" type="button" onClick={acknowledgeWarnings} disabled={busy !== "" || !warningAck.required}>
          {busy === "ack-warnings" ? "Acknowledging..." : "Acknowledge warnings"}
        </button>
        <button className="btn btn-secondary" type="button" onClick={completeSetup} disabled={busy !== "" || status?.continue_blocked || warningAck.required}>
          {busy === "complete" ? "Completing..." : "Complete setup"}
        </button>
        <button className="btn btn-secondary" type="button" onClick={exportBundle} disabled={busy !== ""}>
          {busy === "export" ? "Exporting..." : "Export setup bundle"}
        </button>
      </div>

      <section className="stack">
        <div className="section-heading">
          <h3>Recovery Actions</h3>
          <span className="status-pill status-pill-neutral">{recovery.failed_step_route || "/setup/ready"}</span>
        </div>
        <div className="form-actions">
          {recoveryActions.map((action) => (
            <button
              className={action.id === "run-full-smoke-test" ? "btn btn-primary" : "btn btn-secondary"}
              type="button"
              key={action.id}
              onClick={() => runRecoveryAction(action)}
              disabled={busy !== "" || action.disabled}
            >
              {busy === action.id ? "Running..." : action.label || action.id}
            </button>
          ))}
        </div>
        {recovery.last_action ? (
          <div className="callout">
            Last recovery action: {recovery.last_action} {recovery.last_action_at || ""}
          </div>
        ) : null}
      </section>

      <div className="fact-grid">
        {checks.map((check) => (
          <div className="fact-grid-item" key={check.id}>
            <span className="fact-grid-label">{check.label || check.id}</span>
            <span className={`status-pill status-pill-${toneForCheck(check.status)}`}>{check.status}</span>
            <span className="fact-grid-value">{check.message}</span>
          </div>
        ))}
      </div>
    </article>
  );
}
