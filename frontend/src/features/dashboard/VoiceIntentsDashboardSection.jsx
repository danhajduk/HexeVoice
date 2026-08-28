import { useMemo, useState } from "react";

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

const INTENT_GROUP_ORDER = ["timer", "endpoint", "playback", "voice", "other"];
const STATUS_FILTER_OPTIONS = ["all", "active", "review_due", "restricted", "probation", "retired", "expired"];

function normalizeSearch(value) {
  return String(value || "").trim().toLowerCase();
}

function intentSearchText(intent) {
  return [
    intent?.intent_id,
    intent?.intent_name,
    intent?.status,
    intent?.service_id,
    intent?.owner_service,
    intent?.privacy_class,
    intent?.access_scope,
    intent?.metadata?.family,
    intent?.metadata?.source,
    intent?.definition?.matcher?.type,
    intent?.definition?.dispatch?.command,
    intent?.definition?.dispatch?.event_type,
    intent?.definition?.dispatch?.type,
    ...listValue(intent?.definition?.utterance_examples),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function sortIntentRecords(left, right) {
  const leftStatus = left?.status === "active" ? 0 : 1;
  const rightStatus = right?.status === "active" ? 0 : 1;
  if (leftStatus !== rightStatus) {
    return leftStatus - rightStatus;
  }
  return String(left?.intent_id || "").localeCompare(String(right?.intent_id || ""));
}

function groupKeyForIntent(intent) {
  const intentPrefix = String(intent?.intent_id || "").split(".")[0]?.trim().toLowerCase();
  const metadataFamily = String(intent?.metadata?.family || "").trim().toLowerCase();
  const group = intentPrefix || metadataFamily || "other";
  return group || "other";
}

function formatIntentGroupLabel(groupKey) {
  if (!groupKey || groupKey === "other") {
    return "Other";
  }
  return groupKey
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function groupIntents(intents) {
  const groupsByKey = new Map();

  intents.forEach((intent) => {
    const key = groupKeyForIntent(intent);
    if (!groupsByKey.has(key)) {
      groupsByKey.set(key, { key, label: formatIntentGroupLabel(key), intents: [] });
    }
    groupsByKey.get(key).intents.push(intent);
  });

  return Array.from(groupsByKey.values()).sort((left, right) => {
    const leftIndex = INTENT_GROUP_ORDER.indexOf(left.key);
    const rightIndex = INTENT_GROUP_ORDER.indexOf(right.key);
    const normalizedLeft = leftIndex === -1 ? INTENT_GROUP_ORDER.length : leftIndex;
    const normalizedRight = rightIndex === -1 ? INTENT_GROUP_ORDER.length : rightIndex;
    if (normalizedLeft !== normalizedRight) {
      return normalizedLeft - normalizedRight;
    }
    return left.label.localeCompare(right.label);
  }).map((group) => ({ ...group, intents: [...group.intents].sort(sortIntentRecords) }));
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

function IntentExamples({ intent, limit = 3 }) {
  const examples = listValue(intent?.definition?.utterance_examples).slice(0, limit);
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

function countByPredicate(intents, predicate) {
  return intents.reduce((count, intent) => (predicate(intent) ? count + 1 : count), 0);
}

function formatFilterLabel(value) {
  if (value === "all") {
    return "All statuses";
  }
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function IntentCard({ intent, onOpen, selected }) {
  return (
    <button className={`intent-card${selected ? " intent-card-selected" : ""}`} type="button" onClick={() => onOpen(intent)}>
      <div className="intent-card-header">
        <span className={`status-pill status-pill-${statusTone(intent.status)}`}>
          {valueOrEmpty(intent.status, "unknown")}
        </span>
        <span className="intent-card-updated">Usage {formatUsage(intent)}</span>
      </div>
      <div className="intent-card-title-block">
        <span className="intent-title">{valueOrEmpty(intent.intent_name || intent.intent_id)}</span>
        <code className="inline-code">{valueOrEmpty(intent.intent_id)}</code>
      </div>
      <div className="intent-card-facts">
        <span>
          <strong>Matcher</strong>
          {formatMatcher(intent)}
        </span>
        <span>
          <strong>Updated</strong>
          {formatLocalDateTime(intent.updated_at)}
        </span>
      </div>
      <div className="intent-card-examples">
        <IntentExamples intent={intent} limit={2} />
      </div>
    </button>
  );
}

function IntentDetailPanel({ intent }) {
  if (!intent) {
    return <div className="callout callout-neutral">Select an intent contract to inspect its routing, ownership, privacy, and raw payload.</div>;
  }

  return (
    <div className="intent-detail-inline">
      <div className="selected-endpoint-summary">
        <div>
          <strong>{valueOrEmpty(intent.intent_name || intent.intent_id)}</strong>
          <span>{valueOrEmpty(intent.intent_id)}</span>
        </div>
        <span className={`status-pill status-pill-${statusTone(intent.status)}`}>
          {valueOrEmpty(intent.status, "unknown")}
        </span>
        <span>{valueOrEmpty(intent.version, "v1")}</span>
        <span>{formatMatcher(intent)}</span>
        <span>{formatUsage(intent)} uses</span>
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
          <span className="fact-grid-label">Updated</span>
          <span className="fact-grid-value">{formatLocalDateTime(intent.updated_at)}</span>
        </div>
      </div>
      <section className="intent-detail-section">
        <p className="panel-kicker">Examples</p>
        <IntentExamples intent={intent} limit={8} />
      </section>
      <section className="intent-detail-section">
        <p className="panel-kicker">Raw Contract</p>
        <pre className="code-panel">{JSON.stringify(intent, null, 2)}</pre>
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
  const reviewCount = countByPredicate(intents, (intent) => intent.status && intent.status !== "active");
  const builtinCount = countByPredicate(intents, (intent) => intent?.metadata?.builtin);
  const [testText, setTestText] = useState("set a timer for 5 minutes");
  const [testResult, setTestResult] = useState(null);
  const [testError, setTestError] = useState("");
  const [testingIntent, setTestingIntent] = useState(false);
  const [resultMode, setResultMode] = useState("test");
  const [selectedIntent, setSelectedIntent] = useState(null);
  const [searchText, setSearchText] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const filteredIntents = useMemo(() => {
    const query = normalizeSearch(searchText);
    return intents
      .filter((intent) => (statusFilter === "all" ? true : intent.status === statusFilter))
      .filter((intent) => (query ? intentSearchText(intent).includes(query) : true))
      .sort(sortIntentRecords);
  }, [intents, searchText, statusFilter]);
  const groupedIntents = groupIntents(filteredIntents);
  const selectedIntentRecord =
    selectedIntent && intents.find((intent) => intent.intent_id === selectedIntent.intent_id)
      ? intents.find((intent) => intent.intent_id === selectedIntent.intent_id)
      : filteredIntents[0] || null;

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
      <section className="panel stack operational-content-header intent-dashboard-shell">
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
            <span className="fact-grid-label">Review</span>
            <span className="fact-grid-value">{reviewCount}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Built in</span>
            <span className="fact-grid-value">{builtinCount}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Updated</span>
            <span className="fact-grid-value">{formatLocalDateTime(voiceIntents?.updated_at)}</span>
          </div>
        </div>
      </section>

      <div className="intent-main-layout">
        <section className="panel stack intent-registry-panel">
          <div className="section-heading">
            <div>
              <p className="panel-kicker">Registry</p>
              <h2 className="panel-title">Intent Contracts</h2>
            </div>
            <span className="status-pill status-pill-neutral">
              {filteredIntents.length}/{registeredCount} shown
            </span>
          </div>

          <div className="intent-filter-row">
            <label className="field">
              <span className="field-label">Search</span>
              <input
                className="field-input"
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
                placeholder="timer.create, status, example"
              />
            </label>
            <label className="field">
              <span className="field-label">Status</span>
              <select className="field-input" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                {STATUS_FILTER_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {formatFilterLabel(option)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {intents.length === 0 ? (
            <div className="callout callout-neutral">No registered intents found.</div>
          ) : filteredIntents.length === 0 ? (
            <div className="callout callout-neutral">No intents match the current filters.</div>
          ) : (
            <div className="intent-group-stack">
              {groupedIntents.map((group) => (
                <section className="intent-group" key={group.key}>
                  <div className="intent-group-header">
                    <div>
                      <p className="panel-kicker">Intent Group</p>
                      <h3 className="section-title">{group.label}</h3>
                    </div>
                    <span className="status-pill status-pill-neutral">
                      {group.intents.filter((intent) => intent.status === "active").length}/{group.intents.length} active
                    </span>
                  </div>
                  <div className="intent-card-grid">
                    {group.intents.map((intent) => (
                      <IntentCard
                        key={intent.intent_id}
                        intent={intent}
                        selected={intent.intent_id === selectedIntentRecord?.intent_id}
                        onOpen={setSelectedIntent}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </section>

        <section className="panel stack intent-tester-panel">
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
            <div className="intent-test-actions">
              <button className="btn btn-primary" type="submit" disabled={testingIntent || !testText.trim()}>
                {testingIntent && resultMode === "test" ? "Testing..." : "Test"}
              </button>
              <button className="btn btn-secondary intent-live-invoke-btn" type="button" onClick={handleIntentInvoke} disabled={testingIntent || !testText.trim()}>
                {testingIntent && resultMode === "invoke" ? "Invoking..." : "Invoke Intent"}
              </button>
            </div>
          </form>

          <IntentActionResult result={testResult} error={testError} mode={resultMode} />
        </section>
      </div>

      <section className="panel stack operational-content-header intent-detail-panel">
        <div className="section-heading">
          <div>
            <p className="panel-kicker">Selected Intent</p>
            <h2 className="panel-title">Contract Detail</h2>
          </div>
        </div>
        <IntentDetailPanel intent={selectedIntentRecord} />
      </section>
    </section>
  );
}
