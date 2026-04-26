import { useEffect, useState } from "react";
import { cancelVoiceSession, testAssistantTurn } from "../../api/client";
import { VoiceEndpointActionsCard } from "./cards/VoiceEndpointActionsCard";

const LATEST_SPEECH_VISIBLE_MS = 20000;

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

function formatMs(value) {
  if (typeof value !== "number") {
    return "none";
  }
  return `${Math.round(value)} ms`;
}

function endpointHealth(voiceStatus) {
  const connected = voiceStatus?.connection_state === "connected";
  const online = voiceStatus?.transport_health === "online";
  if (connected && online) {
    return "green";
  }
  if (connected || online) {
    return "yellow";
  }
  return "red";
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

  const timings = voiceStatus?.last_turn_timings || {};

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
          <dt>STT latency</dt>
          <dd>{formatMs(timings.stt_ms)}</dd>
        </div>
        <div>
          <dt>Assistant latency</dt>
          <dd>{formatMs(timings.assistant_ms)}</dd>
        </div>
        <div>
          <dt>TTS latency</dt>
          <dd>{formatMs(timings.tts_ms)}</dd>
        </div>
        <div>
          <dt>Total latency</dt>
          <dd>{formatMs(timings.total_ms)}</dd>
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
  const timings = voiceStatus?.last_turn_timings || {};
  const endpointRows = [
    {
      health: endpointHealth(voiceStatus),
      endpointId: endpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "not connected",
      firmwareVersion: endpointStatus?.firmware_version || "unknown",
      deviceState: endpointStatus?.device_state || "unknown",
      lastSeenAt: formatLocalDateTime(endpointStatus?.last_seen_at),
      transportHealth: voiceStatus?.transport_health || "offline",
      sessionId: session?.session_id || "none",
      backendState: session?.session_state || "idle",
      uxState: session?.ux_state || "idle",
      sttLatency: formatMs(timings.stt_ms),
      totalLatency: formatMs(timings.total_ms),
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
              <th scope="col">Status</th>
              <th scope="col">Endpoint</th>
              <th scope="col">FW</th>
              <th scope="col">Device</th>
              <th scope="col">Last heartbeat</th>
              <th scope="col">Transport</th>
              <th scope="col">STT</th>
              <th scope="col">Total</th>
              <th scope="col">Session</th>
              <th scope="col">Backend</th>
              <th scope="col">UX</th>
            </tr>
          </thead>
          <tbody>
            {endpointRows.map((row) => (
              <tr key={row.endpointId}>
                <td>
                  <span className={`endpoint-health-led endpoint-health-led-${row.health}`} aria-label={`${row.health} endpoint health`} />
                </td>
                <th scope="row">{valueOrEmpty(row.endpointId)}</th>
                <td>{valueOrEmpty(row.firmwareVersion)}</td>
                <td>{valueOrEmpty(row.deviceState)}</td>
                <td>{valueOrEmpty(row.lastSeenAt)}</td>
                <td>{valueOrEmpty(row.transportHealth)}</td>
                <td>{valueOrEmpty(row.sttLatency)}</td>
                <td>{valueOrEmpty(row.totalLatency)}</td>
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
