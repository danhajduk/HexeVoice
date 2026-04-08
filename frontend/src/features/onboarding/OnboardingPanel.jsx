import { useEffect, useState } from "react";
import {
  finalizeTrustActivation,
  getBootstrapDiscovery,
  getLocalSetup,
  pollOnboardingSession,
  saveCoreConnection,
  saveNodeIdentity,
  startOnboardingSession,
  testBootstrapConnection,
  validateBootstrapAdvertisement,
} from "../../api/client";

function toneForStep(stepId, trustState, operationalReady) {
  if (operationalReady || stepId === "ready") {
    return "success";
  }
  if (trustState === "revoked") {
    return "danger";
  }
  if (stepId === "approval" || stepId === "trust_activation" || stepId === "governance_sync") {
    return "warning";
  }
  return "neutral";
}

function StageCard({ title, tone, action, children }) {
  return (
    <article className={`stage-card stage-tone-${tone}`}>
      <div className="stage-card-header">
        <div>
          <p className="panel-kicker">Current Stage</p>
          <h2 className="panel-title">{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </article>
  );
}

function emptyIdentityForm() {
  return {
    node_name: "",
    protocol_version: "global-node-v1",
    node_nonce: "",
    requested_node_id: "",
    hostname: "",
    ui_endpoint: "",
    api_base_url: "",
  };
}

function emptyConnectionForm() {
  return {
    core_base_url: "",
  };
}

function emptyAdvertisementForm() {
  return {
    topic: "hexe/bootstrap/core",
    api_base: "",
    mqtt_host: "",
    mqtt_port: 1884,
    onboarding_mode: "api",
    onboarding_contract: "global-node-v1",
    register_session: "/api/system/nodes/onboarding/sessions",
    registrations: "/api/system/nodes/registrations",
  };
}

function sanitizeOptionalFields(payload) {
  return Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== "" && value !== null && value !== undefined),
  );
}

function FormActions({ busyLabel, busy, label, onClick, secondaryLabel, onSecondaryClick, secondaryDisabled }) {
  return (
    <div className="form-actions">
      <button className="btn btn-primary" type="button" onClick={onClick} disabled={busy}>
        {busy ? busyLabel : label}
      </button>
      {secondaryLabel ? (
        <button className="btn btn-secondary" type="button" onClick={onSecondaryClick} disabled={secondaryDisabled}>
          {secondaryLabel}
        </button>
      ) : null}
    </div>
  );
}

function outcomeTone(sessionState) {
  if (sessionState === "approved") {
    return "success";
  }
  if (["rejected", "expired", "invalid", "consumed"].includes(sessionState)) {
    return "danger";
  }
  if (sessionState === "pending") {
    return "warning";
  }
  return "neutral";
}

