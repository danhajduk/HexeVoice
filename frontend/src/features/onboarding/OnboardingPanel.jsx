function toneForStep(stepId, trustState, operationalReady) {
  if (operationalReady || stepId === "ready") {
    return "success";
  }
  if (trustState === "revoked") {
    return "danger";
  }
  if (stepId === "approval" || stepId === "trust_activation" || stepId === "governance_sync") {
    return "warning";
  }
  return "neutral";
}

function StageCard({ title, tone, action, children }) {
  return (
    <article className={`stage-card stage-tone-${tone}`}>
      <div className="stage-card-header">
        <div>
          <p className="panel-kicker">Current Stage</p>
          <h2 className="panel-title">{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </article>
  );
}

function renderStageBody({ status, onboarding }) {
  const stepId = onboarding?.current_step_id || "node_identity";
  const capabilitySetup = onboarding?.capability_setup;
  const blockers = capabilitySetup?.blocking_reasons || status?.blocking_reasons || [];

  if (stepId === "approval") {
    return (
      <>
        <div className="callout callout-warning">
          Operator approval is pending in Core. Open the approval link and keep this page visible while polling and
          finalization continue.
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Approval URL</span>
            <span className="fact-grid-value stage-link">{onboarding?.approval_url || "Waiting for session start"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Session State</span>
            <span className="fact-grid-value">{onboarding?.session_state || "pending"}</span>
          </div>
        </div>
      </>
    );
  }

  if (stepId === "trust_activation") {
    return (
      <>
        <div className="callout">
          Approval has been granted. The node now needs to consume and persist the trust activation payload exactly
          once before post-trust setup can begin.
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Trust State</span>
            <span className="fact-grid-value">{status?.trust_state || "untrusted"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Next Action</span>
            <span className="fact-grid-value">{onboarding?.next_action || "finalize_trust_activation"}</span>
          </div>
        </div>
      </>
    );
  }

  if (stepId === "provider_setup" || stepId === "capability_declaration" || stepId === "governance_sync" || stepId === "ready") {
    return (
      <>
        <div className="callout">
          The node is in post-trust setup. Provider readiness, capability declaration, governance sync, and final
          operational review now determine whether the node can become fully ready.
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Capability State</span>
            <span className="fact-grid-value">{onboarding?.capability_status || "missing"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Governance State</span>
            <span className="fact-grid-value">{onboarding?.governance_sync_status || "pending_capability"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Providers Enabled</span>
            <span className="fact-grid-value">
              {capabilitySetup?.provider_selection?.enabled?.join(", ") || "none"}
            </span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Readiness</span>
            <span className="fact-grid-value">{onboarding?.operational_ready ? "operational" : "blocked"}</span>
          </div>
        </div>
        {blockers.length > 0 ? (
          <div className="callout callout-warning">
            Blocking reasons: {blockers.join(", ")}
          </div>
        ) : null}
      </>
    );
  }

  return (
    <>
      <div className="callout">
        This node is still in pre-trust onboarding. Local identity, Core connectivity, bootstrap discovery, and
        registration determine when approval can begin.
      </div>
      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Onboarding State</span>
          <span className="fact-grid-value">{onboarding?.onboarding_state || "waiting_for_local_setup"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Next Action</span>
          <span className="fact-grid-value">{onboarding?.next_action || "configure_node_identity"}</span>
        </div>
      </div>
    </>
  );
}

export function OnboardingPanel({ status, onboarding }) {
  const tone = toneForStep(onboarding?.current_step_id, status?.trust_state, status?.operational_ready);
  const action = onboarding?.approval_url ? (
    <a className="status-pill status-pill-warning" href={onboarding.approval_url} target="_blank" rel="noreferrer">
      Open approval
    </a>
  ) : (
    <span className="status-pill status-pill-neutral">{onboarding?.next_action || "follow setup flow"}</span>
  );

  return (
    <StageCard title={onboarding?.current_step_label || "Node Identity"} tone={tone} action={action}>
      {renderStageBody({ status, onboarding })}
    </StageCard>
  );
}
