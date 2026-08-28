function warningMessage(value) {
  if (!value) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  return value.message || value.code || JSON.stringify(value);
}

function actionableVoiceIssue(voiceStatus) {
  const issue = voiceStatus?.last_error;
  if (!issue) {
    return "";
  }
  if (typeof issue === "object" && issue.recoverable && !voiceStatus?.active_session) {
    return "";
  }
  return warningMessage(issue);
}

export function OperationalWarningsCard({ status, onboarding, voiceStatus }) {
  const blockers = status?.blocking_reasons || [];
  const voiceIssue = actionableVoiceIssue(voiceStatus);
  const warnings = [...blockers, voiceIssue].filter(Boolean);

  if (warnings.length === 0) {
    return (
      <section className="overview-warning-strip">
        <div>
          <span className="overview-dot overview-tone-success" />
          <strong>No operational warnings</strong>
        </div>
        <span>{status?.operational_ready ? "Node ready" : onboarding?.current_step_label || "Pending readiness"}</span>
      </section>
    );
  }

  return (
    <section className="card stack panel overview-panel overview-warning-card">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Warnings</p>
          <h2 className="panel-title">Operational Warnings</h2>
        </div>
      </div>
      <div className="callout callout-warning">
        {warnings.join(", ")}
      </div>
      <div className="overview-warning-detail">
        <div className="state-row">
          <span className="state-label">Current step</span>
          <span className="state-value">{onboarding?.current_step_label || status?.current_step_label || "pending"}</span>
        </div>
      </div>
    </section>
  );
}
