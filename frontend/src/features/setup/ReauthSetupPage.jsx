import { useState } from "react";
import { finalizeSetupReauth, startSetupReauth } from "../../api/client";

export function ReauthSetupPage() {
  const [session, setSession] = useState(null);
  const [finalizeResult, setFinalizeResult] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const currentStatus = finalizeResult?.status || session?.status || "not_started";
  const nodeId = session?.node_id || finalizeResult?.node_id || "";
  const trustFinalized = Boolean(finalizeResult?.approved);
  const statusChecks = [
    { label: "Waiting", complete: session?.started && !["approved", "rejected", "expired"].includes(currentStatus) },
    { label: "Approved", complete: currentStatus === "approved" || trustFinalized },
    { label: "Rejected", complete: currentStatus === "rejected" },
    { label: "Expired", complete: currentStatus === "expired" },
    { label: "Trust finalized", complete: trustFinalized },
    { label: "Node ID received", complete: Boolean(nodeId) },
    { label: "Ready to continue", complete: trustFinalized && Boolean(nodeId) },
  ];

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
        <h2>Migration Re-auth</h2>
        <span className={`status-pill status-pill-${finalizeResult?.approved ? "success" : "warning"}`}>
          {finalizeResult?.approved ? "trusted" : currentStatus}
        </span>
      </div>
      {error ? <div className="callout callout-danger">{error}</div> : null}
      {session?.warnings?.length ? <div className="callout callout-warning">{session.warnings.join(", ")}</div> : null}
      {finalizeResult?.warnings?.length ? <div className="callout callout-warning">{finalizeResult.warnings.join(", ")}</div> : null}
      <div className="callout callout-warning">
        Migration re-auth uses `/setup/trust/reauth`. Migration does not import trust secrets. Approve this node in Core, then finalize to save fresh trust material.
      </div>
      <div className="fact-grid">
        {statusChecks.map((item) => (
          <div className="fact-grid-item" key={item.label}>
            <span className="fact-grid-label">{item.label}</span>
            <span className="fact-grid-value">{item.complete ? "yes" : "no"}</span>
          </div>
        ))}
        <div className="fact-grid-item">
          <span className="fact-grid-label">Node ID</span>
          <span className="fact-grid-value">{nodeId || "pending"}</span>
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
          {busy === "start" ? "Starting..." : "Start migration re-auth"}
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
