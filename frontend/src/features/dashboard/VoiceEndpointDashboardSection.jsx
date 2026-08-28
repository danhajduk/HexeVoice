import { useEffect, useState } from "react";
import {
  applyEndpointProvisioning,
  cancelEndpointSession,
  deleteEndpointMedia,
  deleteEndpointVoiceArtifacts,
  deleteVoiceTtsArtifact,
  deleteWakeRecording,
  deliverEndpointMedia,
  getEndpointMediaAssets,
  getEndpointMediaInventory,
  getEndpointVolume,
  getVoiceSession,
  getVoiceSessions,
  muteEndpoint,
  pushFirmwareOta,
  reformatEndpointStorage,
  resetEndpointProvisioning,
  replayEndpointResponse,
  replayVoiceSession,
  setEndpointVolume,
  testAssistantTurn,
  uploadEndpointMedia,
  updateEndpointMetadata,
  wakeRecordingAudioUrl,
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
  if (value >= 60000) {
    return `${Math.round(value / 60000)} min`;
  }
  if (value >= 10000) {
    return `${Math.round(value / 1000)} sec`;
  }
  return `${Math.round(value)} ms`;
}

function formatPercent(value) {
  if (typeof value !== "number") {
    return "none";
  }
  return `${Math.round(value * 100)}%`;
}

function formatNumber(value, digits = 3) {
  if (typeof value !== "number") {
    return "none";
  }
  return value.toFixed(digits);
}

function formatYesNo(value) {
  if (typeof value !== "boolean") {
    return "unknown";
  }
  return value ? "yes" : "no";
}

function labelizeState(value, fallback = "none") {
  if (!value) {
    return fallback;
  }
  return String(value)
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function uxStateLabel(value) {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "wake_armed") {
    return "Wake word armed";
  }
  if (normalized === "listening" || normalized === "capturing" || normalized === "recording") {
    return "Recording speech";
  }
  if (normalized === "speaking" || normalized === "playback") {
    return "Speaking";
  }
  return labelizeState(value, "Idle");
}

function sessionStateLabel(value) {
  const normalized = String(value || "").toLowerCase();
  if (!normalized || normalized === "none") {
    return "No session";
  }
  if (normalized === "idle") {
    return "Idle";
  }
  if (normalized === "capturing" || normalized === "listening" || normalized === "recording") {
    return "Recording";
  }
  return labelizeState(value);
}

function endpointCapabilities(endpointStatus) {
  return endpointStatus?.capabilities && typeof endpointStatus.capabilities === "object"
    ? endpointStatus.capabilities
    : {};
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      resolve(result.includes(",") ? result.split(",").pop() : result);
    };
    reader.onerror = () => reject(reader.error || new Error("file read failed"));
    reader.readAsDataURL(file);
  });
}

function endpointDisplayName(endpointStatus) {
  const provisioning = endpointCapabilities(endpointStatus).provisioning || {};
  return endpointStatus?.display_name || provisioning.display_name || "none";
}

function endpointHardwareId(endpointStatus) {
  const identity = endpointCapabilities(endpointStatus).identity || {};
  return endpointStatus?.hardware_id || identity.hardware_id || "unknown";
}

