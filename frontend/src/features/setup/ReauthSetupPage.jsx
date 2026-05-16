import { useState } from "react";
import { finalizeSetupReauth, startSetupReauth } from "../../api/client";

export function ReauthSetupPage() {
  const [session, setSession] = useState(null);
  const [finalizeResult, setFinalizeResult] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function start() {
    setBusy("start");
    setError("");
    try {
      const payload = await startSetupReauth();
      setSession(payload);
      if (payload.approval_url) {
        window.open(payload.approval_url, "_blank", "noopener,noreferrer");
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function finalize() {
    setBusy("finalize");
    setError("");
    try {
      setFinalizeResult(await finalizeSetupReauth());
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Node Re-auth</h2>
        <span className={`status-pill status-pill-${finalizeResult?.approved ? "success" : "warning"}`}>
          {finalizeResult?.approved ? "trusted" : session?.status || "not started"}
        </span>
      </div>
      {error ? <div className="callout callout-danger">{error}</div> : null}
      {session?.warnings?.length ? <div className="callout callout-warning">{session.warnings.join(", ")}</div> : null}
      {finalizeResult?.warnings?.length ? <div className="callout callout-warning">{finalizeResult.warnings.join(", ")}</div> : null}
      <div className="callout callout-warning">
        Migration does not import trust secrets. Approve this node in Core, then finalize to save fresh trust material.
      </div>
      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Node ID</span>
          <span className="fact-grid-value">{session?.node_id || finalizeResult?.node_id || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Session</span>
          <span className="fact-grid-value">{session?.session_id || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Approval</span>
          <span className="fact-grid-value">{session?.approval_url || "pending"}</span>
        </div>
      </div>
      <div className="form-actions">
        <button className="btn btn-secondary" type="button" onClick={start} disabled={busy !== ""}>
          {busy === "start" ? "Starting..." : "Start re-auth"}
        </button>
        {session?.approval_url ? (
          <button className="btn btn-ghost" type="button" onClick={() => window.open(session.approval_url, "_blank", "noopener,noreferrer")}>
            Open approval
          </button>
        ) : null}
        <button className="btn btn-primary" type="button" onClick={finalize} disabled={busy !== "" || !session?.started}>
          {busy === "finalize" ? "Finalizing..." : "Finalize"}
        </button>
      </div>
    </article>
  );
}
