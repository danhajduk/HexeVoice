export function VoiceEndpointActionsCard({ voiceStatus, onRefresh, onTestTurn, onStopSession, actionMessage }) {
  const actions = voiceStatus?.supported_actions || {};
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
        <button className="btn btn-ghost" type="button" disabled={!actions.replay_response}>
          Replay response
        </button>
        <button className="btn btn-ghost" type="button" disabled={!actions.mute_endpoint}>
          Mute endpoint
        </button>
        <button className="btn btn-ghost" type="button" disabled={!actions.reconnect}>
          Reconnect
        </button>
      </div>
      {actionMessage ? <div className="callout">{actionMessage}</div> : null}
    </section>
  );
}
