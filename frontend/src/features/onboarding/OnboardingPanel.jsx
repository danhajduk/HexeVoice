import { useEffect, useRef, useState } from "react";
import {
  declareCapabilities,
  finalizeTrustActivation,
  getBootstrapDiscovery,
  getCapabilities,
  getLocalSetup,
  getOperationalStatus,
  getProviderSetup,
  getGovernanceCurrent,
  getVoiceIntents,
  pollOnboardingSession,
  registerVoiceIntent,
  refreshGovernance,
  reviewVoiceIntent,
  saveCoreConnection,
  saveCapabilitySelection,
  saveNodeIdentity,
  saveProviderSetup,
  startOnboardingSession,
  testBootstrapConnection,
  updateVoiceIntentLifecycle,
  validateBootstrapAdvertisement,
} from "../../api/client";
import { StageCard } from "../setup/cards/StageCard";
import { NodeIdentityFormCard } from "../setup/cards/NodeIdentityFormCard";
import { NodeSetupCard } from "../setup/cards/NodeSetupCard";

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

function emptyIdentityForm() {
  return {
    node_name: "",
    protocol_version: "1.0",
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

function generateNodeNonce() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `voice-node-${Math.random().toString(36).slice(2, 12)}`;
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

function emptyIntentForm() {
  return {
    intent_id: "",
    intent_name: "",
    command: "",
    example: "",
    reply_text: "",
  };
}

function automaticAdvertisementPayload(connectionForm, bootstrap, advertisementForm) {
  let parsedHost = "";
  let parsedApiBase = connectionForm.core_base_url || "";

  try {
    if (connectionForm.core_base_url) {
      const parsed = new URL(connectionForm.core_base_url);
      parsedHost = parsed.hostname;
      parsedApiBase = parsed.toString();
    }
  } catch {
    // Keep fallback values if the URL is temporarily invalid.
  }

  return {
    topic: advertisementForm.topic || "hexe/bootstrap/core",
    api_base: advertisementForm.api_base || bootstrap?.api_base || parsedApiBase || null,
    mqtt_host: advertisementForm.mqtt_host || bootstrap?.mqtt_host || bootstrap?.bootstrap_host || parsedHost || null,
    mqtt_port: Number(advertisementForm.mqtt_port || bootstrap?.mqtt_port || bootstrap?.bootstrap_port || 1884),
    onboarding_mode: advertisementForm.onboarding_mode || bootstrap?.onboarding_mode || "api",
    onboarding_contract: advertisementForm.onboarding_contract || bootstrap?.onboarding_contract || "global-node-v1",
    onboarding_endpoints: {
      register_session: advertisementForm.register_session || bootstrap?.register_session_endpoint || "/api/system/nodes/onboarding/sessions",
      registrations: advertisementForm.registrations || bootstrap?.registrations_endpoint || "/api/system/nodes/registrations",
    },
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
            <input className="field-input" value={identityForm.ui_endpoint} onChange={(event) => onIdentityChange("ui_endpoint", event.target.value)} placeholder="http://10.0.0.22:8084" />
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
          The saved Core host is being used automatically to establish MQTT reachability. This stage advances as soon
          as the node can connect to the Core bootstrap listener.
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Core base URL</span>
            <span className="fact-grid-value">{connectionForm.core_base_url || "not configured"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Connection status</span>
            <span className="fact-grid-value">{bootstrap?.connection_status || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">MQTT target</span>
            <span className="fact-grid-value">
              {(bootstrap?.bootstrap_host || "pending")}:{bootstrap?.bootstrap_port || 1884}
            </span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Bootstrap topic</span>
            <span className="fact-grid-value">{bootstrap?.bootstrap_topic || "hexe/bootstrap/core"}</span>
          </div>
        </div>
        <div className={`callout ${busyState === "bootstrap-test" ? "callout-warning" : "callout-neutral"}`}>
          {busyState === "bootstrap-test"
            ? "Connecting to the Core MQTT bootstrap listener..."
            : "Waiting for automatic Core connectivity verification."}
        </div>
        {localSetup?.core_connection?.configured ? (
          <div className="callout callout-success">
            Core connection details were carried forward from the node identity card.
          </div>
        ) : null}
      </>
    );
  }

  return (
    <>
      <div className="callout">
        Bootstrap discovery is running automatically. The node is validating the retained advertisement payload before
        registration can begin.
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
      <div className={`callout ${busyState === "bootstrap-validate" ? "callout-warning" : "callout-neutral"}`}>
        {busyState === "bootstrap-validate"
          ? "Validating the retained bootstrap advertisement..."
          : "Waiting for automatic bootstrap advertisement validation."}
      </div>
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
  providerSetup,
  capabilities,
  governanceCurrent,
  operationalStatus,
  providerForm,
  capabilityForm,
  onProviderToggle,
  onProviderSave,
  onCapabilityToggle,
  onCapabilitySave,
  onDeclareCapabilities,
  onGovernanceCurrent,
  onGovernanceRefresh,
  onOperationalPoll,
  voiceIntents,
  intentForm,
  onIntentFormChange,
  onIntentRegister,
  onIntentLifecycle,
  onIntentReview,
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
    const supportedProviders = providerSetup?.supported_providers || capabilitySetup?.provider_selection?.supported?.cloud || ["voice"];
    const enabledProviders = providerForm?.enabled_providers || providerSetup?.enabled_providers || capabilitySetup?.provider_selection?.enabled || [];
    const declarationStatus = capabilities?.capability_status || onboarding?.capability_status || "missing";
    const governanceVersion = governanceCurrent?.governance_version || onboarding?.active_governance_version || "pending";
    const readinessValue = operationalStatus?.operational_ready ?? onboarding?.operational_ready;
    const availableCapabilities = capabilitySetup?.task_capability_selection?.available || [];
    const selectedCapabilities = capabilityForm?.selected_capabilities || capabilities?.selected || capabilitySetup?.task_capability_selection?.selected || [];
    const declaredCapabilities = capabilities?.declared || [];
    const intents = voiceIntents?.intents || [];

    return (
      <>
        <div className="callout">
          The node is in post-trust setup. Provider readiness, capability declaration, governance sync, and final
          operational review now determine whether the node can become fully ready.
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Capability State</span>
            <span className="fact-grid-value">{declarationStatus}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Governance State</span>
            <span className="fact-grid-value">{onboarding?.governance_sync_status || "pending_capability"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Providers Enabled</span>
            <span className="fact-grid-value">{enabledProviders.join(", ") || "none"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Readiness</span>
            <span className="fact-grid-value">{readinessValue ? "operational" : "blocked"}</span>
          </div>
        </div>
        <div className="section-divider" />
        <div className="section-heading-inline">
          <div>
            <p className="panel-kicker">Provider Setup</p>
            <h3 className="section-title">Select enabled providers</h3>
          </div>
        </div>
        <div className="choice-list">
          {supportedProviders.map((providerId) => {
            const selected = enabledProviders.includes(providerId);
            return (
              <button
                key={providerId}
                className={`choice-card ${selected ? "choice-card-selected" : ""}`}
                type="button"
                onClick={() => onProviderToggle(providerId)}
              >
                <span className="choice-check">{selected ? "✓" : ""}</span>
                <span className="choice-copy">
                  <strong>{providerId}</strong>
                  <span>Enable this provider for capability declaration.</span>
                </span>
              </button>
            );
          })}
        </div>
        <FormActions
          busy={busyState === "provider-save"}
          busyLabel="Saving..."
          label="Save provider setup"
          onClick={onProviderSave}
        />
        <div className="section-divider" />
        <div className="callout">
          Declare the node manifest to Core once provider selection is complete. The backend uses the canonical
          task family and provider metadata already persisted locally.
        </div>
        <div className="choice-list">
          {availableCapabilities.map((capabilityId) => {
            const selected = selectedCapabilities.includes(capabilityId);
            const declared = declaredCapabilities.includes(capabilityId);
            return (
              <button
                key={capabilityId}
                className={`choice-card ${selected ? "choice-card-selected" : ""}`}
                type="button"
                onClick={() => onCapabilityToggle(capabilityId)}
              >
                <span className="choice-check">{selected ? "✓" : ""}</span>
                <span className="choice-copy">
                  <strong>{capabilityId}</strong>
                  <span>{declared ? "Currently declared in Core." : "Selected capabilities will be declared to Core."}</span>
                </span>
              </button>
            );
          })}
        </div>
        <FormActions
          busy={busyState === "capability-save"}
          busyLabel="Saving..."
          label="Save capability selection"
          onClick={onCapabilitySave}
        />
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Declared capabilities</span>
            <span className="fact-grid-value">{declaredCapabilities.join(", ") || "pending"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Capability profile</span>
            <span className="fact-grid-value">{capabilities?.capability_profile_id || "pending"}</span>
          </div>
        </div>
        <FormActions
          busy={busyState === "capability-declare"}
          busyLabel="Declaring..."
          label="Declare capabilities"
          onClick={onDeclareCapabilities}
        />
        <div className="section-divider" />
        <div className="section-heading-inline">
          <div>
            <p className="panel-kicker">Voice Intents</p>
            <h3 className="section-title">Registered intent controls</h3>
          </div>
          <span className="status-pill status-pill-neutral">
            {voiceIntents?.active_count ?? 0}/{voiceIntents?.registered_count ?? 0} active
          </span>
        </div>
        <div className="choice-list">
          {intents.map((intent) => {
            const active = intent.status === "active";
            const builtin = Boolean(intent.metadata?.builtin);
            return (
              <div key={intent.intent_id} className={`choice-card ${active ? "choice-card-selected" : ""}`}>
                <span className="choice-check">{active ? "✓" : ""}</span>
                <span className="choice-copy">
                  <strong>{intent.intent_id}</strong>
                  <span>
                    {(intent.intent_name || intent.service_id || "intent")} · {intent.version || "v1"} · {intent.status || "unknown"}
                    {builtin ? " · built in" : ""}
                  </span>
                </span>
                <div className="actions compact-actions">
                  <button
                    className="btn btn-secondary"
                    type="button"
                    onClick={() => onIntentLifecycle(intent.intent_id, active ? "disabled" : "active")}
                    disabled={busyState !== ""}
                  >
                    {active ? "Disable" : "Enable"}
                  </button>
                  <button
                    className="btn btn-ghost"
                    type="button"
                    onClick={() => onIntentReview(intent.intent_id)}
                    disabled={busyState !== ""}
                  >
                    Review
                  </button>
                </div>
              </div>
            );
          })}
        </div>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">Intent ID</span>
            <input className="field-input" value={intentForm.intent_id} onChange={(event) => onIntentFormChange("intent_id", event.target.value)} placeholder="kitchen.status" />
          </label>
          <label className="field">
            <span className="field-label">Intent name</span>
            <input className="field-input" value={intentForm.intent_name} onChange={(event) => onIntentFormChange("intent_name", event.target.value)} placeholder="Kitchen status" />
          </label>
          <label className="field">
            <span className="field-label">Command</span>
            <input className="field-input" value={intentForm.command} onChange={(event) => onIntentFormChange("command", event.target.value)} placeholder="kitchen.status" />
          </label>
          <label className="field">
            <span className="field-label">Example phrase</span>
            <input className="field-input" value={intentForm.example} onChange={(event) => onIntentFormChange("example", event.target.value)} placeholder="kitchen status" />
          </label>
          <label className="field field-span-2">
            <span className="field-label">Reply text</span>
            <input className="field-input" value={intentForm.reply_text} onChange={(event) => onIntentFormChange("reply_text", event.target.value)} placeholder="Kitchen status accepted." />
          </label>
        </div>
        <FormActions
          busy={busyState === "intent-register"}
          busyLabel="Registering..."
          label="Register intent"
          onClick={onIntentRegister}
        />
        <div className="section-divider" />
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Governance version</span>
            <span className="fact-grid-value">{governanceVersion}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Refresh interval</span>
            <span className="fact-grid-value">{governanceCurrent?.refresh_interval_s || "pending"}</span>
          </div>
        </div>
        <FormActions
          busy={busyState === "governance-refresh"}
          busyLabel="Refreshing..."
          label="Refresh governance"
          onClick={onGovernanceRefresh}
          secondaryLabel="Fetch current bundle"
          onSecondaryClick={onGovernanceCurrent}
          secondaryDisabled={busyState === "governance-current"}
        />
        {governanceCurrent?.governance_bundle ? (
          <pre className="code-panel">{JSON.stringify(governanceCurrent.governance_bundle, null, 2)}</pre>
        ) : null}
        <div className="section-divider" />
        <div className="callout callout-success">
          The node can now poll Core's operational-status projection to confirm end-to-end readiness and governance freshness.
        </div>
        <FormActions
          busy={busyState === "operational-poll"}
          busyLabel="Polling..."
          label="Poll operational status"
          onClick={onOperationalPoll}
        />
        {operationalStatus ? (
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">Freshness</span>
              <span className="fact-grid-value">{operationalStatus.governance_freshness_state || "pending"}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Governance version</span>
              <span className="fact-grid-value">{operationalStatus.active_governance_version || "pending"}</span>
            </div>
          </div>
        ) : null}
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
            Registration is being created automatically. Once the Core returns the registration link, you can open it
            directly from this stage.
          </div>
          <div className="fact-grid">
            <div className="fact-grid-item">
              <span className="fact-grid-label">Session ID</span>
              <span className="fact-grid-value">{onboarding?.session_id || "not started"}</span>
            </div>
            <div className="fact-grid-item">
              <span className="fact-grid-label">Registration Link</span>
              <span className="fact-grid-value stage-link">{onboarding?.approval_url || "will appear after link creation"}</span>
            </div>
          </div>
          {busyState === "session-start" ? (
            <div className="callout callout-warning">Creating registration link in Core...</div>
          ) : null}
          {onboarding?.approval_url ? (
            <FormActions
              busyLabel="Opening..."
              busy={false}
              label="Open Registration In Core"
              onClick={() => window.open(onboarding.approval_url, "_blank", "noopener,noreferrer")}
            />
          ) : null}
        </>
      ) : null}
    </>
  );
}

export function OnboardingPanel({ status, onboarding, onRefresh }) {
  const automaticStepRef = useRef("");
  const openedApprovalUrlRef = useRef("");
  const [localSetup, setLocalSetup] = useState(null);
  const [bootstrap, setBootstrap] = useState(null);
  const [identityForm, setIdentityForm] = useState(emptyIdentityForm);
  const [connectionForm, setConnectionForm] = useState(emptyConnectionForm);
  const [advertisementForm, setAdvertisementForm] = useState(emptyAdvertisementForm);
  const [providerSetup, setProviderSetup] = useState(null);
  const [capabilities, setCapabilities] = useState(null);
  const [governanceCurrent, setGovernanceCurrent] = useState(null);
  const [operationalStatus, setOperationalStatus] = useState(null);
  const [voiceIntents, setVoiceIntents] = useState(null);
  const [providerForm, setProviderForm] = useState({ enabled_providers: [], default_provider: "voice" });
  const [capabilityForm, setCapabilityForm] = useState({ selected_capabilities: [] });
  const [intentForm, setIntentForm] = useState(emptyIntentForm);
  const [busyState, setBusyState] = useState("");
  const [stageNotice, setStageNotice] = useState("");
  const [stageError, setStageError] = useState("");
  const requiredInputs = [
    !connectionForm.core_base_url ? "core_base_url" : null,
    !identityForm.node_name ? "node_name" : null,
  ].filter(Boolean);
  const nodeSetupVisible = Boolean(
    onboarding?.session_id ||
    onboarding?.approval_url ||
    (onboarding?.current_step_id && onboarding.current_step_id !== "node_identity") ||
    (status?.trust_state && status.trust_state !== "untrusted")
  );

  useEffect(() => {
    let mounted = true;
    Promise.all([
      getLocalSetup(),
      getBootstrapDiscovery(),
      getProviderSetup().catch(() => null),
      getCapabilities().catch(() => null),
      getVoiceIntents().catch(() => null),
    ])
      .then(([setupPayload, bootstrapPayload, providerPayload, capabilityPayload, intentPayload]) => {
        if (!mounted) {
          return;
        }
        setLocalSetup(setupPayload);
        setBootstrap(bootstrapPayload);
        setIdentityForm({
          node_name: setupPayload?.node_identity?.node_name || "",
          protocol_version: setupPayload?.node_identity?.protocol_version || "1.0",
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
        setProviderSetup(providerPayload);
        setCapabilities(capabilityPayload);
        setVoiceIntents(intentPayload);
        setProviderForm({
          enabled_providers: providerPayload?.enabled_providers || [],
          default_provider: providerPayload?.default_provider || providerPayload?.supported_providers?.[0] || "voice",
        });
        setCapabilityForm({
          selected_capabilities: capabilityPayload?.selected || capabilityPayload?.available || [],
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

  function updateProviderSelection(providerId) {
    setProviderForm((current) => {
      const enabled = current.enabled_providers.includes(providerId)
        ? current.enabled_providers.filter((item) => item !== providerId)
        : [...current.enabled_providers, providerId];
      return {
        enabled_providers: enabled,
        default_provider: enabled.includes(current.default_provider) ? current.default_provider : enabled[0] || "",
      };
    });
  }

  function updateCapabilitySelection(capabilityId) {
    setCapabilityForm((current) => {
      const selected = current.selected_capabilities.includes(capabilityId)
        ? current.selected_capabilities.filter((item) => item !== capabilityId)
        : [...current.selected_capabilities, capabilityId];
      return { selected_capabilities: selected };
    });
  }

  function updateIntentForm(field, value) {
    setIntentForm((current) => ({ ...current, [field]: value }));
  }

  async function refreshSetupPanels() {
    const [setupPayload, bootstrapPayload, providerPayload, capabilityPayload, intentPayload] = await Promise.all([
      getLocalSetup(),
      getBootstrapDiscovery(),
      getProviderSetup().catch(() => null),
      getCapabilities().catch(() => null),
      getVoiceIntents().catch(() => null),
    ]);
    setLocalSetup(setupPayload);
    setBootstrap(bootstrapPayload);
    setProviderSetup(providerPayload);
    setCapabilities(capabilityPayload);
    setVoiceIntents(intentPayload);
    if (providerPayload) {
      setProviderForm({
        enabled_providers: providerPayload.enabled_providers || [],
        default_provider: providerPayload.default_provider || providerPayload.supported_providers?.[0] || "voice",
      });
    }
    if (capabilityPayload) {
      setCapabilityForm({
        selected_capabilities: capabilityPayload.selected || capabilityPayload.available || [],
      });
    }
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

  async function handleSaveInitialSetup() {
    setBusyState("initial-save");
    setStageError("");
    setStageNotice("");
    try {
      await saveNodeIdentity(
        sanitizeOptionalFields({
          ...identityForm,
          protocol_version: identityForm.protocol_version || "1.0",
          node_nonce: identityForm.node_nonce || generateNodeNonce(),
        })
      );
      await saveCoreConnection(connectionForm);
      await refreshSetupPanels();
      setStageNotice("Node identity and Core connection saved.");
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

  async function handleAdvertisementValidation(payloadOverride = null) {
    setBusyState("bootstrap-validate");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await validateBootstrapAdvertisement(
        payloadOverride || {
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
        }
      );
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

  async function handleStartInitialOnboarding() {
    setBusyState("initial-start");
    setStageError("");
    setStageNotice("");
    try {
      await saveNodeIdentity(
        sanitizeOptionalFields({
          ...identityForm,
          protocol_version: identityForm.protocol_version || "1.0",
          node_nonce: identityForm.node_nonce || generateNodeNonce(),
        })
      );
      await saveCoreConnection(connectionForm);
      await refreshSetupPanels();
      setStageNotice("Initial setup saved. Core connection and bootstrap discovery will continue automatically.");
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

  async function handleProviderSave() {
    setBusyState("provider-save");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await saveProviderSetup(providerForm);
      setProviderSetup(payload);
      await refreshSetupPanels();
      setStageNotice("Provider setup saved.");
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleCapabilitySave() {
    setBusyState("capability-save");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await saveCapabilitySelection(capabilityForm);
      setCapabilities(payload);
      setCapabilityForm({
        selected_capabilities: payload.selected || [],
      });
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice("Capability selection saved. Re-declare capabilities to update Core.");
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleDeclareCapabilities() {
    setBusyState("capability-declare");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await declareCapabilities();
      setCapabilities((current) => ({
        ...(current || {}),
        capability_status: payload.capability_status,
        declared: payload.declared_capabilities,
        capability_profile_id: payload.capability_profile_id,
        accepted_at: payload.accepted_at,
        governance_version: payload.governance_version,
        selected: payload.declared_capabilities,
      }));
      setCapabilityForm({
        selected_capabilities: payload.declared_capabilities || [],
      });
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice(`Capability declaration ${payload.capability_status}.`);
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleIntentRegister() {
    const intentId = intentForm.intent_id.trim();
    const command = (intentForm.command || intentId).trim();
    const example = intentForm.example.trim();
    setBusyState("intent-register");
    setStageError("");
    setStageNotice("");
    try {
      if (!intentId || !command || !example) {
        throw new Error("intent_id, command, and example phrase are required");
      }
      const payload = await registerVoiceIntent({
        intent_id: intentId,
        intent_name: intentForm.intent_name.trim() || intentId,
        service_id: "voice.local_intents",
        owner_service: "operator",
        version: "v1",
        status: "active",
        definition: {
          utterance_examples: [example],
          dispatch: {
            type: "local_response",
            command,
          },
          response: {
            reply_text: intentForm.reply_text.trim() || `${intentId} accepted.`,
          },
          matcher: {
            type: "exact_example",
          },
        },
        metadata: {
          source: "setup_ui",
        },
      });
      setVoiceIntents(payload);
      setIntentForm(emptyIntentForm());
      await refreshSetupPanels();
      setStageNotice(`Intent ${intentId} registered.`);
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleIntentLifecycle(intentId, statusValue) {
    setBusyState(`intent-${statusValue}`);
    setStageError("");
    setStageNotice("");
    try {
      const payload = await updateVoiceIntentLifecycle(intentId, {
        status: statusValue,
        reason: "setup_ui",
      });
      setVoiceIntents(payload);
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice(`Intent ${intentId} moved to ${statusValue}.`);
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleIntentReview(intentId) {
    setBusyState("intent-review");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await reviewVoiceIntent(intentId, {
        reviewed_by: "setup_ui",
        review_reason: "operator_review",
        status: "active",
      });
      setVoiceIntents(payload);
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice(`Intent ${intentId} reviewed.`);
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleGovernanceCurrent() {
    setBusyState("governance-current");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await getGovernanceCurrent();
      setGovernanceCurrent(payload);
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice(`Loaded governance bundle ${payload.governance_version || "current"}.`);
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleGovernanceRefresh() {
    setBusyState("governance-refresh");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await refreshGovernance();
      if (payload?.governance_bundle || payload?.governance_version) {
        setGovernanceCurrent((current) => ({
          ...(current || {}),
          governance_bundle: payload.governance_bundle || current?.governance_bundle || {},
          governance_version: payload.governance_version || current?.governance_version || null,
          refresh_interval_s: payload.refresh_interval_s || current?.refresh_interval_s || null,
        }));
      }
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice(payload.updated ? "Governance bundle refreshed." : "Governance already current.");
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  async function handleOperationalPoll() {
    setBusyState("operational-poll");
    setStageError("");
    setStageNotice("");
    try {
      const payload = await getOperationalStatus();
      setOperationalStatus(payload);
      if (onRefresh) {
        await onRefresh();
      }
      setStageNotice(payload.operational_ready ? "Node is operationally ready." : "Operational readiness still blocked.");
    } catch (error) {
      setStageError(String(error.message || error));
    } finally {
      setBusyState("");
    }
  }

  useEffect(() => {
    const stepId = onboarding?.current_step_id;

    if (!stepId || busyState) {
      return;
    }

    if (stepId === "core_connection" && connectionForm.core_base_url) {
      const autoKey = `core_connection:${connectionForm.core_base_url}`;
      if (automaticStepRef.current === autoKey) {
        return;
      }
      automaticStepRef.current = autoKey;
      setStageNotice("Automatically connecting to the Core bootstrap listener...");
      handleBootstrapTest();
      return;
    }

    if (
      stepId === "bootstrap_discovery" &&
      connectionForm.core_base_url &&
      (bootstrap?.connection_status === "bootstrap_connected" || bootstrap?.connection_status === "core_discovered") &&
      !bootstrap?.advertisement_valid
    ) {
      const autoKey = `bootstrap_discovery:${connectionForm.core_base_url}:${bootstrap?.connection_status}`;
      if (automaticStepRef.current === autoKey) {
        return;
      }
      automaticStepRef.current = autoKey;
      setStageNotice("Automatically validating the Core bootstrap advertisement...");
      handleAdvertisementValidation(automaticAdvertisementPayload(connectionForm, bootstrap, advertisementForm));
      return;
    }

    if (stepId === "registration" && !onboarding?.approval_url) {
      const autoKey = `registration:${connectionForm.core_base_url}:${identityForm.node_nonce}`;
      if (automaticStepRef.current === autoKey) {
        return;
      }
      automaticStepRef.current = autoKey;
      setStageNotice("Automatically creating registration link in Core...");
      handleSessionStart();
      return;
    }

    if (stepId !== "core_connection" && stepId !== "bootstrap_discovery" && stepId !== "registration") {
      automaticStepRef.current = "";
    }
  }, [onboarding?.current_step_id, onboarding?.approval_url, connectionForm.core_base_url, identityForm.node_nonce, bootstrap, advertisementForm, busyState]);

  useEffect(() => {
    if (onboarding?.current_step_id !== "approval") {
      return;
    }
    if (!onboarding?.session_id || busyState) {
      return;
    }

    const timer = window.setTimeout(() => {
      setStageNotice("Checking Core for approval finalization...");
      handleSessionPoll();
    }, 3000);

    return () => window.clearTimeout(timer);
  }, [onboarding?.current_step_id, onboarding?.session_id, onboarding?.session_state, busyState]);

  useEffect(() => {
    if (onboarding?.current_step_id !== "trust_activation") {
      return;
    }
    if (busyState) {
      return;
    }

    const autoKey = `trust_activation:${onboarding?.session_id || ""}:${status?.node_id || ""}`;
    if (automaticStepRef.current === autoKey) {
      return;
    }
    automaticStepRef.current = autoKey;

    const timer = window.setTimeout(() => {
      setStageNotice("Applying trust activation automatically...");
      handleTrustFinalize();
    }, 500);

    return () => window.clearTimeout(timer);
  }, [onboarding?.current_step_id, onboarding?.session_id, status?.node_id, busyState]);

  useEffect(() => {
    const approvalUrl = onboarding?.approval_url;
    if (!approvalUrl || typeof window === "undefined") {
      return;
    }
    if (onboarding?.current_step_id !== "registration" && onboarding?.current_step_id !== "approval") {
      return;
    }
    if (openedApprovalUrlRef.current === approvalUrl) {
      return;
    }
    openedApprovalUrlRef.current = approvalUrl;
    window.open(approvalUrl, "_blank", "noopener,noreferrer");
    setStageNotice("Registration link created and opened in a new window. Continue approval in Core.");
  }, [onboarding?.approval_url, onboarding?.current_step_id]);

  const tone = toneForStep(onboarding?.current_step_id, status?.trust_state, status?.operational_ready);
  const action = onboarding?.current_step_id === "registration" ? (
    onboarding?.approval_url ? (
      <a className="status-pill status-pill-warning" href={onboarding.approval_url} target="_blank" rel="noreferrer">
        Open registration
      </a>
    ) : (
      <span className="status-pill status-pill-neutral">
        {busyState === "session-start" ? "creating_registration" : "waiting_for_registration"}
      </span>
    )
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

  if (!nodeSetupVisible) {
    return (
      <NodeIdentityFormCard
        uiPort={8084}
        form={{ core_base_url: connectionForm.core_base_url, node_name: identityForm.node_name }}
        handleChange={(field, value) => {
          if (field === "core_base_url") {
            updateConnection("core_base_url", value);
            return;
          }
          updateIdentity(field, value);
        }}
        saveConfiguration={handleSaveInitialSetup}
        saving={busyState === "initial-save"}
        startOnboarding={handleStartInitialOnboarding}
        starting={busyState === "initial-start"}
        requiredInputs={requiredInputs}
        notice={stageNotice}
        error={stageError}
      />
    );
  }

  return (
    <NodeSetupCard apiPort={9004} onboarding={onboarding} status={status} statusTone={outcomeTone}>
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
          providerSetup,
          capabilities,
          governanceCurrent,
          operationalStatus,
          providerForm,
          capabilityForm,
          onProviderToggle: updateProviderSelection,
          onProviderSave: handleProviderSave,
          onCapabilityToggle: updateCapabilitySelection,
          onCapabilitySave: handleCapabilitySave,
          onDeclareCapabilities: handleDeclareCapabilities,
          onGovernanceCurrent: handleGovernanceCurrent,
          onGovernanceRefresh: handleGovernanceRefresh,
          onOperationalPoll: handleOperationalPoll,
          voiceIntents,
          intentForm,
          onIntentFormChange: updateIntentForm,
          onIntentRegister: handleIntentRegister,
          onIntentLifecycle: handleIntentLifecycle,
          onIntentReview: handleIntentReview,
        })}
      </StageCard>
    </NodeSetupCard>
  );
}
