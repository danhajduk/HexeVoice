import { useEffect, useState } from "react";
import { getSetupHostReadiness, runSetupHostReadinessAction, saveNodeIdentity } from "../../api/client";

const setupModes = [
  { value: "new_node", label: "New Voice Node" },
  { value: "migrate_existing", label: "Migrate Existing" },
];

const lifecycleModes = [
  { value: "existing_supervisor", label: "Use Supervisor" },
  { value: "joined_supervisor", label: "Join Core Supervisor" },
  { value: "standalone_supervisor", label: "Standalone Supervisor" },
  { value: "unsupervised_systemd", label: "Unsupervised systemd" },
];

const defaultAssetChecks = [
  { id: "stt_model", action: "download-default-stt-model", label: "STT base model", button: "Download STT" },
  { id: "tts_model", action: "download-default-tts-model", label: "Piper Kathleen voice", button: "Download TTS" },
  { id: "wake_model", action: "download-default-wake-model", label: "Hexe wake model", button: "Prepare wake" },
  { id: "firmware", action: "download-firmware", label: "Firmware artifacts", button: "Download FW" },
];

const recoveryActions = [
  { action: "redetect-lan-ip", label: "Re-detect LAN" },
  { action: "recheck-supervisor", label: "Re-check Supervisor" },
  { action: "restart-production-services", label: "Restart production" },
  { action: "rerun-supervisor-registration", label: "Retry registration" },
  { action: "rebuild-systemd-services", label: "Rebuild services" },
  { action: "restart-temporary-services", label: "Restart temp UI" },
];

function defaultSupervisorId(readiness) {
  const hostname = readiness?.hostname || "hexevoice";
  return `${hostname}-hexe-supervisor`;
}

function enrollmentTokenApiUrl(form, readiness) {
  const base = normalizedCoreBaseUrl(form.core_base_url);
  if (!base && readiness?.enrollment_token_url) {
    return readiness.enrollment_token_url;
  }
  return base ? `${base.replace(/\/$/, "")}/api/system/supervisors/enrollment-tokens` : "";
}

function normalizedCoreBaseUrl(raw) {
  const trimmed = String(raw || "").trim();
  if (!trimmed) {
    return "";
  }
  try {
    const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
    const url = new URL(withScheme);
    if (!url.port && url.protocol === "http:") {
      url.port = "9001";
    }
    url.pathname = url.pathname.replace(/\/$/, "");
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return trimmed.replace(/\/$/, "");
  }
}

function generateNodeNonce() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `voice-node-${Math.random().toString(36).slice(2, 12)}`;
}

function optionalField(value) {
  const trimmed = String(value || "").trim();
  return trimmed || undefined;
}

function enrollmentPageUrl(form, readiness) {
  const baseUrl = normalizedCoreBaseUrl(form.core_base_url);
  const supervisorId = (form.supervisor_id || defaultSupervisorId(readiness)).trim();
  if (!baseUrl) {
    return "";
  }
  const base = `${baseUrl}/system/supervisors/enrollment`;
  try {
    const url = new URL(base);
    if (supervisorId) {
      url.searchParams.set("supervisor_id", supervisorId);
      url.searchParams.set("supervisor_name", supervisorId);
    }
    url.searchParams.set("return_url", window.location.href);
    return url.toString();
  } catch {
    return base;
  }
}

function coreUrlWarning(form, readiness) {
  const raw = form.core_base_url?.trim();
  if (!raw) {
    return "Enter the Core host URL, for example http://10.0.0.100:9001.";
  }
  const normalized = normalizedCoreBaseUrl(raw);
  try {
    const url = new URL(normalized);
    const host = url.hostname.toLowerCase();
    const nodeHosts = [readiness?.hostname, readiness?.lan_host, window.location.hostname]
      .filter(Boolean)
      .map((item) => String(item).toLowerCase());
    if (nodeHosts.includes(host)) {
      return "This Core URL points at this Voice node. Use the Core host address, not the node address.";
    }
    if (normalized !== raw.replace(/\/$/, "")) {
      return `Using normalized Core URL: ${normalized}`;
    }
  } catch {
    return "Core URL is not a valid URL.";
  }
  return "";
}

