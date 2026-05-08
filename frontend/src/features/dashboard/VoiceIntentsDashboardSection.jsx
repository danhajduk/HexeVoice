function valueOrEmpty(value, fallback = "none") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

function formatLocalDateTime(value) {
  if (!value) {
    return "none";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function statusTone(status) {
  if (status === "active") {
    return "success";
  }
  if (status === "review_due" || status === "restricted" || status === "probation") {
    return "warning";
  }
  if (status === "retired" || status === "expired") {
    return "danger";
  }
  return "neutral";
}

function listValue(value) {
  return Array.isArray(value) ? value.filter((item) => item !== null && item !== undefined && item !== "") : [];
}

function formatDispatch(intent) {
  const dispatch = intent?.definition?.dispatch || {};
  const parts = [dispatch.command, dispatch.event_type, dispatch.type].filter(Boolean);
  return parts.length ? parts.join(" / ") : "none";
}

function formatMatcher(intent) {
  const matcher = intent?.definition?.matcher || {};
  return matcher.type || "none";
}

function formatUsage(intent) {
  const usage = intent?.usage || {};
  const count = usage.dispatch_count ?? usage.match_count ?? usage.count;
  if (count === null || count === undefined || count === "") {
    return "none";
  }
  return String(count);
}

function MetadataList({ intent }) {
  const metadata = intent?.metadata || {};
  const flags = [
    metadata.builtin ? "built in" : "",
    metadata.family ? `family: ${metadata.family}` : "",
    metadata.source ? `source: ${metadata.source}` : "",
  ].filter(Boolean);

  return <span>{flags.join(", ") || "none"}</span>;
}

function IntentExamples({ intent }) {
  const examples = listValue(intent?.definition?.utterance_examples).slice(0, 3);
  if (examples.length === 0) {
    return <span className="muted">none</span>;
  }

  return (
    <ul className="intent-example-list">
      {examples.map((example) => (
        <li key={String(example)}>{String(example)}</li>
      ))}
    </ul>
  );
}

export function VoiceIntentsDashboardSection({ voiceIntents, onRefresh }) {
  const intents = Array.isArray(voiceIntents?.intents) ? voiceIntents.intents : [];
  const registeredCount = voiceIntents?.registered_count ?? intents.length;
  const activeCount = voiceIntents?.active_count ?? intents.filter((intent) => intent.status === "active").length;

  return (
    <section className="grid operational-dashboard-grid">
      <section className="panel stack operational-content-header">
        <div className="section-heading">
          <div>
            <p className="panel-kicker">Voice Intents</p>
            <h2 className="panel-title">Registered Intents</h2>
          </div>
          <div className="hero-actions">
            <span className="status-pill status-pill-neutral">
              {activeCount}/{registeredCount} active
            </span>
            <button className="btn btn-secondary" type="button" onClick={onRefresh}>
              Refresh
            </button>
          </div>
        </div>

        <div className="intent-summary-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Configured</span>
            <span className="fact-grid-value">
              {voiceIntents ? (voiceIntents.configured === false ? "no" : "yes") : "unknown"}
            </span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Schema</span>
            <span className="fact-grid-value">{valueOrEmpty(voiceIntents?.schema_version)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Registered</span>
            <span className="fact-grid-value">{registeredCount}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Updated</span>
            <span className="fact-grid-value">{formatLocalDateTime(voiceIntents?.updated_at)}</span>
          </div>
        </div>
      </section>

      <section className="panel stack operational-content-header">
        <div className="section-heading">
          <div>
            <p className="panel-kicker">Registry</p>
            <h2 className="panel-title">Intent Contracts</h2>
          </div>
        </div>

        {intents.length === 0 ? (
          <div className="callout callout-neutral">No registered intents found.</div>
        ) : (
          <div className="intent-table-wrap">
            <table className="intent-registry-table">
              <thead>
                <tr>
                  <th scope="col">Status</th>
                  <th scope="col">Intent</th>
                  <th scope="col">Service</th>
                  <th scope="col">Dispatch</th>
                  <th scope="col">Matcher</th>
                  <th scope="col">Examples</th>
                  <th scope="col">Scope</th>
                  <th scope="col">Metadata</th>
                  <th scope="col">Usage</th>
                  <th scope="col">Updated</th>
                </tr>
              </thead>
              <tbody>
                {intents.map((intent) => (
                  <tr key={intent.intent_id}>
                    <td>
                      <span className={`status-pill status-pill-${statusTone(intent.status)}`}>
                        {valueOrEmpty(intent.status, "unknown")}
                      </span>
                    </td>
                    <th scope="row">
                      <span className="intent-title">{valueOrEmpty(intent.intent_name || intent.intent_id)}</span>
                      <code className="inline-code">{valueOrEmpty(intent.intent_id)}</code>
                      <span className="muted">{valueOrEmpty(intent.version, "v1")}</span>
                    </th>
                    <td>
                      <span>{valueOrEmpty(intent.service_id)}</span>
                      <span className="muted">{valueOrEmpty(intent.owner_service)}</span>
                    </td>
                    <td>{formatDispatch(intent)}</td>
                    <td>{formatMatcher(intent)}</td>
                    <td>
                      <IntentExamples intent={intent} />
                    </td>
                    <td>
                      <span>{valueOrEmpty(intent.privacy_class)}</span>
                      <span className="muted">{valueOrEmpty(intent.access_scope)}</span>
                    </td>
                    <td>
                      <MetadataList intent={intent} />
                    </td>
                    <td>{formatUsage(intent)}</td>
                    <td>{formatLocalDateTime(intent.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  );
}