function endpointHealthForProjection(projection, endpointStatus) {
  const connected = projection.connection_state === "connected";
  const online = projection.transport_health === "online" || endpointStatus?.connection_state === "online";
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

function voiceStateProjectionForEndpoint(voiceStatus, endpointId) {
  const endpointVoice = endpointId ? voiceStatus?.endpoints?.[endpointId] : null;
  if (endpointVoice) {
    return {
      connection_state: endpointVoice.connection_state || "offline",
      ux_state: endpointVoice.ux_state || endpointVoice.active_session?.ux_state || "idle",
      session_state: endpointVoice.session_state || endpointVoice.active_session?.session_state || "none",
      transport_health: endpointVoice.transport_health || "offline",
      active_session: endpointVoice.active_session || null,
    };
  }
  return {
    ...voiceStateProjection(voiceStatus),
    active_session: voiceStatus?.active_session || null,
  };
}

function endpointStatusesFromRegistry(endpointStatus, endpointRegistry) {
  if (Array.isArray(endpointRegistry?.endpoints) && endpointRegistry.endpoints.length) {
    return endpointRegistry.endpoints;
  }
  return endpointStatus ? [endpointStatus] : [];
}

function endpointStatusById(endpointStatuses, endpointId) {
  return endpointStatuses.find((status) => status?.endpoint_id === endpointId) || endpointStatuses[0] || null;
}

function selectedVoiceStatus(voiceStatus, endpointId) {
  const projection = voiceStateProjectionForEndpoint(voiceStatus, endpointId);
  const endpointVoice = endpointId ? voiceStatus?.endpoints?.[endpointId] : null;
  return {
    ...voiceStatus,
    endpoint_id: endpointId || voiceStatus?.endpoint_id,
    connection_state: projection.connection_state,
    transport_health: projection.transport_health,
    ux_state: projection.ux_state,
    session_state: projection.session_state,
    active_session: projection.active_session,
    state_projection: projection,
    commands: endpointVoice?.commands || voiceStatus?.commands || [],
    last_event_type: endpointVoice?.last_event_type || voiceStatus?.last_event_type,
  };
}

function endpointBoardProfile(endpointStatus) {
  if (!endpointStatus) {
    return "unknown";
  }
  const firmware = endpointCapabilities(endpointStatus).firmware || {};
  if (firmware.board_profile || firmware.profile) {
    return firmware.board_profile || firmware.profile;
  }
  const endpointId = String(endpointStatus?.endpoint_id || "").toLowerCase();
  return endpointId.includes("pe") ? "ha_voice_pe" : "esp_box_3";
}

function firmwareUpdateLabel(update) {
  if (!update) {
    return "unknown";
  }
  if (update.update_available) {
    return "Update ready";
  }
  return labelizeState(update.reason, "current");
}

function audioQualityTone(audioQuality) {
  const status = String(audioQuality?.status || "").toLowerCase();
  if (!status) {
    return "neutral";
  }
  if (status === "ok") {
    return "success";
  }
  if (status === "clipped" || status === "silent" || status === "unsupported_audio") {
    return "danger";
  }
  return "warning";
}

function audioQualitySummary(audioQuality) {
  if (!audioQuality) {
    return "none";
  }
  const warnings = Array.isArray(audioQuality.warnings) ? audioQuality.warnings.filter(Boolean) : [];
  return warnings.length ? `${audioQuality.status}: ${warnings.join(", ")}` : String(audioQuality.status || "unknown");
}

function audioQualityCompactSummary(audioQuality) {
  if (!audioQuality) {
    return "";
  }
  const warnings = Array.isArray(audioQuality.warnings) ? audioQuality.warnings.filter(Boolean) : [];
  if (!warnings.length) {
    return "";
  }
  return warnings.length === 1 ? warnings[0] : `${warnings.length} warnings`;
}

function audioQualityWarnings(audioQuality) {
  return new Set(Array.isArray(audioQuality?.warnings) ? audioQuality.warnings.filter(Boolean) : []);
}

function audioQualityStatusTone(status) {
  const normalized = String(status || "").toLowerCase();
  if (!normalized) {
    return "neutral";
  }
  if (normalized === "ok") {
    return "success";
  }
  if (normalized === "silent" || normalized === "clipped" || normalized === "missing_audio" || normalized === "unsupported_audio") {
    return "danger";
  }
  return "warning";
}

function audioQualityDetailRows(audioQuality) {
  if (!audioQuality) {
    return [];
  }
  const warnings = audioQualityWarnings(audioQuality);
  const warningTone = warnings.size
    ? (warnings.has("silent") || warnings.has("clipped") || warnings.has("missing_audio") || warnings.has("unsupported_audio") ? "danger" : "warning")
    : "success";
  const clippingRatio = typeof audioQuality.clipping_ratio === "number" ? audioQuality.clipping_ratio : null;
  const activeRatio = typeof audioQuality.active_audio_ratio === "number" ? audioQuality.active_audio_ratio : null;
  const silenceRatio = typeof audioQuality.silence_ratio === "number" ? audioQuality.silence_ratio : null;
  const rms = typeof audioQuality.rms === "number" ? audioQuality.rms : null;
  const peak = typeof audioQuality.peak === "number" ? audioQuality.peak : null;
  const speechRms = typeof audioQuality.speech_rms === "number" ? audioQuality.speech_rms : null;
  const source = String(audioQuality.source || "backend").toLowerCase();
  return [
    {
      label: "Source",
      value: source === "endpoint" ? "endpoint metrics" : "backend analysis",
      range: "endpoint preferred when available",
      tone: source === "endpoint" ? "success" : "neutral",
    },
    {
      label: "Status",
      value: audioQuality.status,
      range: "ok preferred",
      tone: audioQualityStatusTone(audioQuality.status),
    },
    {
      label: "Warnings",
      value: Array.isArray(audioQuality.warnings) && audioQuality.warnings.length ? audioQuality.warnings.join(", ") : "none",
      range: "none preferred",
      tone: warningTone,
    },
    {
      label: "Duration",
      value: formatMs(audioQuality.duration_ms),
      range: ">= 300 ms",
      tone: typeof audioQuality.duration_ms === "number" && audioQuality.duration_ms < 300 ? "warning" : "success",
    },
    {
      label: "RMS",
      value: formatNumber(audioQuality.rms),
      range: ">= 0.015, silent <= 0.0005",
      tone: rms === null ? "neutral" : rms <= 0.0005 ? "danger" : rms < 0.015 ? "warning" : "success",
    },
    {
      label: "Peak",
      value: formatNumber(audioQuality.peak),
      range: "0.001 to < 0.999",
      tone: peak === null ? "neutral" : peak <= 0.001 || peak >= 0.999 ? "danger" : "success",
    },
    {
      label: "Clipping",
      value: `${valueOrEmpty(audioQuality.clipping_count, 0)} / ${formatPercent(audioQuality.clipping_ratio)}`,
      range: "< 1%",
      tone: clippingRatio === null ? "neutral" : clippingRatio >= 0.01 ? "danger" : clippingRatio > 0 ? "warning" : "success",
    },
    {
      label: "Active audio",
      value: formatPercent(audioQuality.active_audio_ratio),
      range: "> 10% useful",
      tone: activeRatio === null ? "neutral" : activeRatio <= 0 ? "danger" : activeRatio < 0.1 ? "warning" : "success",
    },
    {
      label: "Silence",
      value: formatPercent(audioQuality.silence_ratio),
      range: "< 90%",
      tone: silenceRatio === null ? "neutral" : silenceRatio >= 1 ? "danger" : silenceRatio > 0.9 ? "warning" : "success",
    },
    {
      label: "Speech RMS",
      value: formatNumber(audioQuality.speech_rms),
      range: ">= 0.015",
      tone: speechRms === null ? "neutral" : speechRms < 0.015 ? "warning" : "success",
    },
  ];
}

const AUDIO_QUALITY_LEGEND = [
  ["Source", "Whether the ambient/SNR values came from endpoint-reported numeric metrics or backend analysis of transient pre-roll audio."],
  ["Status", "Overall result from the current checks. Ok means no warning threshold was crossed."],
  ["Warnings", "Specific thresholds that fired, such as low level, clipping, silence, or a very short clip."],
  ["Duration", "Length of the captured speech audio that was analyzed."],
  ["RMS", "Average signal level for the whole clip. Very low values usually mean quiet speech or silence."],
  ["Peak", "Loudest sample level in the clip. Values near 1.0 are close to the digital maximum."],
  ["Clipping", "Number and percentage of samples at the digital limit, which can sound distorted."],
  ["Active audio", "Percent of samples above the activity threshold. Higher usually means more speech-like audio."],
  ["Silence", "Percent of samples below the activity threshold."],
  ["Speech RMS", "Average level for only the active parts of the clip."],
];

function AudioQualityBadge({ audioQuality }) {
  return (
    <span className={`status-pill status-pill-${audioQualityTone(audioQuality)}`}>
      {audioQuality?.status || "no quality"}
    </span>
  );
}

function AudioQualityButton({ audioQuality, onOpen }) {
  if (!audioQuality) {
    return null;
  }
  return (
    <button
      className={`status-pill status-pill-${audioQualityTone(audioQuality)} status-pill-button`}
      type="button"
      title="Show audio quality details"
      onClick={(event) => {
        event.stopPropagation();
        onOpen?.();
      }}
      onKeyDown={(event) => event.stopPropagation()}
    >
      {audioQuality.status || "audio quality"}
    </button>
  );
}

function AudioQualityFacts({ audioQuality }) {
  const rows = audioQualityDetailRows(audioQuality);
  if (!rows.length) {
    return <div className="callout callout-neutral">No audio-quality metrics were recorded for this turn.</div>;
  }
  return (
    <dl className="fact-grid audio-quality-fact-grid">
      {rows.map((row) => (
        <div className={`fact-grid-item audio-quality-metric audio-quality-metric-${row.tone}`} key={row.label}>
          <dt className="fact-grid-label">{row.label}</dt>
          <dd className="fact-grid-value audio-quality-metric-value">{valueOrEmpty(row.value)}</dd>
          <dd className="audio-quality-metric-range">{row.range}</dd>
        </div>
      ))}
    </dl>
  );
}

function AudioQualityLegend() {
  return (
    <details className="audio-quality-legend">
      <summary>Metric legend</summary>
      <dl>
        {AUDIO_QUALITY_LEGEND.map(([label, description]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{description}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function AudioQualityDetailPopout({ audioQuality, onClose }) {
  if (!audioQuality) {
    return null;
  }
  return (
    <div className="voice-history-popout-backdrop" role="presentation" onClick={onClose}>
      <section
        className="voice-history-popout audio-quality-popout"
        role="dialog"
        aria-modal="true"
        aria-label="Audio quality details"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="section-heading">
          <div>
            <p className="panel-kicker">Track 1</p>
            <h2 className="panel-title">Audio Quality</h2>
          </div>
          <button className="btn btn-ghost btn-compact" type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="audio-quality-popout-summary">
          <AudioQualityBadge audioQuality={audioQuality} />
          <span>{audioQualitySummary(audioQuality)}</span>
        </div>
        <AudioQualityFacts audioQuality={audioQuality} />
        <AudioQualityLegend />
      </section>
    </div>
  );
}

function VoicePipelinePanel({ voiceStatus, latestSession }) {
  const [visibleTranscript, setVisibleTranscript] = useState("");
  const [audioQualityDetailOpen, setAudioQualityDetailOpen] = useState(false);

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
  const audioQuality = latestSession?.transcript?.audio_quality || voiceStatus?.last_transcript_metadata?.audio_quality || null;
  const audioQualityNote = audioQualityCompactSummary(audioQuality);

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
          <dd>{assistant.fallback_used ? `${valueOrEmpty(assistant.provider_id)} fallback` : valueOrEmpty(assistant.provider_id)}</dd>
        </div>
        <div>
          <dt>Assistant detail</dt>
          <dd>{assistant.fallback_used ? valueOrEmpty(assistant.fallback_reason) : valueOrEmpty(assistant.model, "ready")}</dd>
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
        {audioQuality ? (
          <div>
            <dt>Audio quality</dt>
            <dd className="audio-quality-inline">
              <AudioQualityButton audioQuality={audioQuality} onOpen={() => setAudioQualityDetailOpen(true)} />
              {audioQualityNote ? <span>{audioQualityNote}</span> : null}
            </dd>
          </div>
        ) : null}
        <div>
          <dt>Last error</dt>
          <dd>{valueOrEmpty(assistant.error || voiceStatus?.last_error?.code, "clear")}</dd>
        </div>
      </dl>
      <AudioQualityDetailPopout
        audioQuality={audioQualityDetailOpen ? audioQuality : null}
        onClose={() => setAudioQualityDetailOpen(false)}
      />
    </section>
  );
}

function EndpointStatusTable({
  voiceStatus,
  endpointStatus,
  endpointRegistry,
  selectedEndpointId,
  onSelectEndpoint,
}) {
  const timings = voiceStatus?.last_turn_timings || {};
  const endpointStatuses = endpointStatusesFromRegistry(endpointStatus, endpointRegistry);
  const endpointRows = (endpointStatuses.length ? endpointStatuses : [null]).map((currentEndpointStatus) => {
    const endpointId = currentEndpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "not connected";
    const projection = voiceStateProjectionForEndpoint(voiceStatus, endpointId);
    const storage = endpointCapabilities(currentEndpointStatus).storage || {};
    const output = endpointCapabilities(currentEndpointStatus).audio?.output || {};
    const session = projection.active_session || (
      voiceStatus?.active_session?.endpoint_id === endpointId ? voiceStatus.active_session : null
    );
    const endpointVoice = endpointId ? voiceStatus?.endpoints?.[endpointId] : null;
    return {
      health: endpointHealthForProjection(projection, currentEndpointStatus),
      endpointId,
      displayName: endpointDisplayName(currentEndpointStatus),
      zoneId: currentEndpointStatus?.zone_id || "none",
      firmwareVersion: currentEndpointStatus?.firmware_version || "unknown",
      boardProfile: endpointBoardProfile(currentEndpointStatus),
      hardwareId: endpointHardwareId(currentEndpointStatus),
      deviceState: labelizeState(currentEndpointStatus?.device_state, "Unknown"),
      connectionState: labelizeState(currentEndpointStatus?.connection_state, "Unknown"),
      fileTransfer: storage.media_transfer_active ? "downloading file" : storage.media_transfer_status || "idle",
      volume: typeof output.volume_percent === "number" ? `${output.volume_percent}%` : "unknown",
      muted: typeof output.muted === "boolean" ? (output.muted ? "Muted" : "Live") : "Unknown",
      lastSeenAt: formatLocalDateTime(currentEndpointStatus?.last_seen_at),
      ipAddress: currentEndpointStatus?.ip_address || "unknown",
      rssi: typeof currentEndpointStatus?.rssi_dbm === "number" ? `${currentEndpointStatus.rssi_dbm} dBm` : "unknown",
      voiceConnection: labelizeState(projection.connection_state, "Offline"),
      uxState: uxStateLabel(projection.ux_state),
      sessionState: sessionStateLabel(projection.session_state),
      transportHealth: labelizeState(projection.transport_health, "Offline"),
      sessionId: session?.session_id || "none",
      sttLatency: formatMs(timings.stt_ms),
      totalLatency: formatMs(timings.total_ms),
      firmwareUpdate: currentEndpointStatus?.firmware_update || {},
      raw: {
        endpointStatus: currentEndpointStatus,
        voiceStatus: {
          endpoint_id: endpointId,
          endpoint_voice: endpointVoice,
          connection_state: projection.connection_state,
          transport_health: projection.transport_health,
          active_session: session,
          state_projection: projection,
          last_turn_timings: voiceStatus?.last_turn_timings,
          last_event_type: endpointVoice?.last_event_type || voiceStatus?.last_event_type,
          last_error: voiceStatus?.last_error,
        },
      },
    };
  });
  const selectedEndpoint = endpointRows.find((row) => row.endpointId === selectedEndpointId) || endpointRows[0] || null;
  const selectedDetailRows = selectedEndpoint
    ? [
        ["IP address", selectedEndpoint.ipAddress],
        ["Signal", selectedEndpoint.rssi],
        ["Hardware ID", selectedEndpoint.hardwareId],
        ["Board", selectedEndpoint.boardProfile],
        ["Firmware update", firmwareUpdateLabel(selectedEndpoint.firmwareUpdate)],
        ["Last heartbeat", selectedEndpoint.lastSeenAt],
        ["Transport", selectedEndpoint.transportHealth],
      ]
    : [];

  return (
    <section className="voice-endpoint-panel stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoint Status</p>
          <h2 className="panel-title">Selected Endpoint</h2>
        </div>
        <span className="status-pill status-pill-neutral">{`${endpointStatuses.length} endpoint${endpointStatuses.length === 1 ? "" : "s"}`}</span>
      </div>
      {selectedEndpoint ? (
        <div className="selected-endpoint-summary">
          <div>
            <span className={`endpoint-health-led endpoint-health-led-${selectedEndpoint.health}`} />
            <strong>{valueOrEmpty(selectedEndpoint.displayName, selectedEndpoint.endpointId)}</strong>
            <span>{valueOrEmpty(selectedEndpoint.endpointId)}</span>
          </div>
          <span>{selectedEndpoint.connectionState}</span>
          <span>{selectedEndpoint.sessionState}</span>
          <span>{selectedEndpoint.uxState}</span>
          <span>{`${selectedEndpoint.volume} / ${selectedEndpoint.muted}`}</span>
          <span>{selectedEndpoint.rssi}</span>
        </div>
      ) : null}
      <div className="endpoint-overview-layout">
        <div className="endpoint-card-grid">
          {endpointRows.map((row) => (
            <button
              key={row.endpointId}
              className={`endpoint-status-card${row.endpointId === selectedEndpoint?.endpointId ? " endpoint-status-card-selected" : ""}`}
              type="button"
              aria-pressed={row.endpointId === selectedEndpoint?.endpointId}
              onClick={() => onSelectEndpoint?.(row.endpointId)}
            >
              <div className="endpoint-status-card-header">
                <span className={`endpoint-health-led endpoint-health-led-${row.health}`} aria-label={`${row.health} endpoint health`} />
                <div className="endpoint-status-card-title-block">
                  <h3>{valueOrEmpty(row.displayName, row.endpointId)}</h3>
                  <span>{valueOrEmpty(row.endpointId)}</span>
                </div>
                <span className={`status-pill ${row.firmwareUpdate?.update_available ? "status-pill-warning" : "status-pill-neutral"}`}>
                  {firmwareUpdateLabel(row.firmwareUpdate)}
                </span>
              </div>
              <div className="endpoint-status-card-facts">
                <span>
                  <strong>State</strong>
                  {valueOrEmpty(row.deviceState)}
                </span>
                <span>
                  <strong>Voice</strong>
                  {valueOrEmpty(row.voiceConnection)}
                </span>
                <span>
                  <strong>UX</strong>
                  {valueOrEmpty(row.uxState)}
                </span>
                <span>
                  <strong>Board</strong>
                  {valueOrEmpty(row.boardProfile)}
                </span>
                <span>
                  <strong>Volume</strong>
                  {`${valueOrEmpty(row.volume)} / ${valueOrEmpty(row.muted)}`}
                </span>
              </div>
              <div className="endpoint-status-card-footer">
                <span>FW {valueOrEmpty(row.firmwareVersion)}</span>
                <span>{valueOrEmpty(row.lastSeenAt)}</span>
              </div>
            </button>
          ))}
        </div>
        {selectedEndpoint ? (
          <section className="endpoint-detail-inline stack" aria-label={`${selectedEndpoint.endpointId} endpoint details`}>
            <div className="section-heading">
              <div>
                <p className="panel-kicker">Endpoint Detail</p>
                <h2 className="panel-title">{valueOrEmpty(selectedEndpoint.displayName, selectedEndpoint.endpointId)}</h2>
              </div>
              <span className="status-pill status-pill-neutral">{valueOrEmpty(selectedEndpoint.connectionState)}</span>
            </div>
            <div className="endpoint-detail-summary">
              <span className={`endpoint-health-led endpoint-health-led-${selectedEndpoint.health}`} />
              <span className="status-pill status-pill-neutral">{valueOrEmpty(selectedEndpoint.connectionState)}</span>
              <span className="status-pill status-pill-neutral">{valueOrEmpty(selectedEndpoint.voiceConnection)}</span>
              {selectedEndpoint.firmwareUpdate?.update_available ? (
                <span className="status-pill status-pill-warning">Update ready</span>
              ) : null}
            </div>
            <dl className="fact-grid endpoint-detail-grid">
              {selectedDetailRows.map(([label, value]) => (
                <div className="fact-grid-item" key={label}>
                  <dt className="fact-grid-label">{label}</dt>
                  <dd className="fact-grid-value">{valueOrEmpty(value)}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}
      </div>
    </section>
  );
}

function EndpointMetadataPanel({ endpointStatus, voiceStatus, onRefresh, setActionMessage }) {
  const endpointId = endpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "";
  const [displayName, setDisplayName] = useState(endpointStatus?.display_name || "");
  const [zoneId, setZoneId] = useState(endpointStatus?.zone_id || "");
  const [audienceMode, setAudienceMode] = useState(endpointStatus?.audience_mode || "general");
  const [adultOverrideEnabled, setAdultOverrideEnabled] = useState(Boolean(endpointStatus?.adult_override_enabled));

  useEffect(() => {
    setDisplayName(endpointStatus?.display_name || "");
    setZoneId(endpointStatus?.zone_id || "");
    setAudienceMode(endpointStatus?.audience_mode || "general");
    setAdultOverrideEnabled(Boolean(endpointStatus?.adult_override_enabled));
  }, [
    endpointStatus?.display_name,
    endpointStatus?.zone_id,
    endpointStatus?.audience_mode,
    endpointStatus?.adult_override_enabled,
    endpointStatus?.endpoint_id,
  ]);

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
        audience_mode: audienceMode,
        adult_override_enabled: adultOverrideEnabled,
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
        <label>
          <span>Audience mode</span>
          <select
            value={audienceMode}
            onChange={(event) => {
              setAudienceMode(event.target.value);
              if (event.target.value === "general" || event.target.value === "adult_unrestricted") {
                setAdultOverrideEnabled(false);
              }
            }}
            disabled={!endpointId}
          >
            <option value="general">General</option>
            <option value="child_safe">Child safe</option>
            <option value="teen_safe">Teen safe</option>
            <option value="adult_unrestricted">Adult unrestricted</option>
          </select>
        </label>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={adultOverrideEnabled}
            onChange={(event) => setAdultOverrideEnabled(event.target.checked)}
            disabled={!endpointId || audienceMode === "general" || audienceMode === "adult_unrestricted"}
          />
          Adult/admin override
        </label>
        <button className="btn btn-secondary" type="submit" disabled={!endpointId}>
          Save Metadata
        </button>
      </form>
    </section>
  );
}

function EndpointCapabilitiesPanel({ endpointStatus, onPushFirmwareUpdate, firmwareUpdateBusy }) {
  const capabilities = endpointCapabilities(endpointStatus);
  const identity = capabilities.identity || {};
  const display = capabilities.display || {};
  const audio = capabilities.audio || {};
  const audioInput = audio.input || {};
  const audioOutput = audio.output || {};
  const firmware = capabilities.firmware || {};
  const controls = capabilities.controls || {};
  const storage = capabilities.storage || {};
  const firmwareUpdate = endpointStatus?.firmware_update || {};
  const displayResolution = display.resolution || (
    typeof display.width === "number" && typeof display.height === "number"
      ? `${display.width}x${display.height}`
      : "unknown"
  );
  const audioInputSummary = audioInput.sample_rate_hz
    ? `${audioInput.sample_rate_hz} Hz, ${audioInput.channels || "?"} ch`
    : "unknown";
  const audioOutputSummary = typeof audioOutput.volume_percent === "number"
    ? `${audioOutput.volume_percent}% ${audioOutput.muted ? "muted" : "active"}`
    : "unknown";
  const controlLabels = Object.entries(controls)
    .filter(([, supported]) => supported === true)
    .map(([name]) => name)
    .join(", ");

  return (
    <section className="voice-endpoint-panel stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoint Capabilities</p>
          <h2 className="panel-title">Hardware & Firmware</h2>
        </div>
        <span className="status-pill status-pill-neutral">{endpointStatus?.firmware_version || "unknown FW"}</span>
      </div>
      <dl className="facts">
        <div>
          <dt>Hardware ID</dt>
          <dd>{endpointHardwareId(endpointStatus)}</dd>
        </div>
        <div>
          <dt>ID source</dt>
          <dd>{identity.id_source || "unknown"}</dd>
        </div>
        <div>
          <dt>Firmware</dt>
          <dd>{firmware.version || endpointStatus?.firmware_version || "unknown"}</dd>
        </div>
        <div>
          <dt>Build</dt>
          <dd>{firmware.build_date && firmware.build_time ? `${firmware.build_date} ${firmware.build_time}` : "unknown"}</dd>
        </div>
        <div>
          <dt>Touchscreen</dt>
          <dd>{formatYesNo(capabilities.touchscreen?.available)}</dd>
        </div>
        <div>
          <dt>SD card</dt>
          <dd>{formatYesNo(storage.sd_card_available)}</dd>
        </div>
        <div>
          <dt>File transfer</dt>
          <dd>{storage.media_transfer_active ? "downloading file" : storage.media_transfer_status || "idle"}</dd>
        </div>
        <div>
          <dt>Display</dt>
          <dd>{`${displayResolution}, ${display.pixel_format || "unknown"}`}</dd>
        </div>
        <div>
          <dt>Audio input</dt>
          <dd>{audioInputSummary}</dd>
        </div>
        <div>
          <dt>Audio output</dt>
          <dd>{audioOutputSummary}</dd>
        </div>
        <div>
          <dt>Controls</dt>
          <dd>{controlLabels || "unknown"}</dd>
        </div>
      </dl>
      <div className="actions">
        <button
          className="btn btn-primary"
          type="button"
          disabled={!endpointStatus?.endpoint_id || !firmwareUpdate.update_available || firmwareUpdateBusy}
          onClick={() => onPushFirmwareUpdate?.({
            endpointId: endpointStatus?.endpoint_id,
            firmwareUpdate,
          })}
        >
          {firmwareUpdateBusy ? "Sending OTA..." : "Send OTA"}
        </button>
      </div>
    </section>
  );
}

function EndpointProvisioningPanel({ endpointStatus, voiceStatus, onRefresh, setActionMessage }) {
  const endpointId = endpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "";
  const provisioning = endpointCapabilities(endpointStatus).provisioning || {};
  const discovery = provisioning.discovery || {};
  const [provisionedEndpointId, setProvisionedEndpointId] = useState(provisioning.endpoint_id || endpointId);
  const [displayName, setDisplayName] = useState(provisioning.display_name || endpointStatus?.display_name || "");
  const [backendHost, setBackendHost] = useState(provisioning.backend_host || "");
  const [httpPort, setHttpPort] = useState(provisioning.http_port || 9004);
  const [wsPort, setWsPort] = useState(provisioning.ws_port || 9004);
  const [useTls, setUseTls] = useState(Boolean(provisioning.use_tls));
  const [wifiSsid, setWifiSsid] = useState("");
  const [wifiPassword, setWifiPassword] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setProvisionedEndpointId(provisioning.endpoint_id || endpointId);
    setDisplayName(provisioning.display_name || endpointStatus?.display_name || "");
    setBackendHost(provisioning.backend_host || "");
    setHttpPort(provisioning.http_port || 9004);
    setWsPort(provisioning.ws_port || 9004);
    setUseTls(Boolean(provisioning.use_tls));
    setWifiSsid("");
    setWifiPassword("");
  }, [
    endpointId,
    endpointStatus?.display_name,
    provisioning.backend_host,
    provisioning.display_name,
    provisioning.endpoint_id,
    provisioning.http_port,
    provisioning.use_tls,
    provisioning.ws_port,
  ]);

  async function handleApply(event) {
    event.preventDefault();
    if (!endpointId) {
      setActionMessage("Provisioning skipped: endpoint is not connected.");
      return;
    }

    const payload = {
      provisioned_endpoint_id: provisionedEndpointId,
      display_name: displayName,
      backend_host: backendHost,
      http_port: Number(httpPort),
      ws_port: Number(wsPort),
      use_tls: useTls,
    };
    if (wifiSsid) {
      payload.wifi_ssid = wifiSsid;
    }
    if (wifiPassword) {
      payload.wifi_password = wifiPassword;
    }

    setBusy(true);
    try {
      const result = await applyEndpointProvisioning(endpointId, payload);
      setActionMessage(result.accepted ? `Provisioning sent (${result.status}, ${result.request_id}).` : `Provisioning skipped: ${result.reason}`);
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  async function handleReset() {
    if (!endpointId) {
      setActionMessage("Provisioning reset skipped: endpoint is not connected.");
      return;
    }

    setBusy(true);
    try {
      const result = await resetEndpointProvisioning(endpointId);
      setActionMessage(result.accepted ? `Provisioning reset sent (${result.status}, ${result.request_id}).` : `Provisioning reset skipped: ${result.reason}`);
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="voice-endpoint-panel stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoint Settings</p>
          <h2 className="panel-title">Provisioning</h2>
        </div>
        <span className="status-pill status-pill-neutral">{provisioning.configured ? "persisted" : "build defaults"}</span>
      </div>
      <form className="endpoint-metadata-form" onSubmit={handleApply}>
        <label>
          <span>Endpoint ID</span>
          <input
            type="text"
            value={provisionedEndpointId}
            maxLength={63}
            onChange={(event) => setProvisionedEndpointId(event.target.value)}
            disabled={busy || !endpointId}
          />
        </label>
        <label>
          <span>Display name</span>
          <input
            type="text"
            value={displayName}
            maxLength={63}
            onChange={(event) => setDisplayName(event.target.value)}
            disabled={busy || !endpointId}
          />
        </label>
        <label>
          <span>Backend host</span>
          <input
            type="text"
            value={backendHost}
            maxLength={95}
            onChange={(event) => setBackendHost(event.target.value)}
            disabled={busy || !endpointId}
          />
        </label>
        <label>
          <span>HTTP port</span>
          <input
            type="number"
            min="1"
            max="65535"
            value={httpPort}
            onChange={(event) => setHttpPort(event.target.value)}
            disabled={busy || !endpointId}
          />
        </label>
        <label>
          <span>WS port</span>
          <input
            type="number"
            min="1"
            max="65535"
            value={wsPort}
            onChange={(event) => setWsPort(event.target.value)}
            disabled={busy || !endpointId}
          />
        </label>
        <label className="endpoint-media-check">
          <input type="checkbox" checked={useTls} onChange={(event) => setUseTls(event.target.checked)} disabled={busy || !endpointId} />
          <span>TLS</span>
        </label>
        <label>
          <span>Wi-Fi SSID</span>
          <input
            type="text"
            value={wifiSsid}
            maxLength={32}
            placeholder={provisioning.wifi_configured ? "unchanged" : ""}
            onChange={(event) => setWifiSsid(event.target.value)}
            disabled={busy || !endpointId}
          />
        </label>
        <label>
          <span>Wi-Fi password</span>
          <input
            type="password"
            value={wifiPassword}
            maxLength={64}
            placeholder={provisioning.wifi_configured ? "unchanged" : ""}
            onChange={(event) => setWifiPassword(event.target.value)}
            disabled={busy || !endpointId}
          />
        </label>
        <button className="btn btn-secondary" type="submit" disabled={busy || !endpointId || !provisionedEndpointId || !backendHost}>
          Apply Settings
        </button>
        <button className="btn btn-ghost" type="button" onClick={handleReset} disabled={busy || !endpointId}>
          Reset Settings
        </button>
      </form>
      <dl className="facts">
        <div>
          <dt>Discovery</dt>
          <dd>{discovery.enabled ? "enabled" : "disabled"}</dd>
        </div>
        <div>
          <dt>Discovery port</dt>
          <dd>{discovery.udp_port || "unknown"}</dd>
        </div>
        <div>
          <dt>Discovery status</dt>
          <dd>{discovery.status || "unknown"}</dd>
        </div>
      </dl>
    </section>
  );
}

function MediaInventoryList({ title, items }) {
  return (
    <div className="endpoint-media-inventory-group">
      <h3>{title}</h3>
      {items.length === 0 ? (
        <p className="muted-text">empty</p>
      ) : (
        <ul className="endpoint-media-list">
          {items.map((item) => (
            <li key={`${title}-${item.filename}`}>
              <span>{item.filename}</span>
              <code>{item.size_bytes ?? "?"} B</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EndpointMediaManagerPanel({ endpointId, onRefresh, setActionMessage }) {
  const [assets, setAssets] = useState([]);
  const [inventory, setInventory] = useState(null);
  const [mediaType, setMediaType] = useState("picture");
  const [assetClass, setAssetClass] = useState("background");
  const [spriteWidth, setSpriteWidth] = useState("");
  const [spriteHeight, setSpriteHeight] = useState("");
  const [selectedAssetId, setSelectedAssetId] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [overwrite, setOverwrite] = useState(true);
  const [activate, setActivate] = useState(true);
  const [busy, setBusy] = useState(false);

  async function refreshMedia() {
    const [assetPayload, inventoryPayload] = await Promise.all([
      getEndpointMediaAssets().catch(() => ({ assets: [] })),
      endpointId ? getEndpointMediaInventory(endpointId).catch(() => null) : Promise.resolve(null),
    ]);
    setAssets(assetPayload.assets || []);
    setInventory(inventoryPayload);
    setSelectedAssetId((current) => current || assetPayload.assets?.[0]?.asset_id || "");
  }

  useEffect(() => {
    refreshMedia().catch(() => {
      // Main dashboard refresh remains useful when media APIs are not ready yet.
    });
  }, [endpointId]);

  async function handleUpload(event) {
    event.preventDefault();
    if (!selectedFile) {
      setActionMessage("Media upload skipped: choose a file first.");
      return;
    }

    setBusy(true);
    try {
      const contentBase64 = await readFileAsBase64(selectedFile);
      const metadata = {
        asset_class: assetClass,
      };
      if (mediaType === "sprite" && spriteWidth && spriteHeight) {
        metadata.width = Number(spriteWidth);
        metadata.height = Number(spriteHeight);
      }
      const asset = await uploadEndpointMedia({
        media_type: mediaType,
        filename: selectedFile.name,
        content_base64: contentBase64,
        content_type: selectedFile.type || "application/octet-stream",
        metadata,
        overwrite,
        rewrite: overwrite,
        activate,
      });
      setSelectedAssetId(asset.asset_id);
      setActionMessage(`Uploaded ${asset.filename}.`);
      await refreshMedia();
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDeliver() {
    if (!endpointId) {
      setActionMessage("Delivery skipped: endpoint is not connected.");
      return;
    }
    if (!selectedAssetId) {
      setActionMessage("Delivery skipped: choose a staged asset first.");
      return;
    }

    setBusy(true);
    try {
      const result = await deliverEndpointMedia(selectedAssetId, endpointId, { rewrite: overwrite, activate });
      setActionMessage(result.accepted ? `Delivery sent (${result.status}, ${result.request_id}).` : `Delivery skipped: ${result.reason}`);
      await refreshMedia();
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  async function handleReformatStorage() {
    if (!endpointId) {
      setActionMessage("Reformat skipped: endpoint is not connected.");
      return;
    }

    setBusy(true);
    try {
      const result = await reformatEndpointStorage(endpointId);
      setActionMessage(result.accepted ? `Reformat sent (${result.status}, ${result.request_id}).` : `Reformat skipped: ${result.reason}`);
      await refreshMedia();
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!selectedAssetId) {
      setActionMessage("Delete skipped: choose a staged asset first.");
      return;
    }

    setBusy(true);
    try {
      const deleted = await deleteEndpointMedia(selectedAssetId);
      setActionMessage(`Deleted staged asset ${deleted.filename}.`);
      setSelectedAssetId("");
      await refreshMedia();
    } catch (err) {
      setActionMessage(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  const pictures = inventory?.pictures || [];
  const sprites = inventory?.sprites || [];
  const sounds = inventory?.sounds || [];

  return (
    <section className="voice-endpoint-panel stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoint Media</p>
          <h2 className="panel-title">SD Asset Manager</h2>
        </div>
        <span className="status-pill status-pill-neutral">{inventory?.truncated ? "inventory truncated" : `${assets.length} staged`}</span>
      </div>
      <form className="endpoint-media-form" onSubmit={handleUpload}>
        <label>
          <span>Type</span>
          <select value={mediaType} onChange={(event) => setMediaType(event.target.value)} disabled={busy}>
            <option value="picture">Picture</option>
            <option value="sprite">Sprite</option>
            <option value="sound">Sound</option>
          </select>
        </label>
        <label>
          <span>Class</span>
          <select value={assetClass} onChange={(event) => setAssetClass(event.target.value)} disabled={busy}>
            <option value="background">Background</option>
            <option value="avatar">Avatar</option>
            <option value="sprite">Sprite</option>
            <option value="manifest">Manifest</option>
            <option value="alpha_mask">Alpha Mask</option>
            <option value="sound">Sound</option>
          </select>
        </label>
        {mediaType === "sprite" ? (
          <label>
            <span>Size</span>
            <span className="endpoint-media-size">
              <input
                type="number"
                min="1"
                max="320"
                placeholder="W"
                value={spriteWidth}
                onChange={(event) => setSpriteWidth(event.target.value)}
                disabled={busy}
              />
              <input
                type="number"
                min="1"
                max="240"
                placeholder="H"
                value={spriteHeight}
                onChange={(event) => setSpriteHeight(event.target.value)}
                disabled={busy}
              />
            </span>
          </label>
        ) : null}
        <label>
          <span>File</span>
          <input type="file" onChange={(event) => setSelectedFile(event.target.files?.[0] || null)} disabled={busy} />
        </label>
        <label className="endpoint-media-check">
          <input type="checkbox" checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} disabled={busy} />
          <span>Rewrite</span>
        </label>
        <label className="endpoint-media-check">
          <input type="checkbox" checked={activate} onChange={(event) => setActivate(event.target.checked)} disabled={busy} />
          <span>Activate</span>
        </label>
        <button className="btn btn-secondary" type="submit" disabled={busy || !selectedFile}>
          Upload
        </button>
      </form>
      <div className="endpoint-media-actions">
        <select value={selectedAssetId} onChange={(event) => setSelectedAssetId(event.target.value)} disabled={busy || assets.length === 0}>
          <option value="">Select staged asset</option>
          {assets.map((asset) => (
            <option key={asset.asset_id} value={asset.asset_id}>
              {asset.media_type}: {asset.filename}
            </option>
          ))}
        </select>
        <button className="btn btn-primary" type="button" onClick={handleDeliver} disabled={busy || !endpointId || !selectedAssetId}>
          Deliver
        </button>
        <button className="btn btn-ghost" type="button" onClick={handleDelete} disabled={busy || !selectedAssetId}>
          Delete
        </button>
        <button className="btn btn-ghost" type="button" onClick={refreshMedia} disabled={busy}>
          Refresh Media
        </button>
        <button className="btn btn-ghost" type="button" onClick={handleReformatStorage} disabled={busy || !endpointId}>
          Reformat SD Media
        </button>
      </div>
      <div className="endpoint-media-inventory">
        <MediaInventoryList title="Pictures" items={pictures} />
        <MediaInventoryList title="Sprites" items={sprites} />
        <MediaInventoryList title="Sounds" items={sounds} />
      </div>
    </section>
  );
}

function wakeRecordingId(recording) {
  if (!recording || typeof recording !== "object") {
    return "";
  }
  if (recording.recording_id) {
    return String(recording.recording_id);
  }
  const wavPath = typeof recording.wav_path === "string" ? recording.wav_path : "";
  const filename = wavPath.split(/[\\/]/).pop() || "";
  return filename.endsWith(".wav") ? filename.slice(0, -4) : "";
}

function ttsStreamId(tts) {
  if (!tts || typeof tts !== "object") {
    return "";
  }
  return tts.stream_id ? String(tts.stream_id) : "";
}

function visibleHistorySessions(sessions) {
  return sessions.filter((session) => session?.session_state !== "cancelled" && session?.completion_reason !== "cancelled");
}

function latencyTimeline(session) {
  const rawPoints = Array.isArray(session?.latency_points) && session.latency_points.length ? session.latency_points : [
    ["vad_voice_detected", "VAD voice detected", session?.vad?.speech_started_at],
    ["wake_word_detected", "Wake word detected", session?.wake?.detected_at],
    ["vad_silence", "VAD silence", session?.vad?.speech_ended_at],
    ["stt_start", "STT start", session?.latency?.stt_started_at],
    ["stt_end", "STT end", session?.transcript?.completed_at],
    ["intent_processing_done", "Intent processing done", session?.assistant?.completed_at],
    ["tts_start", "TTS start", session?.tts?.started_at],
    ["tts_end", "TTS end", session?.tts?.completed_at],
    ["session_end", "Session end", session?.completed_at],
  ]
    .filter(([, , timestamp]) => timestamp)
    .map(([key, label, timestamp]) => ({ key, label, timestamp }));
  let previousAt = null;
  return rawPoints.map((point) => {
    const timestamp = typeof point.timestamp === "string" ? point.timestamp : "";
    const currentAt = timestamp ? new Date(timestamp) : null;
    const hasCurrentAt = currentAt && !Number.isNaN(currentAt.getTime());
    const offsetFromPrevious =
      typeof point.offset_from_previous_ms === "number"
        ? point.offset_from_previous_ms
        : hasCurrentAt && previousAt
          ? Math.max(0, currentAt.getTime() - previousAt.getTime())
          : null;
    if (hasCurrentAt) {
      previousAt = currentAt;
    }
    return {
      ...point,
      offset_from_previous_ms: offsetFromPrevious,
    };
  });
}

function VoiceSessionDetailPopout({ session, loading, error, onClose }) {
  if (!session && !loading && !error) {
    return null;
  }
  const points = latencyTimeline(session);
  const audioQuality = session?.transcript?.audio_quality || null;
  return (
    <div className="voice-history-popout-backdrop" role="presentation" onClick={onClose}>
      <section
        className="voice-history-popout"
        role="dialog"
        aria-modal="true"
        aria-label="Voice session latency details"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="section-heading">
          <div>
            <p className="panel-kicker">Voice History</p>
            <h2 className="panel-title">Latency Timeline</h2>
          </div>
          <button className="btn btn-ghost btn-compact" type="button" onClick={onClose}>
            Close
          </button>
        </div>
        {loading ? <div className="callout callout-neutral">Loading session details...</div> : null}
        {error ? <div className="callout callout-danger">{error}</div> : null}
        {session ? (
          <>
            <dl className="facts voice-history-detail-facts">
              <div>
                <dt>Session</dt>
                <dd>{valueOrEmpty(session.session_id)}</dd>
              </div>
              <div>
                <dt>Endpoint</dt>
                <dd>{valueOrEmpty(session.endpoint_id)}</dd>
              </div>
              <div>
                <dt>Transcript</dt>
                <dd>{valueOrEmpty(session.transcript?.text)}</dd>
              </div>
              <div>
                <dt>Reply</dt>
                <dd>{valueOrEmpty(session.assistant?.text || session.tts?.spoken_text)}</dd>
              </div>
              <div>
                <dt>Audio quality</dt>
                <dd className="audio-quality-inline">
                  <AudioQualityBadge audioQuality={audioQuality} />
                  <span>{audioQualitySummary(audioQuality)}</span>
                </dd>
              </div>
            </dl>
            <section className="stack">
              <div className="section-heading">
                <div>
                  <p className="panel-kicker">Track 1</p>
                  <h3 className="section-title">Audio Quality</h3>
                </div>
              </div>
              <AudioQualityFacts audioQuality={audioQuality} />
            </section>
            <div className="voice-history-timeline">
              {points.length ? (
                points.map((point) => (
                  <div className="voice-history-timeline-row" key={point.key}>
                    <span className="voice-history-timeline-dot" />
                    <span className="voice-history-timeline-label">{point.label}</span>
                    <span className="voice-history-timeline-time">{formatLocalDateTime(point.timestamp)}</span>
                    <span className="voice-history-timeline-offset">
                      {typeof point.offset_from_vad_ms === "number" ? `+${Math.round(point.offset_from_vad_ms)} ms` : ""}
                    </span>
                    <span className="voice-history-timeline-previous">
                      {typeof point.offset_from_previous_ms === "number"
                        ? `${Math.round(point.offset_from_previous_ms)} ms from last`
                        : ""}
                    </span>
                  </div>
                ))
              ) : (
                <div className="callout callout-neutral">No latency timeline has been recorded for this session yet.</div>
              )}
            </div>
          </>
        ) : null}
      </section>
    </div>
  );
}

function VoiceSessionHistoryPanel({
  sessions,
  historyStatus,
  endpointId,
  detailSession,
  detailLoading,
  detailError,
  onOpenSessionDetail,
  onCloseSessionDetail,
  onReplaySession,
  onReplayWakeRecording,
  onDeleteWakeRecording,
  onDeleteTtsArtifact,
  onDeleteEndpointArtifacts,
  onRefreshHistory,
}) {
  const visibleSessions = sessions;
  const storedCount = typeof historyStatus?.stored_count === "number" ? historyStatus.stored_count : sessions.length;
  const [audioQualityDetail, setAudioQualityDetail] = useState(null);

  return (
    <section className="voice-endpoint-panel stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Voice History</p>
          <h2 className="panel-title">Recent Turns</h2>
        </div>
        <span className="status-pill status-pill-neutral">{`${storedCount} stored`}</span>
      </div>
      <div className="voice-history-table-wrap">
        <table className="voice-history-table">
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Endpoint</th>
              <th scope="col">Status</th>
              <th scope="col">Wake</th>
              <th scope="col">Transcript</th>
              <th scope="col">Response</th>
              <th scope="col">Total</th>
              <th scope="col">Wake Audio</th>
              <th scope="col">TTS Audio</th>
              <th scope="col">Replay</th>
            </tr>
          </thead>
          <tbody>
            {visibleSessions.length ? (
              visibleSessions.map((session) => {
                const replayEligible = session?.replay?.eligible === true;
                const targetEndpointId = endpointId || session.endpoint_id || "";
                const recordingId = wakeRecordingId(session.wake_recording);
                const streamId = ttsStreamId(session.tts);
                const ttsUrl = session.tts?.endpoint_audio_url || session.tts?.audio_url || "";
                const audioQuality = session?.transcript?.audio_quality || null;
                return (
                  <tr
                    className="voice-history-row"
                    key={session.session_id}
                    tabIndex={0}
                    onClick={() => onOpenSessionDetail(session)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onOpenSessionDetail(session);
                      }
                    }}
                  >
                    <td>{formatLocalDateTime(session.completed_at || session.updated_at || session.started_at)}</td>
                    <td>{valueOrEmpty(session.endpoint_id)}</td>
                    <td>
                      <div className="voice-history-status-cell">
                        <span>{sessionStateLabel(session.session_state)}</span>
                        <AudioQualityButton audioQuality={audioQuality} onOpen={() => setAudioQualityDetail(audioQuality)} />
                      </div>
                    </td>
                    <td>{formatPercent(session.wake?.confidence)}</td>
                    <td className="voice-history-text">{valueOrEmpty(session.transcript?.text)}</td>
                    <td className="voice-history-text">{valueOrEmpty(session.assistant?.text)}</td>
                    <td>{formatMs(session.turn_timings?.total_ms ?? session.duration_ms)}</td>
                    <td onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                      <div className="compact-actions">
                        <button
                          className="btn btn-ghost btn-compact"
                          type="button"
                          onClick={() => onReplayWakeRecording(session.wake_recording)}
                          disabled={!recordingId}
                        >
                          Play
                        </button>
                        <a className={`btn btn-ghost btn-compact${recordingId ? "" : " disabled-link"}`} href={recordingId ? wakeRecordingAudioUrl(recordingId) : undefined}>
                          Download
                        </a>
                        <button
                          className="btn btn-ghost btn-compact"
                          type="button"
                          onClick={() => onDeleteWakeRecording(recordingId)}
                          disabled={!recordingId}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                    <td onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                      <div className="compact-actions">
                        <a className={`btn btn-ghost btn-compact${ttsUrl ? "" : " disabled-link"}`} href={ttsUrl || undefined}>
                          Download
                        </a>
                        <button
                          className="btn btn-ghost btn-compact"
                          type="button"
                          onClick={() => onDeleteTtsArtifact(streamId)}
                          disabled={!streamId}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                    <td onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                      <button
                        className="btn btn-ghost btn-compact"
                        type="button"
                        onClick={() => onReplaySession(session.session_id, targetEndpointId)}
                        disabled={!replayEligible || !targetEndpointId}
                      >
                        Replay
                      </button>
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={10}>none</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="compact-actions">
        <button className="btn btn-ghost" type="button" onClick={onRefreshHistory}>
          Refresh History
        </button>
        <button className="btn btn-ghost" type="button" onClick={onDeleteEndpointArtifacts} disabled={!endpointId}>
          Delete Endpoint Audio
        </button>
      </div>
      <VoiceSessionDetailPopout
        session={detailSession}
        loading={detailLoading}
        error={detailError}
        onClose={onCloseSessionDetail}
      />
      <AudioQualityDetailPopout audioQuality={audioQualityDetail} onClose={() => setAudioQualityDetail(null)} />
    </section>
  );
}

function EndpointAdvancedSection({ title, kicker, badge, children }) {
  return (
    <details className="endpoint-advanced-section">
      <summary>
        <span>
          <span className="panel-kicker">{kicker}</span>
          <strong>{title}</strong>
        </span>
        <span className="status-pill status-pill-neutral">{badge}</span>
      </summary>
      <div className="endpoint-advanced-body">
        {children}
      </div>
    </details>
  );
}

export function VoiceEndpointDashboardSection({
  voiceStatus,
  endpointStatus,
  endpointRegistry,
  onRefresh,
}) {
  const endpointStatuses = endpointStatusesFromRegistry(endpointStatus, endpointRegistry);
  const [selectedEndpointId, setSelectedEndpointId] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [volumePercent, setVolumePercent] = useState(70);
  const [muted, setMuted] = useState(false);
  const [voiceSessions, setVoiceSessions] = useState([]);
  const [historyStatus, setHistoryStatus] = useState(voiceStatus?.session_history || null);
  const [historyDetailSession, setHistoryDetailSession] = useState(null);
  const [historyDetailLoading, setHistoryDetailLoading] = useState(false);
  const [historyDetailError, setHistoryDetailError] = useState("");
  const [firmwareUpdateBusy, setFirmwareUpdateBusy] = useState(false);
  const selectedEndpointStatus = endpointStatusById(endpointStatuses, selectedEndpointId);
  const endpointId = selectedEndpointStatus?.endpoint_id || selectedEndpointId || voiceStatus?.endpoint_id || "";
  const scopedVoiceStatus = selectedVoiceStatus(voiceStatus, endpointId);
  const latestEndpointSession = visibleHistorySessions(voiceSessions)[0] || null;
  const reportedOutput = endpointCapabilities(selectedEndpointStatus).audio?.output || {};

  useEffect(() => {
    if (!endpointStatuses.length) {
      return;
    }
    const hasSelected = selectedEndpointId && endpointStatuses.some((status) => status?.endpoint_id === selectedEndpointId);
    if (!hasSelected) {
      setSelectedEndpointId(endpointStatuses[0]?.endpoint_id || "");
    }
  }, [endpointStatuses, selectedEndpointId]);

  useEffect(() => {
    if (typeof reportedOutput.volume_percent === "number") {
      setVolumePercent(reportedOutput.volume_percent);
    }
    if (typeof reportedOutput.muted === "boolean") {
      setMuted(reportedOutput.muted);
    }
  }, [reportedOutput.volume_percent, reportedOutput.muted]);

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

  useEffect(() => {
    const status = voiceStatus?.session_history || null;
    setHistoryStatus(status);
  }, [voiceStatus?.session_history]);

  useEffect(() => {
    let active = true;
    getVoiceSessions({ limit: 12, endpointId: endpointId || undefined })
      .then((payload) => {
        if (active) {
          setVoiceSessions(payload.sessions || []);
        }
      })
      .catch(() => {
        if (active && Array.isArray(voiceStatus?.session_history?.recent_sessions)) {
          setVoiceSessions((current) => (current.length ? current : voiceStatus.session_history.recent_sessions));
        }
      });

    return () => {
      active = false;
    };
  }, [endpointId, voiceStatus?.session_history?.updated_at]);

  async function refreshVoiceSessions({ showMessage = true } = {}) {
    try {
      const payload = await getVoiceSessions({ limit: 12, endpointId: endpointId || undefined });
      setVoiceSessions(payload.sessions || []);
      if (showMessage) {
        setActionMessage("History refreshed.");
      }
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  async function handleOpenSessionDetail(session) {
    if (!session?.session_id) {
      return;
    }
    setHistoryDetailSession(session);
    setHistoryDetailError("");
    setHistoryDetailLoading(true);
    try {
      const payload = await getVoiceSession(session.session_id);
      setHistoryDetailSession(payload.session || session);
    } catch (err) {
      setHistoryDetailError(String(err.message || err));
    } finally {
      setHistoryDetailLoading(false);
    }
  }

  function handleCloseSessionDetail() {
    setHistoryDetailSession(null);
    setHistoryDetailError("");
    setHistoryDetailLoading(false);
  }

  async function handleTestTurn() {
    try {
      const result = await testAssistantTurn(endpointId || "dashboard-test");
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

  async function handleReplaySession(sessionId, targetEndpointId) {
    try {
      if (!sessionId) {
        setActionMessage("Replay skipped: session is missing.");
        return;
      }
      if (!targetEndpointId) {
        setActionMessage("Replay skipped: endpoint is not connected.");
        return;
      }
      const result = await replayVoiceSession(sessionId, targetEndpointId);
      setActionMessage(result.accepted ? `Replay sent (${result.status}, ${result.request_id}).` : `Replay skipped: ${result.reason}`);
      await onRefresh();
      await refreshVoiceSessions({ showMessage: false });
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  async function handleReplayWakeRecording(recording) {
    const recordingId = wakeRecordingId(recording);
    if (!recordingId) {
      setActionMessage("Wake replay skipped: recording is missing.");
      return;
    }
    try {
      const audio = new Audio(wakeRecordingAudioUrl(recordingId));
      await audio.play();
      setActionMessage("Wake recording playback started.");
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  async function handleDeleteWakeRecording(recordingId) {
    if (!recordingId) {
      setActionMessage("Wake delete skipped: recording is missing.");
      return;
    }
    try {
      const result = await deleteWakeRecording(recordingId);
      setActionMessage(`Deleted wake recording ${recordingId} (${result.deleted_count || 0} files).`);
      await refreshVoiceSessions({ showMessage: false });
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  async function handleDeleteTtsArtifact(streamId) {
    if (!streamId) {
      setActionMessage("TTS delete skipped: stream is missing.");
      return;
    }
    try {
      const result = await deleteVoiceTtsArtifact(streamId);
      setActionMessage(`Deleted TTS artifact ${streamId} (${result.deleted_count || 0} files).`);
      await refreshVoiceSessions({ showMessage: false });
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  async function handleDeleteEndpointArtifacts() {
    if (!endpointId) {
      setActionMessage("Endpoint audio delete skipped: endpoint is not connected.");
      return;
    }
    try {
      const result = await deleteEndpointVoiceArtifacts(endpointId);
      setActionMessage(
        `Deleted endpoint audio for ${endpointId}: ${result.wake_deleted_count || 0} wake files, ${result.tts_deleted_count || 0} TTS files.`,
      );
      await refreshVoiceSessions({ showMessage: false });
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    }
  }

  async function handlePushFirmwareUpdate(endpointRow) {
    const update = endpointRow?.firmwareUpdate || {};
    if (!endpointRow?.endpointId || !update.filename) {
      setActionMessage("OTA skipped: firmware artifact is missing.");
      return;
    }
    setFirmwareUpdateBusy(true);
    try {
      const result = await pushFirmwareOta({
        endpointId: endpointRow.endpointId,
        filename: update.filename,
        version: update.latest_version,
      });
      setActionMessage(
        result.accepted
          ? `OTA sent to ${endpointRow.endpointId}: ${update.filename}.`
          : `OTA skipped: ${result.reason || "endpoint unavailable"}`,
      );
      await onRefresh();
    } catch (err) {
      setActionMessage(String(err.message || err));
    } finally {
      setFirmwareUpdateBusy(false);
    }
  }

  return (
    <section className="card stack panel voice-endpoint-main-card">
      <EndpointStatusTable
        voiceStatus={voiceStatus}
        endpointStatus={endpointStatus}
        endpointRegistry={endpointRegistry}
        selectedEndpointId={endpointId}
        onSelectEndpoint={setSelectedEndpointId}
      />
      <div className="voice-endpoint-top">
        <VoicePipelinePanel voiceStatus={scopedVoiceStatus} latestSession={latestEndpointSession} />
        <VoiceEndpointActionsCard
          voiceStatus={scopedVoiceStatus}
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
      <VoiceSessionHistoryPanel
        sessions={voiceSessions}
        historyStatus={historyStatus}
        endpointId={endpointId}
        detailSession={historyDetailSession}
        detailLoading={historyDetailLoading}
        detailError={historyDetailError}
        onOpenSessionDetail={handleOpenSessionDetail}
        onCloseSessionDetail={handleCloseSessionDetail}
        onReplaySession={handleReplaySession}
        onReplayWakeRecording={handleReplayWakeRecording}
        onDeleteWakeRecording={handleDeleteWakeRecording}
        onDeleteTtsArtifact={handleDeleteTtsArtifact}
        onDeleteEndpointArtifacts={handleDeleteEndpointArtifacts}
        onRefreshHistory={refreshVoiceSessions}
      />
      <EndpointAdvancedSection
        title="Hardware & Firmware"
        kicker="Endpoint Capabilities"
        badge={selectedEndpointStatus?.firmware_version || "unknown FW"}
      >
        <EndpointCapabilitiesPanel
          endpointStatus={selectedEndpointStatus}
          onPushFirmwareUpdate={handlePushFirmwareUpdate}
          firmwareUpdateBusy={firmwareUpdateBusy}
        />
      </EndpointAdvancedSection>
      <EndpointAdvancedSection
        title="Provisioning"
        kicker="Endpoint Settings"
        badge={endpointCapabilities(selectedEndpointStatus).provisioning?.configured ? "persisted" : "build defaults"}
      >
        <EndpointProvisioningPanel
          voiceStatus={scopedVoiceStatus}
          endpointStatus={selectedEndpointStatus}
          onRefresh={onRefresh}
          setActionMessage={setActionMessage}
        />
      </EndpointAdvancedSection>
      <EndpointAdvancedSection title="SD Asset Manager" kicker="Endpoint Media" badge="advanced">
        <EndpointMediaManagerPanel
          endpointId={endpointId}
          onRefresh={onRefresh}
          setActionMessage={setActionMessage}
        />
      </EndpointAdvancedSection>
      <EndpointAdvancedSection
        title="Operator Metadata"
        kicker="Endpoint Registry"
        badge={labelizeState(selectedEndpointStatus?.connection_state, "unregistered")}
      >
        <EndpointMetadataPanel
          voiceStatus={scopedVoiceStatus}
          endpointStatus={selectedEndpointStatus}
          onRefresh={onRefresh}
          setActionMessage={setActionMessage}
        />
      </EndpointAdvancedSection>
    </section>
  );
}
