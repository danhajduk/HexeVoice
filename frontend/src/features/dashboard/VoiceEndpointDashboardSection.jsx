import { useState } from "react";
import { cancelVoiceSession, testAssistantTurn } from "../../api/client";
import { VoiceEndpointActionsCard } from "./cards/VoiceEndpointActionsCard";

function valueOrEmpty(value, fallback = "none") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

function VoicePipelinePanel({ voiceStatus }) {
  return (
    <section className="voice-endpoint-panel stack">
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

function EndpointStatusTable({ status, providerSetup, capabilities, voiceStatus, endpointStatus }) {
  const session = voiceStatus?.active_session;
  const rows = [
    ["Namespace", "voice"],
    ["Endpoint", endpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "not connected"],
    ["Endpoint FW", endpointStatus?.firmware_version || "unknown"],
    ["Device state", endpointStatus?.device_state || "unknown"],
    ["Last heartbeat", endpointStatus?.last_seen_at || "none"],
    ["Connection", voiceStatus?.connection_state || "offline"],
    ["Transport", voiceStatus?.transport_health || "offline"],
    ["Active session", session?.session_id || "none"],
    ["Backend state", session?.session_state || "idle"],
    ["Endpoint UX", session?.ux_state || "idle"],
    ["Enabled providers", providerSetup?.enabled_providers?.join(", ") || "none"],
    ["Declared capabilities", capabilities?.declared?.join(", ") || "pending"],
    ["Operational readiness", String(status?.operational_ready ?? false)],
  ];

  return (
    <section className="voice-endpoint-panel stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoint Status</p>
          <h2 className="panel-title">Device Data</h2>
        </div>
        <span className="status-pill status-pill-neutral">{valueOrEmpty(voiceStatus?.connection_state, "offline")}</span>
      </div>
      <div className="voice-endpoint-table-wrap">
        <table className="voice-endpoint-status-table">
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                <td>{valueOrEmpty(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function VoiceEndpointDashboardSection({
  status,
  providerSetup,
  capabilities,
  voiceStatus,
  endpointStatus,
  onRefresh,
}) {
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
    <section className="card stack panel voice-endpoint-main-card">
      <div className="voice-endpoint-top">
        <VoicePipelinePanel voiceStatus={voiceStatus} />
        <VoiceEndpointActionsCard
          voiceStatus={voiceStatus}
          onRefresh={onRefresh}
          onTestTurn={handleTestTurn}
          onStopSession={handleStopSession}
          actionMessage={actionMessage}
        />
      </div>
      <EndpointStatusTable
        status={status}
        providerSetup={providerSetup}
        capabilities={capabilities}
        voiceStatus={voiceStatus}
        endpointStatus={endpointStatus}
      />
    </section>
  );
}
