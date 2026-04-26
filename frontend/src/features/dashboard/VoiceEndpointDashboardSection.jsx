import { useEffect, useState } from "react";
import { cancelVoiceSession, testAssistantTurn } from "../../api/client";
import { VoiceEndpointActionsCard } from "./cards/VoiceEndpointActionsCard";

const LATEST_SPEECH_VISIBLE_MS = 20000;

function valueOrEmpty(value, fallback = "none") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

function VoicePipelinePanel({ voiceStatus }) {
  const [visibleTranscript, setVisibleTranscript] = useState("");

  useEffect(() => {
    const transcript = voiceStatus?.last_transcript || "";
    setVisibleTranscript(transcript);

    if (!transcript) {
      return undefined;
    }

    const timer = window.setTimeout(() => {
      setVisibleTranscript("");
    }, LATEST_SPEECH_VISIBLE_MS);

    return () => window.clearTimeout(timer);
  }, [voiceStatus?.last_transcript]);

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
          <dd>{valueOrEmpty(visibleTranscript)}</dd>
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

function EndpointStatusTable({ voiceStatus, endpointStatus }) {
  const session = voiceStatus?.active_session;
  const endpointRows = [
    {
      endpointId: endpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "not connected",
      firmwareVersion: endpointStatus?.firmware_version || "unknown",
      deviceState: endpointStatus?.device_state || "unknown",
      lastSeenAt: endpointStatus?.last_seen_at || "none",
      connectionState: voiceStatus?.connection_state || "offline",
      transportHealth: voiceStatus?.transport_health || "offline",
      sessionId: session?.session_id || "none",
      backendState: session?.session_state || "idle",
      uxState: session?.ux_state || "idle",
    },
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
          <thead>
            <tr>
              <th scope="col">Endpoint</th>
              <th scope="col">FW</th>
              <th scope="col">Device</th>
              <th scope="col">Last heartbeat</th>
              <th scope="col">Connection</th>
              <th scope="col">Transport</th>
              <th scope="col">Session</th>
              <th scope="col">Backend</th>
              <th scope="col">UX</th>
            </tr>
          </thead>
          <tbody>
            {endpointRows.map((row) => (
              <tr key={row.endpointId}>
                <th scope="row">{valueOrEmpty(row.endpointId)}</th>
                <td>{valueOrEmpty(row.firmwareVersion)}</td>
                <td>{valueOrEmpty(row.deviceState)}</td>
                <td>{valueOrEmpty(row.lastSeenAt)}</td>
                <td>{valueOrEmpty(row.connectionState)}</td>
                <td>{valueOrEmpty(row.transportHealth)}</td>
                <td>{valueOrEmpty(row.sessionId)}</td>
                <td>{valueOrEmpty(row.backendState)}</td>
                <td>{valueOrEmpty(row.uxState)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function VoiceEndpointDashboardSection({
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
        voiceStatus={voiceStatus}
        endpointStatus={endpointStatus}
      />
    </section>
  );
}
