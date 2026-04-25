import { useState } from "react";
import { cancelVoiceSession, testAssistantTurn } from "../../api/client";
import { VoiceEndpointActionsCard } from "./cards/VoiceEndpointActionsCard";
import { VoiceEndpointStatusCard } from "./cards/VoiceEndpointStatusCard";

function valueOrEmpty(value, fallback = "none") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

function VoicePipelineCard({ voiceStatus }) {
  return (
    <section className="card stack panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Speech Pipeline</p>
          <h2 className="panel-title">Latest Turn</h2>
        </div>
        <span className="status-pill status-pill-neutral">{valueOrEmpty(voiceStatus?.last_event_type, "no events")}</span>
      </div>
      <dl className="facts">
        <div>
          <dt>Transcript</dt>
          <dd>{valueOrEmpty(voiceStatus?.last_transcript)}</dd>
        </div>
        <div>
          <dt>Response</dt>
          <dd>{valueOrEmpty(voiceStatus?.last_response)}</dd>
        </div>
        <div>
          <dt>TTS stream</dt>
          <dd>{valueOrEmpty(voiceStatus?.last_tts?.stream_id)}</dd>
        </div>
        <div>
          <dt>Last error</dt>
          <dd>{valueOrEmpty(voiceStatus?.last_error?.code, "clear")}</dd>
        </div>
      </dl>
    </section>
  );
}

function VoiceSessionCard({ voiceStatus }) {
  const session = voiceStatus?.active_session;
  return (
    <section className="card stack panel">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Device Sessions</p>
          <h2 className="panel-title">Session Telemetry</h2>
        </div>
        <span className="status-pill status-pill-neutral">{valueOrEmpty(voiceStatus?.transport_health, "offline")}</span>
      </div>
      <dl className="facts">
        <div>
          <dt>Active session</dt>
          <dd>{valueOrEmpty(session?.session_id)}</dd>
        </div>
        <div>
          <dt>Backend state</dt>
          <dd>{valueOrEmpty(session?.session_state, "idle")}</dd>
        </div>
        <div>
          <dt>Endpoint UX</dt>
          <dd>{valueOrEmpty(session?.ux_state, "idle")}</dd>
        </div>
        <div>
          <dt>Connection</dt>
          <dd>{valueOrEmpty(voiceStatus?.connection_state, "offline")}</dd>
        </div>
      </dl>
    </section>
  );
}

export function VoiceEndpointDashboardSection({ status, providerSetup, capabilities, voiceStatus, onRefresh }) {
  const [actionMessage, setActionMessage] = useState("");

  async function handleTestTurn() {
    try {
      const endpointId = voiceStatus?.endpoint_id || "dashboard-test";
      const result = await testAssistantTurn(endpointId);
      setActionMessage(`Test reply: ${result.reply_text}`);
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  async function handleStopSession() {
    try {
      const result = await cancelVoiceSession();
      setActionMessage(result.accepted ? "Stop sent to active voice session." : `Stop skipped: ${result.reason}`);
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  return (
    <section className="grid operational-dashboard-grid">
      <VoiceEndpointStatusCard
        status={status}
        providerSetup={providerSetup}
        capabilities={capabilities}
        voiceStatus={voiceStatus}
      />
      <VoiceEndpointActionsCard
        voiceStatus={voiceStatus}
        onRefresh={onRefresh}
        onTestTurn={handleTestTurn}
        onStopSession={handleStopSession}
        actionMessage={actionMessage}
      />
      <VoicePipelineCard voiceStatus={voiceStatus} />
      <VoiceSessionCard voiceStatus={voiceStatus} />
    </section>
  );
}