function renderPreTrustStage({
  onboarding,
  localSetup,
  bootstrap,
  identityForm,
  connectionForm,
  advertisementForm,
  busyState,
  stageError,
  stageNotice,
  onIdentityChange,
  onConnectionChange,
  onAdvertisementChange,
  onSaveIdentity,
  onSaveConnection,
  onTestBootstrap,
  onValidateAdvertisement,
}) {
  const stepId = onboarding?.current_step_id || "node_identity";

  if (stepId === "node_identity") {
    return (
      <>
        <div className="callout">
          Define the node-local identity first. These values seed the onboarding session Core will later approve.
        </div>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">Node name</span>
            <input className="field-input" value={identityForm.node_name} onChange={(event) => onIdentityChange("node_name", event.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Protocol version</span>
            <input className="field-input" value={identityForm.protocol_version} onChange={(event) => onIdentityChange("protocol_version", event.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Node nonce</span>
            <input className="field-input" value={identityForm.node_nonce} onChange={(event) => onIdentityChange("node_nonce", event.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Requested node ID</span>
            <input className="field-input" value={identityForm.requested_node_id} onChange={(event) => onIdentityChange("requested_node_id", event.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Hostname</span>
            <input className="field-input" value={identityForm.hostname} onChange={(event) => onIdentityChange("hostname", event.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">API base URL</span>
            <input className="field-input" value={identityForm.api_base_url} onChange={(event) => onIdentityChange("api_base_url", event.target.value)} placeholder="http://10.0.0.22:9000" />
          </label>
          <label className="field field-span-2">
            <span className="field-label">UI endpoint</span>
            <input className="field-input" value={identityForm.ui_endpoint} onChange={(event) => onIdentityChange("ui_endpoint", event.target.value)} placeholder="http://10.0.0.22:8080" />
          </label>
        </div>
        <FormActions busy={busyState === "identity"} busyLabel="Saving..." label="Save node identity" onClick={onSaveIdentity} />
        {localSetup?.node_identity?.configured ? (
          <div className="callout callout-success">Node identity has been saved locally and will resume after restart.</div>
        ) : null}
      </>
    );
  }

  if (stepId === "core_connection") {
    return (
      <>
        <div className="callout">
          Set the Core base URL that this node should use for bootstrap, session start, trust-state refresh, and post-trust readiness APIs.
        </div>
        <label className="field">
          <span className="field-label">Core base URL</span>
          <input className="field-input" value={connectionForm.core_base_url} onChange={(event) => onConnectionChange("core_base_url", event.target.value)} placeholder="http://10.0.0.100:9001" />
        </label>
        <FormActions busy={busyState === "connection"} busyLabel="Saving..." label="Save Core connection" onClick={onSaveConnection} />
        {localSetup?.core_connection?.configured ? (
          <div className="callout callout-success">Core connection is configured and ready for bootstrap discovery.</div>
        ) : null}
      </>
    );
  }

  return (
    <>
      <div className="callout">
        Bootstrap discovery validates network reachability and the retained Core onboarding advertisement before session registration begins.
      </div>
      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Connection status</span>
          <span className="fact-grid-value">{bootstrap?.connection_status || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Advertisement valid</span>
          <span className="fact-grid-value">{String(bootstrap?.advertisement_valid ?? false)}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Bootstrap topic</span>
          <span className="fact-grid-value">{bootstrap?.bootstrap_topic || "hexe/bootstrap/core"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Bootstrap listener</span>
          <span className="fact-grid-value">{bootstrap?.bootstrap_host || "pending"}:{bootstrap?.bootstrap_port || 1884}</span>
        </div>
      </div>
      <FormActions
        busy={busyState === "bootstrap-test"}
        busyLabel="Testing..."
        label="Test bootstrap connection"
        onClick={onTestBootstrap}
        secondaryLabel="Refresh validation"
        onSecondaryClick={onValidateAdvertisement}
        secondaryDisabled={busyState === "bootstrap-validate"}
      />
      <div className="section-divider" />
      <div className="section-heading-inline">
        <div>
          <p className="panel-kicker">Advertisement Inspection</p>
          <h3 className="section-title">Validate retained bootstrap payload</h3>
        </div>
        <span className="status-pill status-pill-neutral">{bootstrap?.onboarding_contract || "global-node-v1"}</span>
      </div>
      <div className="form-grid">
        <label className="field">
          <span className="field-label">Topic</span>
          <input className="field-input" value={advertisementForm.topic} onChange={(event) => onAdvertisementChange("topic", event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">API base</span>
          <input className="field-input" value={advertisementForm.api_base} onChange={(event) => onAdvertisementChange("api_base", event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">MQTT host</span>
          <input className="field-input" value={advertisementForm.mqtt_host} onChange={(event) => onAdvertisementChange("mqtt_host", event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">MQTT port</span>
          <input className="field-input" type="number" value={advertisementForm.mqtt_port} onChange={(event) => onAdvertisementChange("mqtt_port", Number(event.target.value || 0))} />
        </label>
        <label className="field">
          <span className="field-label">Onboarding mode</span>
          <input className="field-input" value={advertisementForm.onboarding_mode} onChange={(event) => onAdvertisementChange("onboarding_mode", event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Onboarding contract</span>
          <input className="field-input" value={advertisementForm.onboarding_contract} onChange={(event) => onAdvertisementChange("onboarding_contract", event.target.value)} />
        </label>
        <label className="field field-span-2">
          <span className="field-label">Register-session endpoint</span>
          <input className="field-input" value={advertisementForm.register_session} onChange={(event) => onAdvertisementChange("register_session", event.target.value)} />
        </label>
        <label className="field field-span-2">
          <span className="field-label">Registrations endpoint</span>
          <input className="field-input" value={advertisementForm.registrations} onChange={(event) => onAdvertisementChange("registrations", event.target.value)} />
        </label>
      </div>
      <FormActions busy={busyState === "bootstrap-validate"} busyLabel="Validating..." label="Validate advertisement" onClick={onValidateAdvertisement} />
    </>
  );
}

function renderStageBody({
  status,
  onboarding,
  localSetup,
  bootstrap,
  identityForm,
  connectionForm,
  advertisementForm,
  busyState,
  stageError,
  stageNotice,
  onIdentityChange,
  onConnectionChange,
  onAdvertisementChange,
  onSaveIdentity,
  onSaveConnection,
  onTestBootstrap,
  onValidateAdvertisement,
  onStartSession,
  onPollSession,
  onFinalizeTrustActivation,
}) {
  const stepId = onboarding?.current_step_id || "node_identity";
  const capabilitySetup = onboarding?.capability_setup;
  const blockers = capabilitySetup?.blocking_reasons || status?.blocking_reasons || [];

  if (stepId === "node_identity" || stepId === "core_connection" || stepId === "bootstrap_discovery") {
    return (
      <>
        {renderPreTrustStage({
          onboarding,
          localSetup,
          bootstrap,
          identityForm,
          connectionForm,
          advertisementForm,
          busyState,
          stageError,
          stageNotice,
          onIdentityChange,
          onConnectionChange,
          onAdvertisementChange,
          onSaveIdentity,
          onSaveConnection,
          onTestBootstrap,
          onValidateAdvertisement,
        })}
        {bootstrap?.last_error ? <div className="callout callout-danger">{bootstrap.last_error}</div> : null}
      </>
    );
  }

  if (stepId === "approval") {
    return (
      <>
        <div className="callout callout-warning">
          Operator approval is pending in Core. Open the approval link and keep this page visible while polling and
          finalization continue.
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Approval URL</span>
            <span className="fact-grid-value stage-link">{onboarding?.approval_url || "Waiting for session start"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Session State</span>
            <span className="fact-grid-value">{onboarding?.session_state || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Expires At</span>
            <span className="fact-grid-value">{onboarding?.expires_at || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Last Outcome</span>
            <span className="fact-grid-value">{onboarding?.last_terminal_outcome || "none"}</span>
          </div>
        </div>
        <FormActions
          busy={busyState === "approval-poll"}
          busyLabel="Polling..."
          label="Poll approval state"
          onClick={onPollSession}
          secondaryLabel="Start over"
          onSecondaryClick={onStartSession}
          secondaryDisabled={busyState !== ""}
        />
        {["rejected", "expired", "invalid", "consumed"].includes(onboarding?.session_state) ? (
          <div className="callout callout-danger">
            Finalize outcome: {onboarding.session_state}. Start a fresh onboarding session after reviewing the current approval state in Core.
          </div>
        ) : null}
      </>
    );
  }

  if (stepId === "trust_activation") {
    return (
      <>
        <div className="callout">
          Approval has been granted. The node now needs to consume and persist the trust activation payload exactly
          once before post-trust setup can begin.
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Trust State</span>
            <span className="fact-grid-value">{status?.trust_state || "untrusted"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Next Action</span>
            <span className="fact-grid-value">{onboarding?.next_action || "finalize_trust_activation"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Session Outcome</span>
            <span className="fact-grid-value">{onboarding?.session_state || "approved"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Node ID</span>
            <span className="fact-grid-value">{status?.node_id || "Pending activation"}</span>
          </div>
        </div>
        <FormActions
          busy={busyState === "trust-finalize"}
          busyLabel="Finalizing..."
          label="Finalize trust activation"
          onClick={onFinalizeTrustActivation}
          secondaryLabel="Poll approval again"
          onSecondaryClick={onPollSession}
          secondaryDisabled={busyState !== ""}
        />
      </>
    );
  }

  if (stepId === "provider_setup" || stepId === "capability_declaration" || stepId === "governance_sync" || stepId === "ready") {
    return (
      <>
        <div className="callout">
          The node is in post-trust setup. Provider readiness, capability declaration, governance sync, and final
          operational review now determine whether the node can become fully ready.
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Capability State</span>
            <span className="fact-grid-value">{onboarding?.capability_status || "missing"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Governance State</span>
            <span className="fact-grid-value">{onboarding?.governance_sync_status || "pending_capability"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Providers Enabled</span>
            <span className="fact-grid-value">
              {capabilitySetup?.provider_selection?.enabled?.join(", ") || "none"}
            </span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Readiness</span>
            <span className="fact-grid-value">{onboarding?.operational_ready ? "operational" : "blocked"}</span>
          </div>
        </div>
        {blockers.length > 0 ? (
          <div className="callout callout-warning">
            Blocking reasons: {blockers.join(", ")}
          </div>
        ) : null}
      </>
    );
  }

  return (
    <>
      <div className="callout">
        This node is still in pre-trust onboarding. Local identity, Core connectivity, bootstrap discovery, and
        registration determine when approval can begin.
      </div>
      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Onboarding State</span>
          <span className="fact-grid-value">{onboarding?.onboarding_state || "waiting_for_local_setup"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Next Action</span>
          <span className="fact-grid-value">{onboarding?.next_action || "configure_node_identity"}</span>
        </div>
      </div>
      {stepId === "registration" ? (
        <>
          <div className={`callout callout-${outcomeTone(onboarding?.session_state)}`}>
            Registration creates the Core onboarding session and returns the approval URL the operator will use.
          </div>
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">Session ID</span>
              <span className="fact-grid-value">{onboarding?.session_id || "not started"}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Approval URL</span>
              <span className="fact-grid-value stage-link">{onboarding?.approval_url || "will appear after start"}</span>
            </div>
          </div>
          <FormActions
            busy={busyState === "session-start"}
            busyLabel="Starting..."
            label="Start onboarding session"
            onClick={onStartSession}
          />
        </>
      ) : null}
    </>
  );
}

export function OnboardingPanel({ status, onboarding, onRefresh }) {
  const [localSetup, setLocalSetup] = useState(null);
  const [bootstrap, setBootstrap] = useState(null);
  const [identityForm, setIdentityForm] = useState(emptyIdentityForm);
  const [connectionForm, setConnectionForm] = useState(emptyConnectionForm);
  const [advertisementForm, setAdvertisementForm] = useState(emptyAdvertisementForm);
  const [busyState, setBusyState] = useState("");
  const [stageNotice, setStageNotice] = useState("");
  const [stageError, setStageError] = useState("");

  useEffect(() => {
    let mounted = true;
    Promise.all([getLocalSetup(), getBootstrapDiscovery()])
      .then(([setupPayload, bootstrapPayload]) => {
        if (!mounted) {
          return;
        }
        setLocalSetup(setupPayload);
        setBootstrap(bootstrapPayload);
        setIdentityForm({
          node_name: setupPayload?.node_identity?.node_name || "",
          protocol_version: setupPayload?.node_identity?.protocol_version || "global-node-v1",
          node_nonce: setupPayload?.node_identity?.node_nonce || "",
          requested_node_id: setupPayload?.node_identity?.requested_node_id || "",
          hostname: setupPayload?.node_identity?.hostname || "",
          ui_endpoint: setupPayload?.node_identity?.ui_endpoint || "",
          api_base_url: setupPayload?.node_identity?.api_base_url || "",
        });
        setConnectionForm({
          core_base_url: setupPayload?.core_connection?.core_base_url || "",
        });
        setAdvertisementForm({
          topic: bootstrapPayload?.bootstrap_topic || "hexe/bootstrap/core",
          api_base: bootstrapPayload?.api_base || "",
          mqtt_host: bootstrapPayload?.mqtt_host || bootstrapPayload?.bootstrap_host || "",
          mqtt_port: bootstrapPayload?.mqtt_port || bootstrapPayload?.bootstrap_port || 1884,
          onboarding_mode: bootstrapPayload?.onboarding_mode || "api",
          onboarding_contract: bootstrapPayload?.onboarding_contract || "global-node-v1",
          register_session: bootstrapPayload?.register_session_endpoint || "/api/system/nodes/onboarding/sessions",
          registrations: bootstrapPayload?.registrations_endpoint || "/api/system/nodes/registrations",
        });
      })
      .catch((error) => {
        if (mounted) {
          setStageError(String(error.message || error));
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  function updateIdentity(field, value) {
    setIdentityForm((current) => ({ ...current, [field]: value }));
  }

  function updateConnection(field, value) {
    setConnectionForm((current) => ({ ...current, [field]: value }));
  }

  function updateAdvertisement(field, value) {
    setAdvertisementForm((current) => ({ ...current, [field]: value }));
  }

  async function refreshSetupPanels() {
    const [setupPayload, bootstrapPayload] = await Promise.all([getLocalSetup(), getBootstrapDiscovery()]);
    setLocalSetup(setupPayload);
    setBootstrap(bootstrapPayload);
    if (onRefresh) {
      await onRefresh();
    }
  }

  async function handleSaveIdentity() {
    setBusyState("identity");
    setStageError("");
    setStageNotice("");
    try {
      await saveNodeIdentity(sanitizeOptionalFields(identityForm));
      await refreshSetupPanels();
      setStageNotice("Node identity saved.");
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleSaveConnection() {
    setBusyState("connection");
    setStageError("");
    setStageNotice("");
    try {
      await saveCoreConnection(connectionForm);
      await refreshSetupPanels();
      setStageNotice("Core connection saved.");
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleBootstrapTest() {
    setBusyState("bootstrap-test");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await testBootstrapConnection();
      setBootstrap(payload);
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice("Bootstrap connection test completed.");
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleAdvertisementValidation() {
    setBusyState("bootstrap-validate");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await validateBootstrapAdvertisement({
        topic: advertisementForm.topic,
        api_base: advertisementForm.api_base || null,
        mqtt_host: advertisementForm.mqtt_host || null,
        mqtt_port: Number(advertisementForm.mqtt_port || 0) || null,
        onboarding_mode: advertisementForm.onboarding_mode || null,
        onboarding_contract: advertisementForm.onboarding_contract || null,
        onboarding_endpoints: {
          register_session: advertisementForm.register_session,
          registrations: advertisementForm.registrations,
        },
      });
      setBootstrap(payload);
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice(payload.advertisement_valid ? "Bootstrap advertisement validated." : "Bootstrap advertisement stored.");
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleSessionStart() {
    setBusyState("session-start");
    setStageError("");
    setStageNotice("");
    try {
      await startOnboardingSession();
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice("Onboarding session started. Share the approval URL with the operator.");
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleSessionPoll() {
    setBusyState("approval-poll");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await pollOnboardingSession();
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice(`Finalize outcome: ${payload.session_state}.`);
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleTrustFinalize() {
    setBusyState("trust-finalize");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await finalizeTrustActivation();
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice(`Trust activation completed for ${payload.node_id}.`);
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  const tone = toneForStep(onboarding?.current_step_id, status?.trust_state, status?.operational_ready);
  const action = onboarding?.current_step_id === "registration" ? (
    <button className="btn btn-primary" type="button" onClick={handleSessionStart} disabled={busyState !== ""}>
      {busyState === "session-start" ? "Starting..." : "Start session"}
    </button>
  ) : onboarding?.approval_url ? (
    <a className="status-pill status-pill-warning" href={onboarding.approval_url} target="_blank" rel="noreferrer">
      Open approval
    </a>
  ) : onboarding?.current_step_id === "trust_activation" ? (
    <button className="btn btn-primary" type="button" onClick={handleTrustFinalize} disabled={busyState !== ""}>
      {busyState === "trust-finalize" ? "Finalizing..." : "Finalize trust"}
    </button>
  ) : (
    <span className="status-pill status-pill-neutral">{onboarding?.next_action || "follow setup flow"}</span>
  );

  return (
    <StageCard title={onboarding?.current_step_label || "Node Identity"} tone={tone} action={action}>
      {stageNotice ? <div className="callout callout-success">{stageNotice}</div> : null}
      {stageError ? <div className="callout callout-danger">{stageError}</div> : null}
      {renderStageBody({
        status,
        onboarding,
        localSetup,
        bootstrap,
        identityForm,
        connectionForm,
        advertisementForm,
        busyState,
        stageError,
        stageNotice,
        onIdentityChange: updateIdentity,
        onConnectionChange: updateConnection,
        onAdvertisementChange: updateAdvertisement,
        onSaveIdentity: handleSaveIdentity,
        onSaveConnection: handleSaveConnection,
        onTestBootstrap: handleBootstrapTest,
        onValidateAdvertisement: handleAdvertisementValidation,
        onStartSession: handleSessionStart,
        onPollSession: handleSessionPoll,
        onFinalizeTrustActivation: handleTrustFinalize,
      })}
    </StageCard>
  );
}
