import { CoreConnectionCard } from "./cards/CoreConnectionCard";
import { NodeOverviewCard } from "./cards/NodeOverviewCard";
import { OperationalWarningsCard } from "./cards/OperationalWarningsCard";

function valueOrEmpty(value, fallback = "none") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

function labelize(value, fallback = "pending") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value).replaceAll("_", " ");
}

function formatMs(value) {
  if (typeof value !== "number") {
    return "none";
  }
  if (value >= 60000) {
    return `${Math.round(value / 60000)} min`;
  }
  if (value >= 10000) {
    return `${Math.round(value / 1000)} sec`;
  }
  return `${Math.round(value)} ms`;
}

function formatText(value, fallback = "none") {
  if (!value) {
    return fallback;
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "object") {
    return value.text || value.message || value.response || value.content || fallback;
  }
  return String(value);
}

function actionableVoiceIssue(voiceStatus) {
  const issue = voiceStatus?.last_error;
  if (!issue) {
    return "";
  }
  if (typeof issue === "object" && issue.recoverable && !voiceStatus?.active_session) {
    return "";
  }
  return formatText(issue, "");
}

function providerTone(provider) {
  if (!provider || provider.healthy === false || provider.configured === false || provider.error || provider.last_error) {
    return "warning";
  }
  return "success";
}

function firstModel(provider, fallback = "ready") {
  const models = provider?.models;
  if (Array.isArray(models) && models.length) {
    return models[0];
  }
  return fallback;
}

function endpointTone(endpoint) {
  return endpoint?.transport_health === "online" || endpoint?.connection_state === "connected" ? "success" : "warning";
}

function latestSession(voiceStatus) {
  const sessions = voiceStatus?.session_history?.recent_sessions;
  return Array.isArray(sessions) && sessions.length ? sessions[0] : null;
}

function stageClass(tone) {
  return `overview-pipeline-stage overview-tone-${tone}`;
}

