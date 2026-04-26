export function VoiceEndpointActionsCard({
  voiceStatus,
  onRefresh,
  onTestTurn,
  onStopSession,
  onReplayResponse,
  onMuteEndpoint,
  onSetVolume,
  volumePercent,
  onVolumeChange,
  muted,
  actionMessage,
}) {
  const actions = voiceStatus?.supported_actions || {};
  const commands = Array.isArray(voiceStatus?.commands) ? voiceStatus.commands.slice(-5).reverse() : [];

  function commandClass(status) {
    if (status === "succeeded") {
      return "status-pill-success";
    }
    if (status === "failed" || status === "timed_out" || status === "unsupported") {
      return "status-pill-danger";
    }
    return "status-pill-warning";
  }

  return (
    <section className="voice-endpoint-panel stack">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Endpoint Actions</p>
          <h2 className="panel-title">Voice Controls</h2>
        </div>
      </div>
      <div className="actions">
        <button className="btn btn-ghost" type="button" onClick={onRefresh}>
          Refresh endpoint
        </button>
        <button className="btn btn-ghost" type="button" onClick={onTestTurn}>
          Test assistant turn
        </button>
        <button className="btn btn-ghost" type="button" onClick={onStopSession} disabled={!actions.stop_session}>
          Stop session
        </button>
        <button className="btn btn-ghost" type="button" onClick={onReplayResponse} disabled={!actions.replay_response}>
          Replay response
        </button>
        <button className="btn btn-ghost" type="button" onClick={onMuteEndpoint} disabled={!actions.mute_endpoint}>
          {muted ? "Unmute endpoint" : "Mute endpoint"}
        </button>
        <button className="btn btn-ghost" type="button" disabled={!actions.reconnect}>
          Reconnect
        </button>
      </div>
      <div className="form-row endpoint-volume-control">
        <label className="field">
          <span className="field-label">Volume</span>
          <input
            type="range"
            min="0"
            max="100"
            step="1"
            value={volumePercent}
            onChange={(event) => onVolumeChange(Number(event.target.value))}
            disabled={!actions.set_volume}
          />
        </label>
        <span className="status-pill status-pill-neutral">{volumePercent}%</span>
        <button className="btn btn-ghost" type="button" onClick={onSetVolume} disabled={!actions.set_volume}>
          Set
        </button>
      </div>
      {commands.length ? (
        <div className="command-status-list">
          {commands.map((command) => (
            <div className="command-status-row" key={command.request_id}>
              <span>{command.command_type}</span>
              <span className={`status-pill ${commandClass(command.status)}`}>{command.status}</span>
            </div>
          ))}
        </div>
      ) : null}
      {actionMessage ? <div className="callout">{actionMessage}</div> : null}
    </section>
  );
}
