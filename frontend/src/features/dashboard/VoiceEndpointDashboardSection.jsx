import { useEffect, useState } from "react";
import {
  cancelEndpointSession,
  getEndpointVolume,
  muteEndpoint,
  replayEndpointResponse,
  setEndpointVolume,
  testAssistantTurn,
  updateEndpointMetadata,
} from "../../api/client";
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

function voiceStateProjection(voiceStatus) {
  return voiceStatus?.state_projection || {
    connection_state: voiceStatus?.connection_state || "offline",
    ux_state: voiceStatus?.ux_state || voiceStatus?.active_session?.ux_state || "idle",
    session_state: voiceStatus?.session_state || voiceStatus?.active_session?.session_state || "none",
    transport_health: voiceStatus?.transport_health || "offline",
  };
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
  const assistant = voiceStatus?.last_assistant || {};

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
          <dt>Assistant</dt>
          <dd>{valueOrEmpty(assistant.provider_id)}</dd>
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
          <dd>{valueOrEmpty(assistant.error || voiceStatus?.last_error?.code, "clear")}</dd>
        </div>
      </dl>
    </section>
  );
}

function EndpointStatusTable({ voiceStatus, endpointStatus }) {
  const session = voiceStatus?.active_session;
  const projection = voiceStateProjection(voiceStatus);
  const timings = voiceStatus?.last_turn_timings || {};
  const endpointRows = [
    {
      health: endpointHealth(voiceStatus),
      endpointId: endpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "not connected",
      displayName: endpointStatus?.display_name || "none",
      zoneId: endpointStatus?.zone_id || "none",
      firmwareVersion: endpointStatus?.firmware_version || "unknown",
      deviceState: endpointStatus?.device_state || "unknown",
      connectionState: endpointStatus?.connection_state || "unknown",
      lastSeenAt: formatLocalDateTime(endpointStatus?.last_seen_at),
      voiceConnection: projection.connection_state,
      uxState: projection.ux_state,
      sessionState: projection.session_state || "none",
      transportHealth: projection.transport_health,
      sessionId: session?.session_id || "none",
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
              <th className="endpoint-health-column" scope="col" aria-label="Endpoint health" />
              <th scope="col">Endpoint</th>
              <th scope="col">Name</th>
              <th scope="col">Zone</th>
              <th scope="col">FW</th>
              <th scope="col">Device</th>
              <th scope="col">Registry</th>
              <th scope="col">Last heartbeat</th>
              <th scope="col">Connection</th>
              <th scope="col">UX</th>
              <th scope="col">Session state</th>
              <th scope="col">Transport</th>
              <th scope="col">STT</th>
              <th scope="col">Total</th>
              <th scope="col">Session</th>
            </tr>
          </thead>
          <tbody>
            {endpointRows.map((row) => (
              <tr key={row.endpointId}>
                <td className="endpoint-health-column">
                  <span className={`endpoint-health-led endpoint-health-led-${row.health}`} aria-label={`${row.health} endpoint health`} />
                </td>
                <th scope="row">{valueOrEmpty(row.endpointId)}</th>
                <td>{valueOrEmpty(row.displayName)}</td>
                <td>{valueOrEmpty(row.zoneId)}</td>
                <td>{valueOrEmpty(row.firmwareVersion)}</td>
                <td>{valueOrEmpty(row.deviceState)}</td>
                <td>{valueOrEmpty(row.connectionState)}</td>
                <td>{valueOrEmpty(row.lastSeenAt)}</td>
                <td>{valueOrEmpty(row.voiceConnection)}</td>
                <td>{valueOrEmpty(row.uxState)}</td>
                <td>{valueOrEmpty(row.sessionState)}</td>
                <td>{valueOrEmpty(row.transportHealth)}</td>
                <td>{valueOrEmpty(row.sttLatency)}</td>
                <td>{valueOrEmpty(row.totalLatency)}</td>
                <td>{valueOrEmpty(row.sessionId)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function EndpointMetadataPanel({ endpointStatus, voiceStatus, onRefresh, setActionMessage }) {
  const endpointId = endpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "";
  const [displayName, setDisplayName] = useState(endpointStatus?.display_name || "");
  const [zoneId, setZoneId] = useState(endpointStatus?.zone_id || "");

  useEffect(() => {
    setDisplayName(endpointStatus?.display_name || "");
    setZoneId(endpointStatus?.zone_id || "");
  }, [endpointStatus?.display_name, endpointStatus?.zone_id, endpointStatus?.endpoint_id]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!endpointId) {
      setActionMessage("Metadata skipped: endpoint is not registered.");
      return;
    }

    try {
      const result = await updateEndpointMetadata(endpointId, {
        display_name: displayName,
        zone_id: zoneId,
      });
      setActionMessage(`Saved ${result.display_name || result.endpoint_id}.`);
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  return (
    <section className="voice-endpoint-panel stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoint Registry</p>
          <h2 className="panel-title">Operator Metadata</h2>
        </div>
        <span className="status-pill status-pill-neutral">{valueOrEmpty(endpointStatus?.connection_state, "unregistered")}</span>
      </div>
      <form className="endpoint-metadata-form" onSubmit={handleSubmit}>
        <label>
          <span>Display name</span>
          <input
            type="text"
            value={displayName}
            maxLength={80}
            onChange={(event) => setDisplayName(event.target.value)}
            disabled={!endpointId}
          />
        </label>
        <label>
          <span>Zone</span>
          <input
            type="text"
            value={zoneId}
            maxLength={80}
            onChange={(event) => setZoneId(event.target.value)}
            disabled={!endpointId}
          />
        </label>
        <button className="btn btn-secondary" type="submit" disabled={!endpointId}>
          Save Metadata
        </button>
      </form>
    </section>
  );
}

export function VoiceEndpointDashboardSection({
  voiceStatus,
  endpointStatus,
  onRefresh,
}) {
  const [actionMessage, setActionMessage] = useState("");
  const [volumePercent, setVolumePercent] = useState(70);
  const [muted, setMuted] = useState(false);
  const projection = voiceStateProjection(voiceStatus);
  const endpointId = endpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "";

  useEffect(() => {
    if (!endpointId) {
      return undefined;
    }

    let active = true;
    getEndpointVolume(endpointId)
      .then((result) => {
        if (active && typeof result.volume_percent === "number") {
          setVolumePercent(result.volume_percent);
        }
      })
      .catch(() => {
        // Dashboard refresh still works if the endpoint has not reported volume yet.
      });

    return () => {
      active = false;
    };
  }, [endpointId]);

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
      if (!endpointId) {
        setActionMessage("Stop skipped: endpoint is not connected.");
        return;
      }
      const result = await cancelEndpointSession(endpointId);
      setActionMessage(result.accepted ? `Stop sent (${result.status}, ${result.request_id}).` : `Stop skipped: ${result.reason}`);
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  async function handleSetVolume() {
    try {
      if (!endpointId) {
        setActionMessage("Volume skipped: endpoint is not connected.");
        return;
      }
      const result = await setEndpointVolume(endpointId, Number(volumePercent));
      setActionMessage(
        result.accepted
          ? `Volume ${result.volume_percent}% sent (${result.status}, ${result.request_id}).`
          : `Volume skipped: ${result.reason}`,
      );
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  async function handleMuteEndpoint() {
    try {
      if (!endpointId) {
        setActionMessage("Mute skipped: endpoint is not connected.");
        return;
      }
      const nextMuted = !muted;
      const result = await muteEndpoint(endpointId, nextMuted);
      if (result.accepted) {
        setMuted(nextMuted);
      }
      setActionMessage(
        result.accepted
          ? `${nextMuted ? "Mute" : "Unmute"} sent (${result.status}, ${result.request_id}).`
          : `Mute skipped: ${result.reason}`,
      );
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  async function handleReplayResponse() {
    try {
      if (!endpointId) {
        setActionMessage("Replay skipped: endpoint is not connected.");
        return;
      }
      const result = await replayEndpointResponse(endpointId);
      setActionMessage(result.accepted ? `Replay sent (${result.status}, ${result.request_id}).` : `Replay skipped: ${result.reason}`);
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
          onReplayResponse={handleReplayResponse}
          onMuteEndpoint={handleMuteEndpoint}
          onSetVolume={handleSetVolume}
          volumePercent={volumePercent}
          onVolumeChange={setVolumePercent}
          muted={muted}
          actionMessage={actionMessage}
        />
      </div>
      <section className="voice-endpoint-panel stack">
        <div className="section-heading">
          <div>
            <p className="panel-kicker">State Families</p>
            <h2 className="panel-title">Connection, UX, Session</h2>
          </div>
          <span className="status-pill status-pill-neutral">{valueOrEmpty(projection.transport_health, "offline")}</span>
        </div>
        <dl className="facts">
          <div>
            <dt>Connection</dt>
            <dd>{valueOrEmpty(projection.connection_state, "offline")}</dd>
          </div>
          <div>
            <dt>UX</dt>
            <dd>{valueOrEmpty(projection.ux_state, "idle")}</dd>
          </div>
          <div>
            <dt>Session</dt>
            <dd>{valueOrEmpty(projection.session_state, "none")}</dd>
          </div>
          <div>
            <dt>Transport</dt>
            <dd>{valueOrEmpty(projection.transport_health, "offline")}</dd>
          </div>
        </dl>
      </section>
      <EndpointStatusTable
        voiceStatus={voiceStatus}
        endpointStatus={endpointStatus}
      />
      <EndpointMetadataPanel
        voiceStatus={voiceStatus}
        endpointStatus={endpointStatus}
        onRefresh={onRefresh}
        setActionMessage={setActionMessage}
      />
    </section>
  );
}
