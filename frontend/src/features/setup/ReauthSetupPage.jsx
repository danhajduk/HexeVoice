import { useState } from "react";
import { finalizeSetupReauth, runSetupTrustAction, startSetupReauth } from "../../api/client";

export function ReauthSetupPage({ onContinue }) {
  const [session, setSession] = useState(null);
  const [finalizeResult, setFinalizeResult] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
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
  const blockers = [
    !session?.started && !trustFinalized ? "required_migrated_reauth" : "",
    currentStatus === "core_unreachable" ? "core_unreachable" : "",
    currentStatus === "unsupported" ? "unsupported_reauth" : "",
    currentStatus === "rejected" ? "reauth_rejected" : "",
    currentStatus === "expired" ? "reauth_expired" : "",
    session?.started && !nodeId ? "missing_node_identity" : "",
    finalizeResult && !finalizeResult.approved ? "local_trust_activation_failure" : "",
  ].filter(Boolean);

  async function start() {
    setBusy("start");
    setError("");
    setNotice("");
    try {
      const payload = await startSetupReauth();
      setSession(payload);
      setNotice("Migration re-auth session started.");
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
    setNotice("");
    try {
      const payload = await finalizeSetupReauth();
      setFinalizeResult(payload);
      setNotice(`Re-auth status: ${payload.status}.`);
      if (payload.approved && payload.node_id) {
        onContinue?.(payload);
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy("");
    }
  }

  async function recover(action) {
    setBusy(action);
    setError("");
    setNotice("");
    try {
      const payload = await runSetupTrustAction(action);
      if (payload.action === "clear-expired-sessions" && payload.accepted) {
        setSession(null);
      } else {
        setSession((current) => ({
          ...(current || {}),
          status: payload.session_state || current?.status,
          approval_url: payload.approval_url || current?.approval_url,
          node_id: payload.node_id || current?.node_id,
        }));
      }
      if (payload.action === "retry-trust-finalize" || payload.action === "repoll-approval") {
        setFinalizeResult({
          status: payload.session_state || payload.message,
          approved: payload.trust_state === "trusted",
          node_id: payload.node_id,
          trust_state: payload.trust_state,
          warnings: payload.warnings || [],
        });
        if (payload.trust_state === "trusted" && payload.node_id) {
          onContinue?.(payload);
        }
      }
      if (payload.approval_url && action === "reopen-core-approval") {
        window.open(payload.approval_url, "_blank", "noopener,noreferrer");
      }
      if (!payload.accepted) {
        setError(payload.message);
      } else {
        const supportSummary = Object.entries(payload.core_support || {})
          .map(([key, value]) => `${key}:${value?.supported ? "supported" : "blocked"}`)
          .join(", ");
        setNotice(supportSummary ? `${payload.message} (${supportSummary})` : payload.message);
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
        <h2>Migration Re-auth</h2>
        <span className={`status-pill status-pill-${finalizeResult?.approved ? "success" : "warning"}`}>
          {finalizeResult?.approved ? "trusted" : currentStatus}
        </span>
      </div>
      {notice ? <div className="callout callout-success">{notice}</div> : null}
      {error ? <div className="callout callout-danger">{error}</div> : null}
      {blockers.length ? <div className="callout callout-danger">Blockers: {blockers.join(", ")}</div> : null}
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
      <div className="form-actions">
        <button className="btn btn-secondary" type="button" onClick={() => recover("repoll-approval")} disabled={busy !== "" || !session?.started}>
          {busy === "repoll-approval" ? "Checking..." : "Re-poll approval"}
        </button>
        <button className="btn btn-secondary" type="button" onClick={() => recover("retry-trust-finalize")} disabled={busy !== "" || !session?.started}>
          {busy === "retry-trust-finalize" ? "Retrying..." : "Retry trust finalize"}
        </button>
        <button className="btn btn-ghost" type="button" onClick={() => recover("reopen-core-approval")} disabled={busy !== "" || !session?.approval_url}>
          Open Core approval
        </button>
        <button className="btn btn-ghost" type="button" onClick={() => recover("clear-expired-sessions")} disabled={busy !== ""}>
          Clear expired session
        </button>
        <button className="btn btn-ghost" type="button" onClick={() => recover("recheck-core-trust-support")} disabled={busy !== ""}>
          Re-check Core support
        </button>
      </div>
    </article>
  );
}
