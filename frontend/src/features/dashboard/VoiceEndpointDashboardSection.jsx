import { useEffect, useState } from "react";
import {
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
  return `${Math.round(value)} ms`;
}

function formatPercent(value) {
  if (typeof value !== "number") {
    return "none";
  }
  return `${Math.round(value * 100)}%`;
}

function formatYesNo(value) {
  if (typeof value !== "boolean") {
    return "unknown";
  }
  return value ? "yes" : "no";
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

function EndpointStatusTable({ voiceStatus, endpointStatus, onPushFirmwareUpdate, firmwareUpdateBusy }) {
  const [selectedEndpoint, setSelectedEndpoint] = useState(null);
  const session = voiceStatus?.active_session;
  const projection = voiceStateProjection(voiceStatus);
  const storage = endpointCapabilities(endpointStatus).storage || {};
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
      fileTransfer: storage.media_transfer_active ? "downloading file" : storage.media_transfer_status || "idle",
      lastSeenAt: formatLocalDateTime(endpointStatus?.last_seen_at),
      voiceConnection: projection.connection_state,
      uxState: projection.ux_state,
      sessionState: projection.session_state || "none",
      transportHealth: projection.transport_health,
      sessionId: session?.session_id || "none",
      sttLatency: formatMs(timings.stt_ms),
      totalLatency: formatMs(timings.total_ms),
      firmwareUpdate: endpointStatus?.firmware_update || {},
      raw: {
        endpointStatus,
        voiceStatus: {
          endpoint_id: voiceStatus?.endpoint_id,
          connection_state: voiceStatus?.connection_state,
          transport_health: voiceStatus?.transport_health,
          active_session: voiceStatus?.active_session,
          state_projection: voiceStatus?.state_projection,
          last_turn_timings: voiceStatus?.last_turn_timings,
          last_event_type: voiceStatus?.last_event_type,
          last_error: voiceStatus?.last_error,
        },
      },
    },
  ];
  const selectedDetailRows = selectedEndpoint
    ? [
        ["Endpoint", selectedEndpoint.endpointId],
        ["Name", selectedEndpoint.displayName],
        ["Zone", selectedEndpoint.zoneId],
        ["Firmware", selectedEndpoint.firmwareVersion],
        ["Latest firmware", selectedEndpoint.firmwareUpdate.latest_version || "unknown"],
        ["Firmware artifact", selectedEndpoint.firmwareUpdate.filename || "none"],
        ["Firmware update", selectedEndpoint.firmwareUpdate.reason || "unknown"],
        ["Device state", selectedEndpoint.deviceState],
        ["Registry state", selectedEndpoint.connectionState],
        ["File transfer", selectedEndpoint.fileTransfer],
        ["Last heartbeat", selectedEndpoint.lastSeenAt],
        ["Voice connection", selectedEndpoint.voiceConnection],
        ["UX state", selectedEndpoint.uxState],
        ["Session state", selectedEndpoint.sessionState],
        ["Transport", selectedEndpoint.transportHealth],
        ["STT latency", selectedEndpoint.sttLatency],
        ["Total latency", selectedEndpoint.totalLatency],
        ["Session", selectedEndpoint.sessionId],
      ]
    : [];

  return (
    <section className="voice-endpoint-panel stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoint Status</p>
          <h2 className="panel-title">Device Data</h2>
        </div>
        <span className="status-pill status-pill-neutral">{valueOrEmpty(voiceStatus?.connection_state, "offline")}</span>
      </div>
      <div className="endpoint-card-grid">
        {endpointRows.map((row) => (
          <button
            key={row.endpointId}
            className="endpoint-status-card"
            type="button"
            onClick={() => setSelectedEndpoint(row)}
          >
            <div className="endpoint-status-card-header">
              <span className={`endpoint-health-led endpoint-health-led-${row.health}`} aria-label={`${row.health} endpoint health`} />
              <div className="endpoint-status-card-title-block">
                <h3>{valueOrEmpty(row.endpointId)}</h3>
                <span>{valueOrEmpty(row.displayName)}</span>
              </div>
              <span className="status-pill status-pill-neutral">{valueOrEmpty(row.connectionState)}</span>
            </div>
            <div className="endpoint-status-card-facts">
              <span>
                <strong>Device</strong>
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
                <strong>Total</strong>
                {valueOrEmpty(row.totalLatency)}
              </span>
            </div>
            <div className="endpoint-status-card-footer">
              <span>
                FW {valueOrEmpty(row.firmwareVersion)}
                {row.firmwareUpdate?.update_available ? " -> " + valueOrEmpty(row.firmwareUpdate.latest_version) : ""}
              </span>
              <span>{valueOrEmpty(row.lastSeenAt)}</span>
            </div>
          </button>
        ))}
      </div>
      {selectedEndpoint ? (
        <div className="endpoint-detail-backdrop" role="presentation" onClick={() => setSelectedEndpoint(null)}>
          <section
            className="endpoint-detail-popout"
            role="dialog"
            aria-modal="true"
            aria-label={`${selectedEndpoint.endpointId} endpoint details`}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="section-heading">
              <div>
                <p className="panel-kicker">Endpoint Detail</p>
                <h2 className="panel-title">{valueOrEmpty(selectedEndpoint.endpointId)}</h2>
              </div>
              <button className="btn btn-ghost" type="button" onClick={() => setSelectedEndpoint(null)}>
                Close
              </button>
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
            <div className="endpoint-detail-section">
              <h3 className="subsection-title">Raw Data</h3>
              <pre className="json-preview">{JSON.stringify(selectedEndpoint.raw, null, 2)}</pre>
            </div>
            <div className="actions">
              <button
                className="btn btn-primary"
                type="button"
                disabled={!selectedEndpoint.firmwareUpdate?.update_available || firmwareUpdateBusy}
                onClick={() => onPushFirmwareUpdate?.(selectedEndpoint)}
              >
                {firmwareUpdateBusy ? "Sending OTA..." : "Send OTA"}
              </button>
            </div>
          </section>
        </div>
      ) : null}
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

function EndpointCapabilitiesPanel({ endpointStatus }) {
  const capabilities = endpointCapabilities(endpointStatus);
  const display = capabilities.display || {};
  const audio = capabilities.audio || {};
  const audioInput = audio.input || {};
  const audioOutput = audio.output || {};
  const firmware = capabilities.firmware || {};
  const controls = capabilities.controls || {};
  const storage = capabilities.storage || {};
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
            </dl>
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
  const visibleSessions = visibleHistorySessions(sessions);
  const storedCount = typeof historyStatus?.stored_count === "number" ? historyStatus.stored_count : sessions.length;

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
              <th scope="col">State</th>
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
                    <td>{valueOrEmpty(session.session_state)}</td>
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
  const [voiceSessions, setVoiceSessions] = useState([]);
  const [historyStatus, setHistoryStatus] = useState(voiceStatus?.session_history || null);
  const [historyDetailSession, setHistoryDetailSession] = useState(null);
  const [historyDetailLoading, setHistoryDetailLoading] = useState(false);
  const [historyDetailError, setHistoryDetailError] = useState("");
  const [firmwareUpdateBusy, setFirmwareUpdateBusy] = useState(false);
  const projection = voiceStateProjection(voiceStatus);
  const endpointId = endpointStatus?.endpoint_id || voiceStatus?.endpoint_id || "";
  const reportedOutput = endpointCapabilities(endpointStatus).audio?.output || {};

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
        onPushFirmwareUpdate={handlePushFirmwareUpdate}
        firmwareUpdateBusy={firmwareUpdateBusy}
      />
      <EndpointCapabilitiesPanel endpointStatus={endpointStatus} />
      <EndpointMediaManagerPanel
        endpointId={endpointId}
        onRefresh={onRefresh}
        setActionMessage={setActionMessage}
      />
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
      <EndpointMetadataPanel
        voiceStatus={voiceStatus}
        endpointStatus={endpointStatus}
        onRefresh={onRefresh}
        setActionMessage={setActionMessage}
      />
    </section>
  );
}
