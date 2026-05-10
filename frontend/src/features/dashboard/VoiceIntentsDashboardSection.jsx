import { useState } from "react";

import { dispatchVoiceIntent, invokeVoiceIntent } from "../../api/client";

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

function IntentCard({ intent, onOpen }) {
  return (
    <button className="intent-card" type="button" onClick={() => onOpen(intent)}>
      <div className="intent-card-header">
        <span className={`status-pill status-pill-${statusTone(intent.status)}`}>
          {valueOrEmpty(intent.status, "unknown")}
        </span>
        <span className="intent-card-updated">{formatLocalDateTime(intent.updated_at)}</span>
      </div>
      <div className="intent-card-title-block">
        <span className="intent-title">{valueOrEmpty(intent.intent_name || intent.intent_id)}</span>
        <code className="inline-code">{valueOrEmpty(intent.intent_id)}</code>
      </div>
      <div className="intent-card-facts">
        <span>
          <strong>Service</strong>
          {valueOrEmpty(intent.service_id)}
        </span>
        <span>
          <strong>Dispatch</strong>
          {formatDispatch(intent)}
        </span>
        <span>
          <strong>Matcher</strong>
          {formatMatcher(intent)}
        </span>
        <span>
          <strong>Usage</strong>
          {formatUsage(intent)}
        </span>
      </div>
      <div className="intent-card-examples">
        <IntentExamples intent={intent} />
      </div>
    </button>
  );
}

