import { useEffect, useState } from "react";
import { completeSetupReady, exportSetupReadyBundle, getSetupReadyStatus, runSetupReadySmokeTest } from "../../api/client";

function toneForCheck(status) {
  if (status === "pass") return "success";
  if (status === "fail") return "danger";
  if (status === "warn") return "warning";
  return "neutral";
}

export function ReadySetupPage({ onRefresh }) {
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

  const smoke = status?.last_smoke;
  const checks = smoke?.checks || [];

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
      </div>

      <div className="form-actions">
        <button className="btn btn-primary" type="button" onClick={runSmokeTest} disabled={busy !== ""}>
          {busy === "smoke" ? "Running..." : "Run smoke test"}
        </button>
        <button className="btn btn-secondary" type="button" onClick={completeSetup} disabled={busy !== "" || status?.continue_blocked}>
          {busy === "complete" ? "Completing..." : "Complete setup"}
        </button>
        <button className="btn btn-secondary" type="button" onClick={exportBundle} disabled={busy !== ""}>
          {busy === "export" ? "Exporting..." : "Export setup bundle"}
        </button>
      </div>

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