function VoicePipelineCard({ voiceStatus }) {
  const stt = voiceStatus?.turn_pipeline?.stt;
  const assistant = voiceStatus?.turn_pipeline?.assistant;
  const tts = voiceStatus?.turn_pipeline?.tts;
  const wake = voiceStatus?.wake_provider;
  const session = latestSession(voiceStatus);
  const timings = voiceStatus?.last_turn_timings || session?.turn_timings || {};
  const endpointLabel = voiceStatus?.connection_count
    ? `${voiceStatus.connection_count} online`
    : labelize(voiceStatus?.transport_health, "pending");

  const stages = [
    {
      id: "wake",
      label: "Wake",
      tone: providerTone(wake),
      value: firstModel(wake, "armed"),
      detail: "openWakeWord",
      metric: wake?.last_detection?.detected ? "last accepted" : "armed",
    },
    {
      id: "stt",
      label: "STT",
      tone: providerTone(stt),
      value: valueOrEmpty(stt?.model, "model pending"),
      detail: "faster whisper",
      metric: formatMs(timings.stt_ms ?? stt?.last_duration_ms),
    },
    {
      id: "assistant",
      label: "AI",
      tone: providerTone(assistant),
      value: labelize(assistant?.provider || "local"),
      detail: "intent routing",
      metric: formatMs(timings.assistant_ms),
    },
    {
      id: "tts",
      label: "TTS",
      tone: providerTone(tts),
      value: labelize(tts?.provider),
      detail: valueOrEmpty(tts?.voice, "voice pending").replace("en_US-", ""),
      metric: formatMs(timings.tts_ms),
    },
    {
      id: "endpoint",
      label: "Endpoint",
      tone: voiceStatus?.transport_health === "online" ? "success" : "warning",
      value: endpointLabel,
      detail: labelize(voiceStatus?.ux_state || voiceStatus?.connection_state, "waiting"),
      metric: formatMs(timings.total_ms),
    },
  ];

  return (
    <section className="card panel overview-panel overview-pipeline-card">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Voice Pipeline</p>
          <h2 className="panel-title">Wake To Playback</h2>
        </div>
      </div>
      <div className="overview-pipeline" aria-label="Voice provider pipeline">
        {stages.map((stage, index) => (
          <div className={stageClass(stage.tone)} key={stage.id}>
            <div className="overview-stage-header">
              <span className="overview-stage-dot" />
              <span className="overview-stage-label">{stage.label}</span>
            </div>
            <strong>{stage.value}</strong>
            <span>{stage.detail}</span>
            <small>{stage.metric}</small>
            {index < stages.length - 1 ? <span className="overview-stage-connector" aria-hidden="true" /> : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function LiveVoiceCard({ voiceStatus, openVoiceEndpoint, onRefresh }) {
  const activeSession = voiceStatus?.active_session;
  const activeEndpoint = voiceStatus?.endpoint_id || activeSession?.endpoint_id;

  return (
    <section className="card panel overview-panel overview-live-card">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Live Voice</p>
          <h2 className="panel-title">{activeEndpoint ? activeEndpoint : "No Active Endpoint"}</h2>
        </div>
        <span className={`status-pill status-pill-${voiceStatus?.transport_health === "online" ? "success" : "warning"}`}>
          {labelize(voiceStatus?.transport_health, "pending")}
        </span>
      </div>
      <dl className="overview-facts overview-live-facts">
        <div>
          <dt>Current state</dt>
          <dd>{labelize(voiceStatus?.ux_state || voiceStatus?.session_state, "waiting")}</dd>
        </div>
        <div>
          <dt>Session</dt>
          <dd>{labelize(activeSession?.session_state || voiceStatus?.session_state, "idle")}</dd>
        </div>
        <div>
          <dt>Endpoints</dt>
          <dd>{voiceStatus?.connection_count ?? 0} connected</dd>
        </div>
        <div>
          <dt>Last issue</dt>
          <dd>{actionableVoiceIssue(voiceStatus) || "clear"}</dd>
        </div>
      </dl>
      <div className="overview-actions">
        <button className="btn btn-primary" type="button" onClick={openVoiceEndpoint}>
          Open Endpoints
        </button>
        <button className="btn btn-ghost" type="button" onClick={onRefresh}>
          Refresh
        </button>
      </div>
    </section>
  );
}

function EndpointOverviewCard({ voiceStatus, endpointStatus, openVoiceEndpoint }) {
  const endpointMap = voiceStatus?.endpoints && typeof voiceStatus.endpoints === "object" ? voiceStatus.endpoints : {};
  const endpointIds = Array.isArray(voiceStatus?.connected_endpoint_ids)
    ? voiceStatus.connected_endpoint_ids
    : Object.keys(endpointMap);
  const visibleIds = endpointIds.length ? endpointIds : [endpointStatus?.endpoint_id].filter(Boolean);

  return (
    <section className="card panel overview-panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoints</p>
          <h2 className="panel-title">{visibleIds.length ? `${visibleIds.length} Connected` : "No Endpoints"}</h2>
        </div>
        <button className="btn btn-ghost btn-compact" type="button" onClick={openVoiceEndpoint}>
          Details
        </button>
      </div>
      <div className="overview-endpoint-list">
        {visibleIds.length ? (
          visibleIds.map((endpointId) => {
            const endpoint = endpointMap[endpointId] || (endpointStatus?.endpoint_id === endpointId ? endpointStatus : {});
            return (
              <div className="overview-endpoint-row" key={endpointId}>
                <span className={`overview-dot overview-tone-${endpointTone(endpoint)}`} />
                <div>
                  <strong>{endpointId}</strong>
                  <span>{labelize(endpoint.ux_state || endpoint.session_state || endpoint.connection_state, "idle")}</span>
                </div>
                <small>{labelize(endpoint.transport_health || endpoint.connection_state, "pending")}</small>
              </div>
            );
          })
        ) : (
          <div className="callout callout-neutral">No endpoint connections are reporting yet.</div>
        )}
      </div>
    </section>
  );
}

function LatestTurnCard({ voiceStatus }) {
  const session = latestSession(voiceStatus);
  const timings = voiceStatus?.last_turn_timings || session?.turn_timings || {};
  const transcript = voiceStatus?.last_transcript || session?.transcript;
  const response = voiceStatus?.last_response || voiceStatus?.last_assistant || session?.assistant;
  const completedAt = session?.completed_at || session?.updated_at || voiceStatus?.active_session?.last_updated_at;

  return (
    <section className="card panel overview-panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Latest Turn</p>
          <h2 className="panel-title">{session?.session_state ? labelize(session.session_state) : "Waiting"}</h2>
        </div>
        <span className="status-pill status-pill-neutral">{formatMs(timings.total_ms ?? session?.duration_ms)}</span>
      </div>
      <div className="overview-turn-text">
        <div>
          <span className="fact-grid-label">Transcript</span>
          <p>{formatText(transcript, "No transcript recorded yet.")}</p>
        </div>
        <div>
          <span className="fact-grid-label">Response</span>
          <p>{formatText(response, "No assistant response recorded yet.")}</p>
        </div>
      </div>
      <dl className="overview-facts overview-turn-facts">
        <div>
          <dt>Endpoint</dt>
          <dd>{valueOrEmpty(session?.endpoint_id || voiceStatus?.endpoint_id)}</dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd>{valueOrEmpty(completedAt, "pending")}</dd>
        </div>
      </dl>
    </section>
  );
}

export function OverviewDashboardSection({
  status,
  onboarding,
  governance,
  operational,
  voiceStatus,
  endpointStatus,
  openSetup,
  openVoiceEndpoint,
  onRefresh,
}) {
  return (
    <section className="overview-dashboard">
      <OperationalWarningsCard status={status} onboarding={onboarding} voiceStatus={voiceStatus} />
      <div className="overview-primary-grid">
        <LiveVoiceCard voiceStatus={voiceStatus} openVoiceEndpoint={openVoiceEndpoint} onRefresh={onRefresh} />
        <VoicePipelineCard voiceStatus={voiceStatus} />
      </div>
      <div className="overview-secondary-grid">
        <EndpointOverviewCard
          voiceStatus={voiceStatus}
          endpointStatus={endpointStatus}
          openVoiceEndpoint={openVoiceEndpoint}
        />
        <LatestTurnCard voiceStatus={voiceStatus} />
        <NodeOverviewCard status={status} onboarding={onboarding} operational={operational} />
        <CoreConnectionCard
          status={status}
          onboarding={onboarding}
          governance={governance}
          operational={operational}
          openSetup={openSetup}
        />
      </div>
    </section>
  );
}