export function HostSetupPage({ readiness, onReadinessChange, onRefreshReadiness, onContinue }) {
  const [localReadiness, setLocalReadiness] = useState(null);
  const [form, setForm] = useState({
    setup_mode: "new_node",
    lifecycle_mode: "unsupervised_systemd",
    core_base_url: "",
    supervisor_id: "",
    enrollment_token: "",
    node_name: "",
    node_type: "voice-node",
    requested_node_id: "",
    hostname: "",
    lan_host: "",
    api_base_url: "",
    ui_endpoint: "",
    protocol_version: "1.0",
    node_nonce: "",
  });
  const [busyAction, setBusyAction] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [readinessSynced, setReadinessSynced] = useState(false);
  const activeReadiness = readiness || localReadiness;

  function syncFormFromReadiness(payload) {
    const identity = payload.node_identity || {};
    setReadinessSynced(true);
    setForm((current) => ({
      ...current,
      setup_mode: payload.setup_mode || current.setup_mode,
      lifecycle_mode: payload.lifecycle_mode || current.lifecycle_mode,
      core_base_url: current.core_base_url || payload.core_base_url || "",
      supervisor_id: current.supervisor_id || defaultSupervisorId(payload),
      node_name: current.node_name || identity.node_name || payload.hostname || "",
      node_type: identity.node_type || current.node_type || "voice-node",
      requested_node_id: current.requested_node_id || identity.requested_node_id || "",
      hostname: current.hostname || identity.hostname || payload.hostname || "",
      lan_host: identity.lan_host || payload.lan_host || current.lan_host || "",
      api_base_url: current.api_base_url || identity.api_base_url || payload.api_base_url || "",
      ui_endpoint: current.ui_endpoint || identity.ui_endpoint || payload.ui_base_url || "",
      protocol_version: current.protocol_version || identity.protocol_version || "1.0",
      node_nonce: current.node_nonce || identity.node_nonce || "",
    }));
  }

  function applyReadiness(payload) {
    setLocalReadiness(payload);
    onReadinessChange?.(payload);
    syncFormFromReadiness(payload);
  }

  async function refresh() {
    const payload = onRefreshReadiness ? await onRefreshReadiness() : await getSetupHostReadiness();
    applyReadiness(payload);
  }

  useEffect(() => {
    let mounted = true;
    if (readiness && !readinessSynced) {
      syncFormFromReadiness(readiness);
    } else if (!readiness && !readinessSynced) {
      getSetupHostReadiness()
        .then((payload) => {
          if (mounted) {
            applyReadiness(payload);
          }
        })
        .catch((err) => {
          if (mounted) setError(String(err.message || err));
        });
    }
    return () => {
      mounted = false;
    };
  }, [readiness, readinessSynced]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function runAction(action) {
    setBusyAction(action);
    setNotice("");
    setError("");
    try {
      if (action === "continue") {
        await saveIdentity({ quiet: true });
      }
      const actionForm = {
        ...form,
        core_base_url: normalizedCoreBaseUrl(form.core_base_url) || form.core_base_url,
      };
      const payload = await runSetupHostReadinessAction(action, actionForm);
      if (payload.readiness) {
        applyReadiness(payload.readiness);
      } else {
        await refresh();
      }
      if (!payload.accepted) {
        setError(payload.message || "setup_host_action_failed");
      } else {
        setNotice(payload.message || "Action completed.");
        if (action === "continue") {
          onContinue?.();
        }
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusyAction("");
    }
  }

  async function saveIdentity({ quiet = false } = {}) {
    const nodeName = form.node_name.trim();
    if (!nodeName) {
      throw new Error("node_name_required");
    }
    const identityPayload = {
      node_name: nodeName,
      protocol_version: form.protocol_version || "1.0",
      node_nonce: form.node_nonce || generateNodeNonce(),
      requested_node_id: optionalField(form.requested_node_id),
      hostname: optionalField(form.hostname),
      api_base_url: optionalField(form.api_base_url),
      ui_endpoint: optionalField(form.ui_endpoint),
    };
    const saved = await saveNodeIdentity(identityPayload);
    setForm((current) => ({
      ...current,
      protocol_version: saved.protocol_version || identityPayload.protocol_version,
      node_nonce: saved.node_nonce || identityPayload.node_nonce,
      requested_node_id: saved.requested_node_id || "",
      hostname: saved.hostname || current.hostname,
      api_base_url: saved.api_base_url || current.api_base_url,
      ui_endpoint: saved.ui_endpoint || current.ui_endpoint,
    }));
    await refresh();
    if (!quiet) {
      setNotice("Node identity saved.");
      setError("");
    }
    return saved;
  }

  async function handleSaveIdentity() {
    setBusyAction("save-node-identity");
    setNotice("");
    setError("");
    try {
      await saveIdentity();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusyAction("");
    }
  }

  function openEnrollmentPage() {
    const url = enrollmentPageUrl(form, activeReadiness);
    if (!url) {
      setError("core_url_required");
      return;
    }
    window.open(url, "_blank", "noopener,noreferrer");
    setNotice("Core Supervisor enrollment page opened. Copy the token there, then paste it here.");
    setError("");
  }

  async function copyEnrollmentTokenCommand() {
    const url = enrollmentTokenApiUrl(form, activeReadiness);
    if (!url) {
      setError("core_url_required");
      return;
    }
    const body = JSON.stringify({ supervisor_id: form.supervisor_id || defaultSupervisorId(activeReadiness) });
    const command = `curl -fsS -X POST ${url} -H "Content-Type: application/json" -H "X-Admin-Token: <admin-token>" -d '${body}'`;
    try {
      await navigator.clipboard.writeText(command);
      setNotice("Core enrollment token command copied.");
      setError("");
    } catch {
      window.prompt("Copy Core enrollment token command", command);
    }
  }

  const enrollmentUrl = enrollmentPageUrl(form, activeReadiness);
  const enrollmentWarning = form.lifecycle_mode === "joined_supervisor" ? coreUrlWarning(form, activeReadiness) : "";
  const checkById = new Map((activeReadiness?.checks || []).map((check) => [check.id, check]));
  const supportedActions = new Set(activeReadiness?.supported_actions || []);

  return (
    <article className="card stack">
      <div className="section-heading">
        <h2>Host Setup</h2>
        <span className={`status-pill status-pill-${activeReadiness?.ok ? "success" : "warning"}`}>
          {activeReadiness?.ok ? "ready" : "needs attention"}
        </span>
      </div>
      {notice ? <div className="callout callout-success">{notice}</div> : null}
      {error ? <div className="callout callout-danger">{error}</div> : null}

      <div className="form-grid">
        <label className="field">
          <span className="field-label">Node display name</span>
          <input className="field-input" value={form.node_name} onChange={(event) => update("node_name", event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Node type</span>
          <input className="field-input" value={form.node_type} readOnly />
        </label>
        <label className="field">
          <span className="field-label">Requested node ID</span>
          <input className="field-input" value={form.requested_node_id} onChange={(event) => update("requested_node_id", event.target.value)} placeholder="optional" />
        </label>
        <label className="field">
          <span className="field-label">Hostname</span>
          <input className="field-input" value={form.hostname} onChange={(event) => update("hostname", event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">LAN identity</span>
          <input className="field-input" value={form.lan_host} readOnly />
        </label>
        <label className="field">
          <span className="field-label">Final API URL</span>
          <input className="field-input" value={form.api_base_url} onChange={(event) => update("api_base_url", event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Final UI URL</span>
          <input className="field-input" value={form.ui_endpoint} onChange={(event) => update("ui_endpoint", event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Node nonce</span>
          <input className="field-input" value={form.node_nonce} onChange={(event) => update("node_nonce", event.target.value)} placeholder="generated on save" />
        </label>
        <label className="field">
          <span className="field-label">Setup mode</span>
          <select className="field-input" value={form.setup_mode} onChange={(event) => update("setup_mode", event.target.value)}>
            {setupModes.map((mode) => (
              <option key={mode.value} value={mode.value}>{mode.label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Lifecycle mode</span>
          <select className="field-input" value={form.lifecycle_mode} onChange={(event) => update("lifecycle_mode", event.target.value)}>
            {lifecycleModes.map((mode) => (
              <option key={mode.value} value={mode.value}>{mode.label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Core URL</span>
          <input className="field-input" value={form.core_base_url} onChange={(event) => update("core_base_url", event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Supervisor ID</span>
          <input
            className="field-input"
            value={form.supervisor_id}
            onChange={(event) => update("supervisor_id", event.target.value)}
            placeholder={defaultSupervisorId(activeReadiness)}
          />
        </label>
      </div>

      <div className="form-actions">
        <button className="btn btn-secondary" type="button" onClick={handleSaveIdentity} disabled={busyAction !== "" || !form.node_name.trim()}>
          {busyAction === "save-node-identity" ? "Saving..." : "Save node identity"}
        </button>
      </div>

      {form.lifecycle_mode === "joined_supervisor" ? (
        <div className="stack">
          {enrollmentWarning ? <div className="callout callout-warning">{enrollmentWarning}</div> : null}
          {enrollmentUrl ? (
            <div className="fact-grid">
              <span className="fact-grid-label">Core enrollment URL</span>
              <span className="fact-grid-value">{enrollmentUrl}</span>
            </div>
          ) : null}
          <div className="form-actions">
            <label className="field">
              <span className="field-label">Enrollment token</span>
              <input className="field-input" value={form.enrollment_token} onChange={(event) => update("enrollment_token", event.target.value)} />
            </label>
            <button className="btn btn-secondary" type="button" onClick={openEnrollmentPage} disabled={!form.core_base_url}>
              Open Core enrollment
            </button>
            <button className="btn btn-ghost" type="button" onClick={copyEnrollmentTokenCommand} disabled={!form.core_base_url && !activeReadiness?.enrollment_token_url}>
              Copy API command
            </button>
          </div>
        </div>
      ) : null}

      <section className="stack">
        <div className="section-heading">
          <h3>Default assets</h3>
          <span className="status-pill status-pill-neutral">Step 1</span>
        </div>
        <div className="fact-grid">
          {defaultAssetChecks.map((asset) => {
            const check = checkById.get(asset.id);
            const status = check?.status || "unknown";
            const statusClass = status === "pass" ? "success" : status === "fail" ? "danger" : "warning";
            return (
              <div className="fact-grid-item stack" key={asset.id}>
                <span className="fact-grid-label">{asset.label}</span>
                <span className="fact-grid-value">
                  <span className={`status-pill status-pill-${statusClass}`}>{status}</span>
                  <span>{check?.message || "Not checked yet."}</span>
                </span>
                <button
                  className="btn btn-ghost"
                  type="button"
                  onClick={() => runAction(asset.action)}
                  disabled={busyAction !== "" || !supportedActions.has(asset.action)}
                >
                  {busyAction === asset.action ? "Working..." : asset.button}
                </button>
              </div>
            );
          })}
        </div>
      </section>

      <section className="stack">
        <div className="section-heading">
          <h3>Recovery actions</h3>
          <span className="status-pill status-pill-neutral">Step 2</span>
        </div>
        <div className="form-actions">
          {recoveryActions.map((item) => (
            <button
              className="btn btn-ghost"
              key={item.action}
              type="button"
              onClick={() => runAction(item.action)}
              disabled={busyAction !== "" || !supportedActions.has(item.action)}
            >
              {busyAction === item.action ? "Working..." : item.label}
            </button>
          ))}
        </div>
      </section>

      <div className="form-actions">
        <button className="btn btn-ghost" type="button" onClick={() => runAction("prepare-runtime-dirs")} disabled={busyAction !== ""}>
          Runtime dirs
        </button>
        <button className="btn btn-ghost" type="button" onClick={() => runAction("check-cuda")} disabled={busyAction !== ""}>
          Check CUDA
        </button>
        <button className="btn btn-ghost" type="button" onClick={() => runAction("install-host-alias")} disabled={busyAction !== ""}>
          Add HexeVoice alias
        </button>
        {form.lifecycle_mode === "standalone_supervisor" ? (
          <button className="btn btn-secondary" type="button" onClick={() => runAction("install-standalone-supervisor")} disabled={busyAction !== ""}>
            Install Supervisor
          </button>
        ) : null}
        {form.lifecycle_mode === "joined_supervisor" ? (
          <button className="btn btn-secondary" type="button" onClick={() => runAction("install-joined-supervisor")} disabled={busyAction !== ""}>
            Join Supervisor
          </button>
        ) : null}
        <button className="btn btn-primary" type="button" onClick={() => runAction("continue")} disabled={busyAction !== ""}>
          {busyAction === "continue" ? "Saving..." : "Continue"}
        </button>
      </div>

      {activeReadiness?.blockers?.length ? <div className="callout callout-danger">Blockers: {activeReadiness.blockers.join(", ")}</div> : null}
      {activeReadiness?.warnings?.length ? <div className="callout callout-warning">Warnings: {activeReadiness.warnings.join(", ")}</div> : null}
    </article>
  );
}