function IntentDetailPopout({ intent, onClose }) {
  if (!intent) {
    return null;
  }

  return (
    <div className="intent-detail-backdrop" role="presentation" onClick={onClose}>
      <section
        className="intent-detail-popout"
        role="dialog"
        aria-modal="true"
        aria-label="Intent contract details"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="section-heading">
          <div>
            <p className="panel-kicker">Intent Contract</p>
            <h2 className="panel-title">{valueOrEmpty(intent.intent_name || intent.intent_id)}</h2>
          </div>
          <button className="btn btn-ghost btn-compact" type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="intent-detail-summary">
          <span className={`status-pill status-pill-${statusTone(intent.status)}`}>
            {valueOrEmpty(intent.status, "unknown")}
          </span>
          <code className="inline-code">{valueOrEmpty(intent.intent_id)}</code>
          <span className="muted">{valueOrEmpty(intent.version, "v1")}</span>
        </div>
        <div className="intent-detail-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Service</span>
            <span className="fact-grid-value">{valueOrEmpty(intent.service_id)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Owner</span>
            <span className="fact-grid-value">{valueOrEmpty(intent.owner_service)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Dispatch</span>
            <span className="fact-grid-value">{formatDispatch(intent)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Matcher</span>
            <span className="fact-grid-value">{formatMatcher(intent)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Privacy</span>
            <span className="fact-grid-value">{valueOrEmpty(intent.privacy_class)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Scope</span>
            <span className="fact-grid-value">{valueOrEmpty(intent.access_scope)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Metadata</span>
            <span className="fact-grid-value">
              <MetadataList intent={intent} />
            </span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Usage</span>
            <span className="fact-grid-value">{formatUsage(intent)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Updated</span>
            <span className="fact-grid-value">{formatLocalDateTime(intent.updated_at)}</span>
          </div>
        </div>
        <section className="intent-detail-section">
          <p className="panel-kicker">Examples</p>
          <IntentExamples intent={intent} />
        </section>
        <section className="intent-detail-section">
          <p className="panel-kicker">Raw Contract</p>
          <pre className="code-panel">{JSON.stringify(intent, null, 2)}</pre>
        </section>
      </section>
    </div>
  );
}

function decisionText(decision) {
  if (!decision) {
    return "none";
  }
  return [decision.status, decision.reason].filter(Boolean).join(" / ") || "none";
}

function IntentActionResult({ result, error, mode }) {
  if (error) {
    return <div className="callout callout-danger">{error}</div>;
  }

  if (!result) {
    return null;
  }

  const matched = Boolean(result.matched);

  return (
    <div className="intent-test-result">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">{mode === "invoke" ? "Invoke Result" : "Test Result"}</p>
          <h3 className="section-title">{matched ? valueOrEmpty(result.intent_id, "Matched") : "No Match"}</h3>
        </div>
        <span className={`status-pill status-pill-${matched ? "success" : "neutral"}`}>
          {matched ? "matched" : "unmatched"}
        </span>
      </div>
      <div className="intent-test-result-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Command</span>
          <span className="fact-grid-value">{valueOrEmpty(result.command)}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Provider</span>
          <span className="fact-grid-value">{valueOrEmpty(result.provider_id)}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Reply</span>
          <span className="fact-grid-value">{valueOrEmpty(result.reply_text)}</span>
        </div>
        {mode === "invoke" ? (
          <>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Event ID</span>
              <span className="fact-grid-value">{valueOrEmpty(result.recognized_event_id)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Recognition</span>
              <span className="fact-grid-value">{decisionText(result.recognition_event)}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Dispatch</span>
              <span className="fact-grid-value">{decisionText(result.dispatch_event)}</span>
            </div>
          </>
        ) : null}
      </div>
      {mode === "invoke" && result.reply_audio ? (
        <div className="intent-audio-result">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Voice Ready</span>
            <span className="fact-grid-value">{result.reply_audio.voice_ready ? "yes" : "no"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Audio</span>
            <span className="fact-grid-value">
              {result.reply_audio.audio_url ? (
                <a href={result.reply_audio.audio_url}>{result.reply_audio.stream_id || "audio"}</a>
              ) : (
                "none"
              )}
            </span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Expires</span>
            <span className="fact-grid-value">{formatLocalDateTime(result.reply_audio.expires_at)}</span>
          </div>
        </div>
      ) : null}
      {matched ? <pre className="code-panel">{JSON.stringify(result.slots || {}, null, 2)}</pre> : null}
    </div>
  );
}

export function VoiceIntentsDashboardSection({ voiceIntents, onRefresh }) {
  const intents = Array.isArray(voiceIntents?.intents) ? voiceIntents.intents : [];
  const registeredCount = voiceIntents?.registered_count ?? intents.length;
  const activeCount = voiceIntents?.active_count ?? intents.filter((intent) => intent.status === "active").length;
  const [testText, setTestText] = useState("set a timer for 5 minutes");
  const [testResult, setTestResult] = useState(null);
  const [testError, setTestError] = useState("");
  const [testingIntent, setTestingIntent] = useState(false);
  const [resultMode, setResultMode] = useState("test");
  const [selectedIntent, setSelectedIntent] = useState(null);

  async function handleIntentTest(event) {
    event.preventDefault();
    const text = testText.trim();
    if (!text || testingIntent) {
      return;
    }

    setTestingIntent(true);
    setTestError("");
    setResultMode("test");
    try {
      const result = await dispatchVoiceIntent({ endpoint_id: "dashboard-intent-test", text });
      setTestResult(result);
      await onRefresh?.();
    } catch (err) {
      setTestResult(null);
      setTestError(String(err.message || err));
    } finally {
      setTestingIntent(false);
    }
  }

  async function handleIntentInvoke() {
    const text = testText.trim();
    if (!text || testingIntent) {
      return;
    }
    if (!window.confirm("Invoke this intent now?")) {
      return;
    }

    setTestingIntent(true);
    setTestError("");
    setResultMode("invoke");
    try {
      const result = await invokeVoiceIntent({ endpoint_id: "dashboard-intent-invoke", text });
      setTestResult(result);
      await onRefresh?.();
    } catch (err) {
      setTestResult(null);
      setTestError(String(err.message || err));
    } finally {
      setTestingIntent(false);
    }
  }

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
            <p className="panel-kicker">Test Intent</p>
            <h2 className="panel-title">Dispatch Dry Run</h2>
          </div>
        </div>

        <form className="intent-test-form" onSubmit={handleIntentTest}>
          <label className="field">
            <span className="field-label">Utterance</span>
            <input
              className="field-input"
              value={testText}
              onChange={(event) => setTestText(event.target.value)}
              placeholder="set a timer for 5 minutes"
            />
          </label>
          <button className="btn btn-primary" type="submit" disabled={testingIntent || !testText.trim()}>
            {testingIntent ? "Testing..." : "Test"}
          </button>
          <button className="btn btn-secondary" type="button" onClick={handleIntentInvoke} disabled={testingIntent || !testText.trim()}>
            {testingIntent && resultMode === "invoke" ? "Invoking..." : "Invoke Intent"}
          </button>
        </form>

        <IntentActionResult result={testResult} error={testError} mode={resultMode} />
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
          <div className="intent-card-grid">
            {intents.map((intent) => (
              <IntentCard key={intent.intent_id} intent={intent} onOpen={setSelectedIntent} />
            ))}
          </div>
        )}
      </section>
      <IntentDetailPopout intent={selectedIntent} onClose={() => setSelectedIntent(null)} />
    </section>
  );
}
